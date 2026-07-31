from __future__ import annotations

import uuid
from dataclasses import dataclass

from apps.gateway.application.deployment.schedule_ports import (
    ScheduleDispatchAuditRecorderPort,
    ScheduleDispatchRepositoryPort,
    ScheduleDispatchUnitOfWork,
)
from apps.shared.domain.schedule_dispatch import (
    REASON_EXECUTION_OUTCOME_UNKNOWN,
    STATUS_DEAD_LETTERED,
    validate_operation_correlation,
    validate_outcome_resolution,
)


class ScheduleOutcomeReviewError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScheduleOutcomeReviewResult:
    claim_id: uuid.UUID
    audit_id: uuid.UUID
    resolution: str


class ScheduleOutcomeReviewUseCase:
    def review(
        self,
        *,
        repository: ScheduleDispatchRepositoryPort,
        audit: ScheduleDispatchAuditRecorderPort,
        uow: ScheduleDispatchUnitOfWork,
        claim_id: uuid.UUID,
        resolution: str,
        operation_correlation_id: str,
    ) -> ScheduleOutcomeReviewResult:
        resolution = validate_outcome_resolution(resolution)
        operation_correlation_id = validate_operation_correlation(
            operation_correlation_id
        )
        try:
            claim = repository.lock_outcome_review_claim(claim_id)
            if claim is None:
                raise ScheduleOutcomeReviewError("schedule dispatch claim was not found")
            if not (
                claim.status == STATUS_DEAD_LETTERED
                and claim.safe_reason_code == REASON_EXECUTION_OUTCOME_UNKNOWN
            ):
                raise ScheduleOutcomeReviewError(
                    "schedule dispatch claim is not reviewable"
                )
            if claim.outcome_reviewed_at is not None:
                raise ScheduleOutcomeReviewError(
                    "schedule dispatch claim was already reviewed"
                )

            reviewed_at = repository.database_now()
            audit_id = audit.record_outcome_reviewed(
                organization_id=claim.organization_id,
                claim_id=claim.claim_id,
                operation_correlation_id=operation_correlation_id,
                outcome_resolution_code=resolution,
            )
            repository.mark_outcome_reviewed(
                claim.claim_id,
                reviewed_at=reviewed_at,
                audit_id=audit_id,
                resolution=resolution,
            )
            uow.commit()
            return ScheduleOutcomeReviewResult(
                claim_id=claim.claim_id,
                audit_id=audit_id,
                resolution=resolution,
            )
        except Exception:
            uow.rollback()
            raise
