from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.shared.db.models.mail_credential import (
    MAIL_CREDENTIAL_ACTIVE,
    MailCredential,
)
from apps.shared.db.models.mail_processing import (
    MailDraftEffect,
    MailMessageProcessing,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.domain.mail_processing import (
    MailDraftEffectStatus,
    MailProcessingStatus,
)
from apps.shared.services.app_lifecycle_admission import (
    lock_app_workflow_for_admission,
)
from apps.workflow_engine.application.mail_processing import (
    AcknowledgementAdmission,
    DraftAdmission,
    MailProcessingApplicationError,
    ProcessingRegistration,
    ProtectedReference,
)


class SqlAlchemyMailProcessingRepository:
    def __init__(
        self,
        db: Session,
        *,
        clock: Callable[[], datetime] | None = None,
        retry_delay: timedelta = timedelta(seconds=1),
    ) -> None:
        self._db = db
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._retry_delay = retry_delay

    def register_message(
        self,
        *,
        registration: ProcessingRegistration,
        message_identity_hash: str,
        source_reference: ProtectedReference,
    ) -> uuid.UUID:
        self._require_scope(registration)
        app_id = (
            self._db.query(Workflow.app_id)
            .filter(
                Workflow.id == registration.workflow_id,
                Workflow.organization_id == registration.organization_id,
            )
            .scalar()
        )
        if app_id is None or lock_app_workflow_for_admission(
            self._db,
            app_id=app_id,
            workflow_id=registration.workflow_id,
            organization_id=registration.organization_id,
        ) is None:
            self._db.rollback()
            raise MailProcessingApplicationError("mail.processing_not_available")
        processing_id = uuid.uuid4()
        now = self._clock()
        statement = (
            insert(MailMessageProcessing)
            .values(
                id=processing_id,
                organization_id=registration.organization_id,
                workflow_id=registration.workflow_id,
                deployment_id=registration.deployment_id,
                source_node_id=registration.source_node_id,
                credential_id=registration.credential_id,
                provider=registration.provider.lower(),
                message_identity_hash=message_identity_hash,
                encrypted_source_reference=source_reference.ciphertext,
                source_key_version=source_reference.key_version,
                source_algorithm=source_reference.algorithm,
                status=MailProcessingStatus.PENDING.value,
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                constraint="uq_mail_message_processings_logical_message"
            )
            .returning(MailMessageProcessing.id)
        )
        try:
            inserted = self._db.execute(statement).scalar_one_or_none()
            if inserted is None:
                inserted = (
                    self._db.query(MailMessageProcessing.id)
                    .filter(
                        MailMessageProcessing.organization_id
                        == registration.organization_id,
                        MailMessageProcessing.workflow_id == registration.workflow_id,
                        MailMessageProcessing.source_node_id
                        == registration.source_node_id,
                        MailMessageProcessing.credential_id
                        == registration.credential_id,
                        MailMessageProcessing.provider == registration.provider.lower(),
                        MailMessageProcessing.message_identity_hash
                        == message_identity_hash,
                    )
                    .scalar()
                )
            if inserted is None:
                raise MailProcessingApplicationError(
                    "mail.processing_registration_failed"
                )
            self._db.commit()
            return inserted
        except MailProcessingApplicationError:
            self._db.rollback()
            raise
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise MailProcessingApplicationError(
                "mail.processing_persistence_failed"
            ) from exc

    def load_source_reference(
        self,
        *,
        processing_id: uuid.UUID,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        credential_id: uuid.UUID,
    ) -> ProtectedReference:
        row = (
            self._db.query(MailMessageProcessing)
            .filter(
                MailMessageProcessing.id == processing_id,
                MailMessageProcessing.organization_id == organization_id,
                MailMessageProcessing.workflow_id == workflow_id,
                MailMessageProcessing.credential_id == credential_id,
            )
            .first()
        )
        if row is None:
            raise MailProcessingApplicationError("mail.processing_not_available")
        return ProtectedReference(
            ciphertext=row.encrypted_source_reference,
            key_version=row.source_key_version,
            algorithm=row.source_algorithm,
        )

    def get_processing_credential_id(
        self,
        *,
        processing_id: uuid.UUID,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        deployment_id: uuid.UUID | None,
    ) -> uuid.UUID:
        processing = (
            self._db.query(MailMessageProcessing)
            .filter(
                MailMessageProcessing.id == processing_id,
                MailMessageProcessing.organization_id == organization_id,
                MailMessageProcessing.workflow_id == workflow_id,
            )
            .first()
        )
        if processing is None or (
            processing.status != MailProcessingStatus.SUCCEEDED.value
            and processing.deployment_id != deployment_id
        ):
            raise MailProcessingApplicationError("mail.processing_not_available")
        return processing.credential_id

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
    ) -> DraftAdmission:
        if max_attempts < 1:
            raise MailProcessingApplicationError("mail.draft_retry_policy_invalid")
        try:
            processing = (
                self._db.query(MailMessageProcessing)
                .filter(
                    MailMessageProcessing.id == processing_id,
                    MailMessageProcessing.organization_id == organization_id,
                    MailMessageProcessing.workflow_id == workflow_id,
                )
                .with_for_update()
                .first()
            )
            if processing is None or processing.status in {
                MailProcessingStatus.FAILED.value,
                MailProcessingStatus.OUTCOME_UNKNOWN.value,
            }:
                raise MailProcessingApplicationError("mail.processing_not_available")

            effect = (
                self._db.query(MailDraftEffect)
                .filter(
                    MailDraftEffect.processing_id == processing_id,
                    MailDraftEffect.node_id == node_id,
                    MailDraftEffect.operation_key_hash == operation_key_hash,
                )
                .with_for_update()
                .first()
            )
            if processing.status == MailProcessingStatus.SUCCEEDED.value:
                if (
                    effect is not None
                    and effect.input_digest == input_digest
                    and effect.status == MailDraftEffectStatus.SUCCEEDED.value
                ):
                    self._db.commit()
                    return DraftAdmission(
                        effect_id=effect.id,
                        status=effect.status,
                        acquired=False,
                    )
                raise MailProcessingApplicationError("mail.processing_not_available")
            if processing.deployment_id != deployment_id:
                if (
                    processing.status == MailProcessingStatus.PENDING.value
                    and effect is None
                ):
                    processing.deployment_id = deployment_id
                else:
                    raise MailProcessingApplicationError(
                        "mail.processing_deployment_conflict"
                    )
            if effect is None:
                effect = MailDraftEffect(
                    processing_id=processing_id,
                    node_id=node_id,
                    operation_key_hash=operation_key_hash,
                    input_digest=input_digest,
                    status=MailDraftEffectStatus.PENDING.value,
                    attempt_count=0,
                )
                self._db.add(effect)
                self._db.flush()
            elif effect.input_digest != input_digest:
                raise MailProcessingApplicationError("mail.draft_input_conflict")

            if effect.status in {
                MailDraftEffectStatus.SUCCEEDED.value,
                MailDraftEffectStatus.OUTCOME_UNKNOWN.value,
            }:
                self._db.commit()
                return DraftAdmission(
                    effect_id=effect.id,
                    status=effect.status,
                    acquired=False,
                )

            now = self._clock()
            if effect.status == MailDraftEffectStatus.CLAIMED.value:
                if effect.lease_expires_at is None or effect.lease_expires_at > now:
                    self._db.commit()
                    return DraftAdmission(
                        effect_id=effect.id,
                        status=effect.status,
                        acquired=False,
                    )
                effect.status = MailDraftEffectStatus.OUTCOME_UNKNOWN.value
                effect.safe_reason_code = "mail.draft_claim_expired"
                effect.lease_owner_hash = None
                effect.lease_expires_at = None
                effect.completed_at = now
                processing.status = MailProcessingStatus.OUTCOME_UNKNOWN.value
                processing.safe_reason_code = "mail.draft_claim_expired"
                processing.completed_at = now
                self._db.commit()
                return DraftAdmission(
                    effect_id=effect.id,
                    status=effect.status,
                    acquired=False,
                )

            if (
                effect.status == MailDraftEffectStatus.FAILED_BEFORE_EFFECT.value
                and effect.next_attempt_at is not None
                and effect.next_attempt_at > now
            ):
                self._db.commit()
                return DraftAdmission(
                    effect_id=effect.id,
                    status=effect.status,
                    acquired=False,
                )
            if effect.attempt_count >= max_attempts:
                effect.status = MailDraftEffectStatus.EXHAUSTED.value
                effect.safe_reason_code = "mail.draft_attempts_exhausted"
                effect.next_attempt_at = None
                effect.completed_at = now
                processing.status = MailProcessingStatus.FAILED.value
                processing.safe_reason_code = "mail.draft_attempts_exhausted"
                processing.completed_at = now
                processing.lease_owner_hash = None
                processing.lease_expires_at = None
                self._db.commit()
                return DraftAdmission(
                    effect_id=effect.id,
                    status=effect.status,
                    acquired=False,
                )
            effect.status = MailDraftEffectStatus.CLAIMED.value
            effect.attempt_count += 1
            effect.lease_owner_hash = lease_owner_hash
            effect.lease_expires_at = lease_expires_at
            effect.safe_reason_code = None
            effect.next_attempt_at = None
            if processing.status == MailProcessingStatus.PENDING.value:
                processing.status = MailProcessingStatus.PROCESSING.value
                processing.attempt_count += 1
            self._db.commit()
            return DraftAdmission(
                effect_id=effect.id,
                status=effect.status,
                acquired=True,
            )
        except MailProcessingApplicationError:
            self._db.rollback()
            raise
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise MailProcessingApplicationError(
                "mail.processing_persistence_failed"
            ) from exc

    def record_draft_succeeded(
        self, *, effect_id: uuid.UUID, draft_reference: ProtectedReference
    ) -> None:
        effect, _processing = self._required_claimed_effect(effect_id)
        effect.status = MailDraftEffectStatus.SUCCEEDED.value
        effect.encrypted_draft_reference = draft_reference.ciphertext
        effect.draft_key_version = draft_reference.key_version
        effect.draft_algorithm = draft_reference.algorithm
        effect.next_attempt_at = None
        self._complete_effect(effect)

    def record_draft_failed_before_effect(
        self, *, effect_id: uuid.UUID, safe_reason_code: str
    ) -> None:
        effect, _processing = self._required_claimed_effect(effect_id)
        effect.status = MailDraftEffectStatus.FAILED_BEFORE_EFFECT.value
        effect.safe_reason_code = safe_reason_code
        effect.next_attempt_at = self._clock() + self._retry_delay
        self._complete_effect(effect, terminal=False)

    def record_draft_outcome_unknown(
        self, *, effect_id: uuid.UUID, safe_reason_code: str
    ) -> None:
        effect, processing = self._required_claimed_effect(effect_id)
        effect.status = MailDraftEffectStatus.OUTCOME_UNKNOWN.value
        effect.safe_reason_code = safe_reason_code
        effect.next_attempt_at = None
        processing.status = MailProcessingStatus.OUTCOME_UNKNOWN.value
        processing.safe_reason_code = safe_reason_code
        processing.completed_at = self._clock()
        self._complete_effect(effect)

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
    ) -> AcknowledgementAdmission:
        try:
            processing = (
                self._db.query(MailMessageProcessing)
                .filter(
                    MailMessageProcessing.id == processing_id,
                    MailMessageProcessing.organization_id == organization_id,
                    MailMessageProcessing.workflow_id == workflow_id,
                )
                .with_for_update()
                .first()
            )
            if processing is None:
                raise MailProcessingApplicationError("mail.processing_not_available")
            if processing.status == MailProcessingStatus.SUCCEEDED.value:
                self._db.commit()
                return AcknowledgementAdmission(
                    status=processing.status,
                    acquired=False,
                )
            if processing.deployment_id != deployment_id:
                raise MailProcessingApplicationError(
                    "mail.processing_deployment_conflict"
                )
            if processing.required_effect_contract_hash is None:
                processing.required_effect_contract_hash = required_effect_contract_hash
            elif (
                processing.required_effect_contract_hash
                != required_effect_contract_hash
            ):
                raise MailProcessingApplicationError("mail.effect_contract_mismatch")
            unique_ids = set(effect_ids)
            effects = (
                self._db.query(MailDraftEffect)
                .filter(
                    MailDraftEffect.processing_id == processing_id,
                    MailDraftEffect.id.in_(unique_ids),
                )
                .with_for_update()
                .all()
            )
            if len(effects) != len(unique_ids) or any(
                effect.status != MailDraftEffectStatus.SUCCEEDED.value
                for effect in effects
            ):
                raise MailProcessingApplicationError("mail.required_effects_incomplete")
            if processing.status not in {
                MailProcessingStatus.PROCESSING.value,
                MailProcessingStatus.ACK_PENDING.value,
            }:
                raise MailProcessingApplicationError("mail.processing_state_invalid")
            now = self._clock()
            if (
                processing.status == MailProcessingStatus.ACK_PENDING.value
                and processing.lease_owner_hash is not None
                and processing.lease_expires_at is not None
                and processing.lease_expires_at > now
            ):
                self._db.commit()
                return AcknowledgementAdmission(
                    status=processing.status,
                    acquired=False,
                )
            processing.status = MailProcessingStatus.ACK_PENDING.value
            processing.safe_reason_code = None
            processing.lease_owner_hash = lease_owner_hash
            processing.lease_expires_at = lease_expires_at
            processing.attempt_count += 1
            self._db.commit()
            return AcknowledgementAdmission(
                status=processing.status,
                acquired=True,
            )
        except MailProcessingApplicationError:
            self._db.rollback()
            raise
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise MailProcessingApplicationError(
                "mail.processing_persistence_failed"
            ) from exc

    def record_acknowledgement_succeeded(self, *, processing_id: uuid.UUID) -> None:
        try:
            processing = self._required_processing_for_update(processing_id)
            if processing.status == MailProcessingStatus.SUCCEEDED.value:
                self._db.commit()
                return
            if processing.status != MailProcessingStatus.ACK_PENDING.value:
                raise MailProcessingApplicationError("mail.processing_state_invalid")
            processing.status = MailProcessingStatus.SUCCEEDED.value
            processing.safe_reason_code = None
            processing.completed_at = self._clock()
            processing.lease_owner_hash = None
            processing.lease_expires_at = None
            self._db.commit()
        except MailProcessingApplicationError:
            self._db.rollback()
            raise
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise MailProcessingApplicationError(
                "mail.processing_persistence_failed"
            ) from exc

    def record_acknowledgement_pending(
        self, *, processing_id: uuid.UUID, safe_reason_code: str
    ) -> None:
        try:
            processing = self._required_processing_for_update(processing_id)
            if processing.status != MailProcessingStatus.ACK_PENDING.value:
                raise MailProcessingApplicationError("mail.processing_state_invalid")
            processing.safe_reason_code = safe_reason_code
            processing.lease_owner_hash = None
            processing.lease_expires_at = None
            self._db.commit()
        except MailProcessingApplicationError:
            self._db.rollback()
            raise
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise MailProcessingApplicationError(
                "mail.processing_persistence_failed"
            ) from exc

    def _require_scope(self, registration: ProcessingRegistration) -> None:
        workflow_exists = (
            self._db.query(Workflow.id)
            .filter(
                Workflow.id == registration.workflow_id,
                Workflow.organization_id == registration.organization_id,
            )
            .scalar()
        )
        credential_exists = (
            self._db.query(MailCredential.id)
            .filter(
                MailCredential.id == registration.credential_id,
                MailCredential.organization_id == registration.organization_id,
                MailCredential.status == MAIL_CREDENTIAL_ACTIVE,
            )
            .scalar()
        )
        if workflow_exists is None or credential_exists is None:
            raise MailProcessingApplicationError("mail.processing_scope_invalid")

    def _required_claimed_effect(
        self,
        effect_id: uuid.UUID,
    ) -> tuple[MailDraftEffect, MailMessageProcessing]:
        try:
            processing_id = (
                self._db.query(MailDraftEffect.processing_id)
                .filter(MailDraftEffect.id == effect_id)
                .scalar()
            )
            if processing_id is None:
                raise MailProcessingApplicationError("mail.draft_effect_state_invalid")
            processing = (
                self._db.query(MailMessageProcessing)
                .filter(MailMessageProcessing.id == processing_id)
                .with_for_update()
                .first()
            )
            effect = (
                self._db.query(MailDraftEffect)
                .filter(
                    MailDraftEffect.id == effect_id,
                    MailDraftEffect.processing_id == processing_id,
                )
                .with_for_update()
                .first()
            )
            if (
                processing is None
                or effect is None
                or effect.status != MailDraftEffectStatus.CLAIMED.value
            ):
                raise MailProcessingApplicationError("mail.draft_effect_state_invalid")
            return effect, processing
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise MailProcessingApplicationError(
                "mail.processing_persistence_failed"
            ) from exc

    def _complete_effect(
        self, effect: MailDraftEffect, *, terminal: bool = True
    ) -> None:
        effect.lease_owner_hash = None
        effect.lease_expires_at = None
        effect.completed_at = self._clock() if terminal else None
        try:
            self._db.commit()
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise MailProcessingApplicationError(
                "mail.processing_persistence_failed"
            ) from exc

    def _required_processing_for_update(
        self, processing_id: uuid.UUID
    ) -> MailMessageProcessing:
        processing = (
            self._db.query(MailMessageProcessing)
            .filter(MailMessageProcessing.id == processing_id)
            .with_for_update()
            .first()
        )
        if processing is None:
            raise MailProcessingApplicationError("mail.processing_not_available")
        return processing
