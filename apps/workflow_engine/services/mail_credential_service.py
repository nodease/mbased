from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.audit_log import (
    ActorType,
    AuditCategory,
    AuditLog,
    AuditStatus,
)
from apps.shared.audit.context import get_current_metadata
from apps.shared.db.models.mail_credential import (
    MAIL_CREDENTIAL_ACTIVE,
    MAIL_CREDENTIAL_REVOKED,
    MailCredential,
)
from apps.shared.services.credential_encryption import (
    CredentialEncryptionError,
    EncryptedSecretEnvelope,
    get_credential_encryption_service,
)
from apps.shared.domain.mail_oauth import GmailOAuthSecret, MailOAuthSecretError
from apps.shared.services.egress_guard import (
    EgressGuardError,
    ensure_network_target_allowed,
)
from apps.shared.services.permission_audit import record_resource_permission_denied
from apps.shared.services.permissions import (
    get_effective_mail_credential_auth_state,
    has_mail_credential_permission,
)


class MailCredentialRuntimeError(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ResolvedMailCredential:
    credential_id: uuid.UUID
    email_address: str
    imap_host: str
    imap_port: int
    resolved_ip: str
    use_ssl: bool
    secret: str = field(repr=False)
    provider: str = "custom"
    auth_type: str = "app_password"


class MailCredentialResolver:
    @staticmethod
    def refresh_oauth_serialized(
        db: Session,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        credential_id: uuid.UUID,
        refresh: Callable[[str], Any],
    ) -> Any:
        """Use a short DB lease so token HTTP does not hold a row lock."""
        lease_owner = uuid.uuid4().hex
        encryption = get_credential_encryption_service()
        try:
            row = (
                db.query(MailCredential)
                .filter(
                    MailCredential.id == credential_id,
                    MailCredential.organization_id == organization_id,
                    MailCredential.status == MAIL_CREDENTIAL_ACTIVE,
                    MailCredential.provider == "gmail",
                    MailCredential.auth_type == "oauth2",
                )
                .with_for_update()
                .first()
            )
            if row is None or not has_mail_credential_permission(
                db,
                user_id,
                credential_id,
                "use",
                organization_id=organization_id,
            ):
                raise MailCredentialRuntimeError("mail.credential_not_available")
            now = datetime.now(timezone.utc)
            if (
                row.oauth_refresh_lease_owner_hash is not None
                and row.oauth_refresh_lease_expires_at is not None
                and row.oauth_refresh_lease_expires_at > now
            ):
                raise MailCredentialRuntimeError("mail.oauth_refresh_in_progress")
            current_secret = encryption.decrypt(
                EncryptedSecretEnvelope(
                    ciphertext=row.encrypted_secret,
                    key_version=row.encryption_key_version,
                    algorithm=row.encryption_algorithm,
                )
            )
            row.oauth_refresh_lease_owner_hash = lease_owner
            row.oauth_refresh_lease_expires_at = now + timedelta(seconds=15)
            db.commit()
        except MailCredentialRuntimeError:
            db.rollback()
            raise
        except (CredentialEncryptionError, SQLAlchemyError):
            db.rollback()
            raise MailCredentialRuntimeError("mail.oauth_refresh_failed") from None

        try:
            access_token = refresh(current_secret)
        except Exception as exc:
            reason_code = getattr(exc, "reason_code", None)
            MailCredentialResolver._finalize_oauth_refresh_failure(
                db,
                organization_id=organization_id,
                credential_id=credential_id,
                lease_owner=lease_owner,
                user_id=user_id,
                reauthorization_required=(
                    reason_code == "mail.oauth_reauthorization_required"
                ),
            )
            if isinstance(reason_code, str) and reason_code.startswith("mail."):
                raise
            raise MailCredentialRuntimeError(
                "mail.oauth_token_exchange_failed"
            ) from None

        rotated_payload = getattr(access_token, "rotated_secret_payload", None)
        try:
            envelope = None
            if rotated_payload is not None:
                GmailOAuthSecret.parse(rotated_payload)
                envelope = encryption.encrypt(rotated_payload)
            row = (
                db.query(MailCredential)
                .filter(
                    MailCredential.id == credential_id,
                    MailCredential.organization_id == organization_id,
                )
                .with_for_update()
                .first()
            )
            if (
                row is None
                or row.oauth_refresh_lease_owner_hash != lease_owner
                or row.status != MAIL_CREDENTIAL_ACTIVE
            ):
                db.rollback()
                raise MailCredentialRuntimeError("mail.oauth_refresh_stale")
            if envelope is not None:
                row.encrypted_secret = envelope.ciphertext
                row.encryption_key_version = envelope.key_version
                row.encryption_algorithm = envelope.algorithm
                row.updated_at = datetime.now(timezone.utc)
                db.add(
                    MailCredentialResolver._oauth_audit_log(
                        action=AuditAction.MAIL_CREDENTIAL_UPDATE,
                        user_id=user_id,
                        organization_id=organization_id,
                        credential_id=row.id,
                        metadata={"rotation": "oauth_refresh"},
                    )
                )
            row.oauth_refresh_lease_owner_hash = None
            row.oauth_refresh_lease_expires_at = None
            db.commit()
            return access_token
        except MailCredentialRuntimeError:
            raise
        except MailOAuthSecretError as exc:
            db.rollback()
            raise MailCredentialRuntimeError(exc.reason_code) from None
        except (CredentialEncryptionError, SQLAlchemyError):
            db.rollback()
            raise MailCredentialRuntimeError("mail.oauth_refresh_failed") from None

    @staticmethod
    def _finalize_oauth_refresh_failure(
        db: Session,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        credential_id: uuid.UUID,
        lease_owner: str,
        reauthorization_required: bool,
    ) -> None:
        try:
            row = (
                db.query(MailCredential)
                .filter(
                    MailCredential.id == credential_id,
                    MailCredential.organization_id == organization_id,
                )
                .with_for_update()
                .first()
            )
            if row is None or row.oauth_refresh_lease_owner_hash != lease_owner:
                db.rollback()
                return
            row.oauth_refresh_lease_owner_hash = None
            row.oauth_refresh_lease_expires_at = None
            if reauthorization_required and row.status == MAIL_CREDENTIAL_ACTIVE:
                now = datetime.now(timezone.utc)
                row.status = MAIL_CREDENTIAL_REVOKED
                row.revoked_at = now
                row.updated_at = now
                db.add(
                    MailCredentialResolver._oauth_audit_log(
                        action=AuditAction.MAIL_CREDENTIAL_REVOKE,
                        user_id=user_id,
                        organization_id=organization_id,
                        credential_id=row.id,
                        metadata={"reason": "oauth_reauthorization_required"},
                    )
                )
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise MailCredentialRuntimeError("mail.oauth_refresh_failed") from None

    @staticmethod
    def revalidate_use(
        db: Session,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        credential_id: uuid.UUID,
    ) -> None:
        active = (
            db.query(MailCredential.id)
            .filter(
                MailCredential.id == credential_id,
                MailCredential.organization_id == organization_id,
                MailCredential.status == MAIL_CREDENTIAL_ACTIVE,
            )
            .scalar()
        )
        if active is None or not has_mail_credential_permission(
            db,
            user_id,
            credential_id,
            "use",
            organization_id=organization_id,
        ):
            raise MailCredentialRuntimeError("mail.credential_not_available")

    @staticmethod
    def resolve(
        db: Session,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        credential_id: uuid.UUID,
    ) -> ResolvedMailCredential:
        credential = (
            db.query(MailCredential)
            .filter(
                MailCredential.id == credential_id,
                MailCredential.organization_id == organization_id,
            )
            .first()
        )
        if credential is None or credential.status != MAIL_CREDENTIAL_ACTIVE:
            raise MailCredentialRuntimeError("mail.credential_not_available")

        if not has_mail_credential_permission(
            db,
            user_id,
            credential.id,
            "use",
            organization_id=organization_id,
        ):
            record_resource_permission_denied(
                user_id=user_id,
                resource_type="mail_credential",
                resource_id=credential.id,
                action="use",
                effective_auth_state=get_effective_mail_credential_auth_state(
                    db,
                    user_id,
                    credential.id,
                    organization_id=organization_id,
                ),
                organization_id=organization_id,
                metadata=get_current_metadata(),
            )
            raise MailCredentialRuntimeError("mail.credential_permission_denied")

        if credential.auth_type == "oauth2":
            if credential.provider != "gmail":
                raise MailCredentialRuntimeError("mail.oauth_provider_unsupported")
            canonical_host = "imap.gmail.com"
            safe_port = 993
            resolved_ip = ""
        else:
            expected_port = 993 if credential.use_ssl else 143
            if credential.imap_port != expected_port:
                raise MailCredentialRuntimeError("mail.egress_target_denied")

            try:
                canonical_host, safe_port, resolved_ip = ensure_network_target_allowed(
                    credential.imap_host,
                    credential.imap_port,
                    allowed_ports=frozenset({143, 993}),
                )
            except EgressGuardError as exc:
                raise MailCredentialRuntimeError("mail.egress_target_denied") from exc

        try:
            secret = get_credential_encryption_service().decrypt(
                EncryptedSecretEnvelope(
                    ciphertext=credential.encrypted_secret,
                    key_version=credential.encryption_key_version,
                    algorithm=credential.encryption_algorithm,
                )
            )
        except CredentialEncryptionError as exc:
            raise MailCredentialRuntimeError(
                "mail.credential_decryption_failed"
            ) from exc

        return ResolvedMailCredential(
            credential_id=credential.id,
            provider=credential.provider,
            auth_type=credential.auth_type,
            email_address=credential.email_address,
            imap_host=canonical_host,
            imap_port=safe_port,
            resolved_ip=resolved_ip,
            use_ssl=credential.use_ssl,
            secret=secret,
        )

    @staticmethod
    def _oauth_audit_log(
        *,
        action: str,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        credential_id: uuid.UUID,
        metadata: dict[str, str],
    ) -> AuditLog:
        return AuditLog(
            action=action,
            category=AuditCategory.ACTION,
            actor_id=user_id,
            actor_type=ActorType.USER,
            target_type="mail_credential",
            target_id=str(credential_id),
            before=None,
            after=None,
            status=AuditStatus.SUCCESS,
            audit_metadata={
                "organization_id": str(organization_id),
                "provider": "gmail",
                **metadata,
            },
        )
