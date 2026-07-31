from __future__ import annotations

import hashlib
import uuid
from typing import Any

from apps.shared.db.session import SessionLocal
from apps.shared.domain.mail_processing import (
    MailSourceReference,
    build_required_effect_contract_hash,
)
from apps.shared.services.credential_encryption import (
    get_credential_encryption_service,
)
from apps.workflow_engine.composition.mail import (
    build_gmail_mailbox_provider,
    build_google_oauth_token_service,
)
from apps.workflow_engine.adapters.mail_processing_repository import (
    SqlAlchemyMailProcessingRepository,
)
from apps.workflow_engine.application.mail_processing import (
    MailAcknowledgementPort,
    MailProcessingApplicationError,
    MailProcessingApplicationService,
)
from apps.workflow_engine.services.mail_credential_service import (
    MailCredentialResolver,
    ResolvedMailCredential,
)
from apps.workflow_engine.workflow.nodes.base.node import Node
from apps.workflow_engine.workflow.nodes.mail.entities import MailAcknowledgeNodeData
from apps.workflow_engine.workflow.nodes.mail.gmail_draft_node import (
    _required_selected_string,
)
from apps.workflow_engine.workflow.nodes.mail.mail_node import (
    _connect_imap_credential,
)

_ALLOWED_FOLDERS = frozenset({"INBOX", "SENT", "DRAFTS", "SPAM", "TRASH"})


class ImapAcknowledgementAdapter(MailAcknowledgementPort):
    def __init__(self, credential: ResolvedMailCredential) -> None:
        self._credential = credential

    def acknowledge(self, *, source_reference: MailSourceReference) -> None:
        if source_reference.folder not in _ALLOWED_FOLDERS:
            raise RuntimeError("mail.folder_select_failed")
        if source_reference.uid_validity is None or source_reference.uid is None:
            raise RuntimeError("mail.message_identity_invalid")
        mail = _connect_imap_credential(self._credential)
        try:
            status, _ = mail.select(source_reference.folder)
            if status != "OK":
                raise RuntimeError("mail.folder_select_failed")
            _code, values = mail.response("UIDVALIDITY")
            try:
                raw = values[0] if values else None
                current_uid_validity = int(
                    raw.decode("ascii") if isinstance(raw, bytes) else raw
                )
            except (TypeError, ValueError, IndexError, UnicodeDecodeError) as exc:
                raise RuntimeError("mail.message_identity_invalid") from exc
            if current_uid_validity != source_reference.uid_validity:
                raise RuntimeError("mail.message_identity_stale")
            status, _ = mail.uid("store", str(source_reference.uid), "+FLAGS", "\\Seen")
            if status != "OK":
                raise RuntimeError("mail.acknowledgement_failed")
        finally:
            try:
                mail.close()
            except Exception:
                pass
            try:
                mail.logout()
            except Exception:
                pass


