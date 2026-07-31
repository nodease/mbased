from __future__ import annotations

import hashlib
import uuid
from typing import Any

from apps.shared.db.session import SessionLocal
from apps.shared.services.credential_encryption import (
    get_credential_encryption_service,
)
from apps.workflow_engine.composition.mail import (
    build_gmail_draft_provider,
    build_google_oauth_token_service,
)
from apps.workflow_engine.adapters.mail_processing_repository import (
    SqlAlchemyMailProcessingRepository,
)
from apps.workflow_engine.application.mail_processing import (
    MailProcessingApplicationError,
    MailProcessingApplicationService,
)
from apps.workflow_engine.services.mail_credential_service import (
    MailCredentialResolver,
)
from apps.workflow_engine.workflow.nodes.base.node import Node
from apps.workflow_engine.workflow.nodes.mail.entities import GmailDraftNodeData


class GmailDraftNode(Node[GmailDraftNodeData]):
    node_type = "gmailDraftNode"

    def _run(self, inputs: dict[str, Any]) -> dict[str, str]:
        if self.data.credential_id is None:
            raise RuntimeError("mail.credential_reference_required")
        processing_ref = _required_selected_string(
            inputs,
            self.data.processing_ref_selector,
            reason_code="mail.processing_reference_required",
        )
        reply_body = _required_selected_string(
            inputs,
            self.data.reply_body_selector,
            reason_code="mail.reply_body_invalid",
        )
        organization_id = self._required_context_uuid("organization_id")
        workflow_id = self._required_context_uuid("workflow_id")
        user_id = self._required_execution_subject_uuid()
        db, should_close = self._borrow_db_session()
        try:
            service = self._processing_service(db)

            def provider_factory():
                credential = MailCredentialResolver.resolve(
                    db,
                    user_id=user_id,
                    organization_id=organization_id,
                    credential_id=self.data.credential_id,
                )
                if credential.provider != "gmail" or credential.auth_type != "oauth2":
                    raise RuntimeError("mail.gmail_oauth_credential_required")
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
                injected_factory = self.execution_context.get(
                    "gmail_draft_provider_factory"
                )
                if callable(injected_factory):
                    return injected_factory(
                        access_token.value,
                        credential.email_address,
                    )
                return build_gmail_draft_provider(
                    access_token=access_token.value,
                    mailbox_email=credential.email_address,
                )

            return service.create_gmail_reply_draft(
                processing_ref=processing_ref,
                organization_id=organization_id,
                workflow_id=workflow_id,
                deployment_id=self._optional_context_uuid("deployment_id"),
                credential_id=self.data.credential_id,
                node_id=self.id,
                reply_body=reply_body,
                lease_owner_hash=self._lease_owner_hash(),
                lease_expires_at=None,
                provider_factory=provider_factory,
                authorization_guard=lambda: MailCredentialResolver.revalidate_use(
                    db,
                    user_id=user_id,
                    organization_id=organization_id,
                    credential_id=self.data.credential_id,
                ),
            )
        except MailProcessingApplicationError as exc:
            raise RuntimeError(exc.reason_code) from exc
        finally:
            if should_close:
                db.close()

    def _processing_service(self, db) -> MailProcessingApplicationService:
        factory = self.execution_context.get("mail_processing_service_factory")
        if callable(factory):
            return factory(db)
        return MailProcessingApplicationService(
            repository=SqlAlchemyMailProcessingRepository(db),
            encryption=get_credential_encryption_service(),
        )

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


def _required_selected_string(
    inputs: dict[str, Any], selector: list[str], *, reason_code: str
) -> str:
    value: Any = inputs
    for key in selector:
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit():
            index = int(key)
            value = value[index] if 0 <= index < len(value) else None
        else:
            value = None
        if value is None:
            break
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(reason_code)
    return value
