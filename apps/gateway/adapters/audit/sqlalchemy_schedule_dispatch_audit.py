from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from apps.shared.db.models.audit_log import (
    ActorType,
    AuditCategory,
    AuditLog,
    AuditStatus,
)
from apps.shared.domain.policy_reason import BUDGET_EXCEEDED_POLICY_REASON
from apps.shared.domain.schedule_dispatch import SCHEDULE_DISPATCH_REASONS

_ALLOWED_ACTIONS = frozenset(
    {
        "schedule_dispatch.canceled",
        "schedule_dispatch.deferred",
        "schedule_dispatch.failed",
        "schedule_dispatch.workflow_run_missing",
    }
)


class SqlAlchemyScheduleDispatchAuditRecorder:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record_budget_block(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        claim_id: uuid.UUID,
    ) -> None:
        self.db.add(
            AuditLog(
                action="policy.block",
                category=AuditCategory.ACTION,
                actor_id=None,
                actor_type=ActorType.SYSTEM,
                target_type="workflow",
                target_id=str(workflow_id),
                before=None,
                after=None,
                status=AuditStatus.FAILURE,
                audit_metadata={
                    "organization_id": str(organization_id),
                    "reason": BUDGET_EXCEEDED_POLICY_REASON,
                    "policy_reason": BUDGET_EXCEEDED_POLICY_REASON,
                    "trigger_mode": "scheduler",
                    "schedule_dispatch_claim_id": str(claim_id),
                },
            )
        )
    def record_policy_result(
        self,
        *,
        organization_id: uuid.UUID,
        claim_id: uuid.UUID,
        action: str,
        reason: str,
    ) -> None:
        if action not in _ALLOWED_ACTIONS:
            raise ValueError("unsupported schedule dispatch audit action")
        if reason not in SCHEDULE_DISPATCH_REASONS:
            raise ValueError("unsupported schedule dispatch audit reason")
        self.db.add(
            AuditLog(
                action=action,
                category=AuditCategory.ACTION,
                actor_id=None,
                actor_type=ActorType.SYSTEM,
                target_type="schedule_dispatch_claim",
                target_id=str(claim_id),
                before=None,
                after=None,
                status=(
                    AuditStatus.FAILURE
                    if action != "schedule_dispatch.deferred"
                    else AuditStatus.SUCCESS
                ),
                audit_metadata={
                    "organization_id": str(organization_id),
                    "reason": reason,
                },
            )
        )

    def record_schedule_configuration_invalid(
        self,
        *,
        organization_id: uuid.UUID,
        schedule_id: uuid.UUID,
    ) -> None:
        self.db.add(
            AuditLog(
                action="schedule.configuration_invalid",
                category=AuditCategory.ACTION,
                actor_id=None,
                actor_type=ActorType.SYSTEM,
                target_type="schedule",
                target_id=str(schedule_id),
                before=None,
                after=None,
                status=AuditStatus.FAILURE,
                audit_metadata={
                    "organization_id": str(organization_id),
                    "reason": "schedule_configuration_invalid",
                },
            )
        )

    def record_workflow_run_missing(
        self,
        *,
        organization_id: uuid.UUID,
        claim_id: uuid.UUID,
    ) -> None:
        self.db.add(
            AuditLog(
                action="schedule_dispatch.workflow_run_missing",
                category=AuditCategory.ACTION,
                actor_id=None,
                actor_type=ActorType.SYSTEM,
                target_type="schedule_dispatch_claim",
                target_id=str(claim_id),
                before=None,
                after=None,
                status=AuditStatus.FAILURE,
                audit_metadata={
                    "organization_id": str(organization_id),
                    "reason": "workflow_run_missing",
                },
            )
        )

    def record_outcome_reviewed(
        self,
        *,
        organization_id: uuid.UUID,
        claim_id: uuid.UUID,
        operation_correlation_id: str,
        outcome_resolution_code: str,
    ) -> uuid.UUID:
        audit_id = uuid.uuid4()
        self.db.add(
            AuditLog(
                id=audit_id,
                action="schedule_dispatch.outcome_reviewed",
                category=AuditCategory.ACTION,
                actor_id=None,
                actor_type=ActorType.SYSTEM,
                target_type="schedule_dispatch_claim",
                target_id=str(claim_id),
                before=None,
                after=None,
                status=AuditStatus.SUCCESS,
                audit_metadata={
                    "organization_id": str(organization_id),
                    "operation_correlation_id": operation_correlation_id,
                    "outcome_resolution_code": outcome_resolution_code,
                },
            )
        )
        return audit_id