class MailAcknowledgeNode(Node[MailAcknowledgeNodeData]):
    node_type = "mailAcknowledgeNode"

    def _run(self, inputs: dict[str, Any]) -> dict[str, str]:
        processing_ref = _required_selected_string(
            inputs,
            self.data.processing_ref_selector,
            reason_code="mail.processing_reference_required",
        )
        effect_refs = [
            _required_selected_string(
                inputs, selector, reason_code="mail.effect_reference_invalid"
            )
            for selector in self.data.required_effect_ref_selectors
        ]
        organization_id = self._required_context_uuid("organization_id")
        workflow_id = self._required_context_uuid("workflow_id")
        user_id = self._required_execution_subject_uuid()
        db, should_close = self._borrow_db_session()
        try:
            service = self._processing_service(db)

            def acknowledgement_factory(credential_id: uuid.UUID):
                credential = MailCredentialResolver.resolve(
                    db,
                    user_id=user_id,
                    organization_id=organization_id,
                    credential_id=credential_id,
                )
                injected_factory = self.execution_context.get(
                    "mail_acknowledgement_factory"
                )
                if callable(injected_factory):
                    return injected_factory(credential)
                return self._default_acknowledgement(
                    credential,
                    db=db,
                    user_id=user_id,
                    organization_id=organization_id,
                )

            return service.acknowledge_message(
                processing_ref=processing_ref,
                effect_refs=effect_refs,
                organization_id=organization_id,
                workflow_id=workflow_id,
                deployment_id=self._optional_context_uuid("deployment_id"),
                acknowledgement_factory=acknowledgement_factory,
                required_effect_contract_hash=self._required_effect_contract_hash(),
                lease_owner_hash=self._lease_owner_hash(),
                authorization_guard=lambda credential_id: (
                    MailCredentialResolver.revalidate_use(
                        db,
                        user_id=user_id,
                        organization_id=organization_id,
                        credential_id=credential_id,
                    )
                ),
            )
        except MailProcessingApplicationError as exc:
            raise RuntimeError(exc.reason_code) from exc
        finally:
            if should_close:
                db.close()

    def _default_acknowledgement(
        self,
        credential: ResolvedMailCredential,
        *,
        db,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> MailAcknowledgementPort:
        if credential.auth_type != "oauth2":
            return ImapAcknowledgementAdapter(credential)
        if credential.provider != "gmail":
            raise RuntimeError("mail.oauth_provider_unsupported")
        token_service = self.execution_context.get("google_oauth_token_service")
        if token_service is None:
            token_service = build_google_oauth_token_service()
        access_token = MailCredentialResolver.refresh_oauth_serialized(
            db,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential.credential_id,
            refresh=token_service.refresh,
        )
        provider_factory = self.execution_context.get("gmail_mailbox_provider_factory")
        if callable(provider_factory):
            return provider_factory(access_token.value)
        return build_gmail_mailbox_provider(access_token=access_token.value)

    def _processing_service(self, db) -> MailProcessingApplicationService:
        factory = self.execution_context.get("mail_processing_service_factory")
        if callable(factory):
            return factory(db)
        return MailProcessingApplicationService(
            repository=SqlAlchemyMailProcessingRepository(db),
            encryption=get_credential_encryption_service(),
        )

    def _required_effect_contract_hash(self) -> str:
        deployment_value = self.execution_context.get("deployment_id")
        try:
            deployment_id = (
                uuid.UUID(str(deployment_value))
                if deployment_value is not None
                else None
            )
            return build_required_effect_contract_hash(
                processing_selector=self.data.processing_ref_selector,
                effect_selectors=self.data.required_effect_ref_selectors,
                deployment_id=deployment_id,
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError("mail.effect_contract_invalid") from exc

    def _lease_owner_hash(self) -> str:
        identity = "\x1f".join(
            (
                str(self.execution_context.get("workflow_task_id") or ""),
                str(self.execution_context.get("workflow_run_id") or ""),
                self.id,
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def _required_context_uuid(self, key: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(self.execution_context.get(key)))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"mail.{key}_required") from exc

    def _optional_context_uuid(self, key: str) -> uuid.UUID | None:
        value = self.execution_context.get(key)
        if value is None:
            return None
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"mail.{key}_invalid") from exc

    def _required_execution_subject_uuid(self) -> uuid.UUID:
        subject = self.execution_context.get("execution_subject")
        if not isinstance(subject, dict):
            raise RuntimeError("mail.execution_subject_required")
        subject_type = subject.get("subject_type") or subject.get("type") or "user"
        subject_id = subject.get("subject_id") or subject.get("id")
        if subject_type != "user":
            raise RuntimeError("mail.execution_subject_required")
        try:
            return uuid.UUID(str(subject_id))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("mail.execution_subject_required") from exc

    def _borrow_db_session(self):
        factory = self.execution_context.get("db_session_factory")
        if callable(factory):
            return factory(), True
        legacy_session = self.execution_context.get("db")
        if legacy_session is not None:
            return legacy_session, False
        return SessionLocal(), True
