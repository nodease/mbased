from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol, Sequence

from apps.shared.domain.mail_processing import (
    MailDraftEffectStatus,
    MailProcessingStatus,
    MailSourceReference,
    build_draft_operation_key_hash,
    build_message_identity_hash,
    digest_reply_body,
    parse_opaque_reference,
)
from apps.shared.services.credential_encryption import (
    CredentialEncryptionService,
    EncryptedSecretEnvelope,
)
from apps.shared.services.mail_observability import MailProcessingObservability


class MailProcessingApplicationError(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


class GmailDraftRejectedBeforeEffect(MailProcessingApplicationError):
    pass


class GmailDraftOutcomeUnknown(MailProcessingApplicationError):
    pass


@dataclass(frozen=True)
class ProcessingRegistration:
    organization_id: uuid.UUID
    workflow_id: uuid.UUID
    deployment_id: uuid.UUID | None
    source_node_id: str
    credential_id: uuid.UUID
    provider: str
    source: MailSourceReference


@dataclass(frozen=True)
class ProtectedReference:
    ciphertext: str
    key_version: str
    algorithm: str


@dataclass(frozen=True)
class DraftAdmission:
    effect_id: uuid.UUID
    status: str
    acquired: bool = False
    provider_reference: ProtectedReference | None = None


@dataclass(frozen=True)
class AcknowledgementAdmission:
    status: str
    acquired: bool


@dataclass(frozen=True)
class GmailSourceMessage:
    message_id: str
    thread_id: str
    rfc_message_id: str
    references: tuple[str, ...]
    reply_to: str
    subject: str


@dataclass(frozen=True)
class GmailDraftRequest:
    source: GmailSourceMessage
    reply_body: str


@dataclass(frozen=True)
class GmailDraftCreated:
    provider_draft_id: str


class MailProcessingRepository(Protocol):
    def register_message(
        self,
        *,
        registration: ProcessingRegistration,
        message_identity_hash: str,
        source_reference: ProtectedReference,
    ) -> uuid.UUID: ...

    def load_source_reference(
        self,
        *,
        processing_id: uuid.UUID,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        credential_id: uuid.UUID,
    ) -> ProtectedReference: ...

    def get_processing_credential_id(
        self,
        *,
        processing_id: uuid.UUID,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        deployment_id: uuid.UUID | None,
    ) -> uuid.UUID: ...

    def claim_draft_effect(
        self,
        *,
        processing_id: uuid.UUID,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        deployment_id: uuid.UUID | None,
        node_id: str,
        operation_key_hash: str,
        input_digest: str,
        lease_owner_hash: str,
        lease_expires_at: datetime,
        max_attempts: int,
    ) -> DraftAdmission: ...

    def record_draft_succeeded(
        self, *, effect_id: uuid.UUID, draft_reference: ProtectedReference
    ) -> None: ...

    def record_draft_failed_before_effect(
        self, *, effect_id: uuid.UUID, safe_reason_code: str
    ) -> None: ...

    def record_draft_outcome_unknown(
        self, *, effect_id: uuid.UUID, safe_reason_code: str
    ) -> None: ...

    def require_effects_succeeded(
        self,
        *,
        processing_id: uuid.UUID,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        deployment_id: uuid.UUID | None,
        effect_ids: Sequence[uuid.UUID],
        required_effect_contract_hash: str,
        lease_owner_hash: str,
        lease_expires_at: datetime,
    ) -> AcknowledgementAdmission: ...

    def record_acknowledgement_succeeded(self, *, processing_id: uuid.UUID) -> None: ...

    def record_acknowledgement_pending(
        self, *, processing_id: uuid.UUID, safe_reason_code: str
    ) -> None: ...


class GmailDraftProviderPort(Protocol):
    def resolve_source_message(
        self, *, source_reference: MailSourceReference
    ) -> GmailSourceMessage: ...

    def create_reply_draft(self, request: GmailDraftRequest) -> GmailDraftCreated: ...


class MailAcknowledgementPort(Protocol):
    def acknowledge(self, *, source_reference: MailSourceReference) -> None: ...


class MailProcessingApplicationService:
    def __init__(
        self,
        *,
        repository: MailProcessingRepository,
        encryption: CredentialEncryptionService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._encryption = encryption
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def register_message(self, registration: ProcessingRegistration) -> str:
        identity_hash = build_message_identity_hash(
            organization_id=registration.organization_id,
            workflow_id=registration.workflow_id,
            source_node_id=registration.source_node_id,
            credential_id=registration.credential_id,
            provider=registration.provider,
            source=registration.source,
        )
        protected = self._protect(registration.source.to_json())
        processing_id = self._repository.register_message(
            registration=registration,
            message_identity_hash=identity_hash,
            source_reference=protected,
        )
        MailProcessingObservability.record("registration", "resolved")
        return str(processing_id)

    def create_gmail_reply_draft(
        self,
        *,
        processing_ref: str,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        deployment_id: uuid.UUID | None,
        credential_id: uuid.UUID,
        node_id: str,
        reply_body: str,
        lease_owner_hash: str,
        lease_expires_at: datetime | None,
        provider_factory: Callable[[], GmailDraftProviderPort],
        authorization_guard: Callable[[], None],
        max_attempts: int = 3,
    ) -> dict[str, str]:
        processing_id = parse_opaque_reference(
            processing_ref, reason_code="mail.processing_reference_invalid"
        )
        operation_key_hash = build_draft_operation_key_hash(
            processing_id=processing_id, node_id=node_id
        )
        input_digest = digest_reply_body(reply_body)
        admission = self._repository.claim_draft_effect(
            processing_id=processing_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            deployment_id=deployment_id,
            node_id=node_id,
            operation_key_hash=operation_key_hash,
            input_digest=input_digest,
            lease_owner_hash=lease_owner_hash,
            lease_expires_at=lease_expires_at or self._clock() + timedelta(minutes=5),
            max_attempts=max_attempts,
        )
        MailProcessingObservability.record(
            "draft_admission", _admission_metric_outcome(admission)
        )
        if admission.status == MailDraftEffectStatus.SUCCEEDED.value:
            return {
                "status": "succeeded",
                "draft_ref": str(admission.effect_id),
                "processing_ref": str(processing_id),
            }
        if admission.status == MailDraftEffectStatus.EXHAUSTED.value:
            raise MailProcessingApplicationError("mail.draft_attempts_exhausted")
        if admission.status == MailDraftEffectStatus.FAILED_BEFORE_EFFECT.value:
            raise MailProcessingApplicationError("mail.draft_retry_not_due")
        if admission.status != MailDraftEffectStatus.CLAIMED.value:
            raise MailProcessingApplicationError("mail.draft_effect_not_admitted")
        if not admission.acquired:
            raise MailProcessingApplicationError("mail.draft_effect_in_progress")

        protected_source = self._repository.load_source_reference(
            processing_id=processing_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            credential_id=credential_id,
        )
        source_reference = self._source_from_protected(protected_source)
        self._authorize_draft_provider_call(
            authorization_guard=authorization_guard,
            effect_id=admission.effect_id,
        )
        try:
            provider = provider_factory()
            source = provider.resolve_source_message(source_reference=source_reference)
        except GmailDraftRejectedBeforeEffect as exc:
            self._repository.record_draft_failed_before_effect(
                effect_id=admission.effect_id, safe_reason_code=exc.reason_code
            )
            MailProcessingObservability.record("draft", "failed_before_effect")
            raise
        except Exception as exc:
            reason_code = getattr(exc, "reason_code", None)
            safe_reason_code = (
                reason_code
                if isinstance(reason_code, str) and reason_code.startswith("mail.")
                else "mail.source_resolution_failed"
            )
            self._repository.record_draft_failed_before_effect(
                effect_id=admission.effect_id,
                safe_reason_code=safe_reason_code,
            )
            MailProcessingObservability.record("draft", "failed_before_effect")
            raise GmailDraftRejectedBeforeEffect(safe_reason_code) from None

        self._authorize_draft_provider_call(
            authorization_guard=authorization_guard,
            effect_id=admission.effect_id,
        )

        try:
            created = provider.create_reply_draft(
                GmailDraftRequest(source=source, reply_body=reply_body)
            )
        except GmailDraftRejectedBeforeEffect as exc:
            self._repository.record_draft_failed_before_effect(
                effect_id=admission.effect_id, safe_reason_code=exc.reason_code
            )
            MailProcessingObservability.record("draft", "failed_before_effect")
            raise
        except GmailDraftOutcomeUnknown as exc:
            self._repository.record_draft_outcome_unknown(
                effect_id=admission.effect_id, safe_reason_code=exc.reason_code
            )
            MailProcessingObservability.record("draft", "outcome_unknown")
            raise
        except Exception:
            self._repository.record_draft_outcome_unknown(
                effect_id=admission.effect_id,
                safe_reason_code="mail.draft_outcome_unknown",
            )
            MailProcessingObservability.record("draft", "outcome_unknown")
            raise GmailDraftOutcomeUnknown("mail.draft_outcome_unknown") from None

        if not created.provider_draft_id:
            self._repository.record_draft_outcome_unknown(
                effect_id=admission.effect_id,
                safe_reason_code="mail.draft_response_invalid",
            )
            MailProcessingObservability.record("draft", "outcome_unknown")
            raise GmailDraftOutcomeUnknown("mail.draft_response_invalid")
        protected_draft = self._protect(created.provider_draft_id)
        try:
            self._repository.record_draft_succeeded(
                effect_id=admission.effect_id, draft_reference=protected_draft
            )
        except Exception:
            MailProcessingObservability.record("draft", "outcome_unknown")
            raise GmailDraftOutcomeUnknown("mail.draft_outcome_unknown") from None
        MailProcessingObservability.record("draft", "succeeded")
        return {
            "status": "succeeded",
            "draft_ref": str(admission.effect_id),
            "processing_ref": str(processing_id),
        }

    def acknowledge_message(
        self,
        *,
        processing_ref: str,
        effect_refs: Sequence[str],
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        deployment_id: uuid.UUID | None,
        acknowledgement_factory: Callable[[uuid.UUID], MailAcknowledgementPort],
        required_effect_contract_hash: str,
        authorization_guard: Callable[[uuid.UUID], None],
        lease_owner_hash: str,
        lease_expires_at: datetime | None = None,
    ) -> dict[str, str]:
        processing_id = parse_opaque_reference(
            processing_ref, reason_code="mail.processing_reference_invalid"
        )
        if not effect_refs:
            raise MailProcessingApplicationError("mail.required_effects_missing")
        effect_ids = tuple(
            parse_opaque_reference(ref, reason_code="mail.effect_reference_invalid")
            for ref in effect_refs
        )
        admission = self._repository.require_effects_succeeded(
            processing_id=processing_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            deployment_id=deployment_id,
            effect_ids=effect_ids,
            required_effect_contract_hash=required_effect_contract_hash,
            lease_owner_hash=lease_owner_hash,
            lease_expires_at=lease_expires_at
            or self._clock() + timedelta(minutes=5),
        )
        if admission.status == MailProcessingStatus.SUCCEEDED.value:
            MailProcessingObservability.record("ack", "duplicate")
            return {"status": "succeeded", "processing_ref": str(processing_id)}
        if not admission.acquired:
            raise MailProcessingApplicationError("mail.acknowledgement_in_progress")
        credential_id = self._repository.get_processing_credential_id(
            processing_id=processing_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            deployment_id=deployment_id,
        )
        protected_source = self._repository.load_source_reference(
            processing_id=processing_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            credential_id=credential_id,
        )
        source_reference = self._source_from_protected(protected_source)
        try:
            authorization_guard(credential_id)
            acknowledgement = acknowledgement_factory(credential_id)
            authorization_guard(credential_id)
            acknowledgement.acknowledge(source_reference=source_reference)
        except Exception:
            self._repository.record_acknowledgement_pending(
                processing_id=processing_id,
                safe_reason_code="mail.acknowledgement_failed",
            )
            MailProcessingObservability.record("ack", "pending")
            raise MailProcessingApplicationError(
                "mail.acknowledgement_failed"
            ) from None
        self._repository.record_acknowledgement_succeeded(processing_id=processing_id)
        MailProcessingObservability.record("ack", "succeeded")
        return {"status": "succeeded", "processing_ref": str(processing_id)}

    def processing_credential_id(
        self,
        *,
        processing_ref: str,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        deployment_id: uuid.UUID | None,
    ) -> uuid.UUID:
        processing_id = parse_opaque_reference(
            processing_ref, reason_code="mail.processing_reference_invalid"
        )
        return self._repository.get_processing_credential_id(
            processing_id=processing_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            deployment_id=deployment_id,
        )

    def _protect(self, value: str) -> ProtectedReference:
        envelope = self._encryption.encrypt(value)
        return ProtectedReference(
            ciphertext=envelope.ciphertext,
            key_version=envelope.key_version,
            algorithm=envelope.algorithm,
        )

    def _authorize_draft_provider_call(
        self,
        *,
        authorization_guard: Callable[[], None],
        effect_id: uuid.UUID,
    ) -> None:
        try:
            authorization_guard()
        except Exception:
            self._repository.record_draft_failed_before_effect(
                effect_id=effect_id,
                safe_reason_code="mail.credential_not_available",
            )
            MailProcessingObservability.record("draft", "failed_before_effect")
            raise GmailDraftRejectedBeforeEffect(
                "mail.credential_not_available"
            ) from None

    def _source_from_protected(
        self, reference: ProtectedReference
    ) -> MailSourceReference:
        raw = self._encryption.decrypt(
            EncryptedSecretEnvelope(
                ciphertext=reference.ciphertext,
                key_version=reference.key_version,
                algorithm=reference.algorithm,
            )
        )
        try:
            import json

            decoded = json.loads(raw)
        except (TypeError, ValueError):
            raise MailProcessingApplicationError(
                "mail.source_reference_invalid"
            ) from None
        if not isinstance(decoded, dict):
            raise MailProcessingApplicationError("mail.source_reference_invalid")
        try:
            return MailSourceReference.from_mapping(decoded)
        except ValueError:
            raise MailProcessingApplicationError(
                "mail.source_reference_invalid"
            ) from None


def _admission_metric_outcome(admission: DraftAdmission) -> str:
    if admission.status == MailDraftEffectStatus.EXHAUSTED.value:
        return "exhausted"
    if admission.status == MailDraftEffectStatus.FAILED_BEFORE_EFFECT.value:
        return "retry_not_due"
    if admission.status == MailDraftEffectStatus.SUCCEEDED.value:
        return "duplicate"
    if admission.status == MailDraftEffectStatus.CLAIMED.value:
        return "acquired" if admission.acquired else "in_progress"
    if admission.status == MailDraftEffectStatus.OUTCOME_UNKNOWN.value:
        return "outcome_unknown"
    return "pending"
