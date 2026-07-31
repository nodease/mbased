from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.shared.db.models.app import App
from apps.shared.db.models.schedule import Schedule
from apps.shared.db.models.schedule_dispatch import ScheduleDispatchClaim
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.domain.schedule_dispatch import (
    REASON_BUDGET_EVALUATION_FAILED,
    REASON_EXECUTION_FAILED_AFTER_ADMISSION,
    STATUS_CANCELED,
    STATUS_DEAD_LETTERED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
)
from apps.shared.services.workflow_budget_execution import (
    evaluate_workflow_budget_execution,
)
from apps.workflow_engine.application.schedule_dispatch import (
    ScheduleAdmissionSnapshot,
    ScheduleClaimLocator,
)


class SqlAlchemyScheduleAdmissionUnitOfWork:
    def __init__(self, db: Session) -> None:
        self.db = db

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


class SharedWorkflowBudgetDecisionAdapter:
    def __init__(self, db: Session) -> None:
        self.db = db

    def evaluate(self, *, workflow_id, now):
        return evaluate_workflow_budget_execution(
            self.db,
            workflow_id=workflow_id,
            now=now,
        )


class SqlAlchemyScheduleAdmissionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._claim: ScheduleDispatchClaim | None = None
        self._schedule: Schedule | None = None

    def database_now(self) -> datetime:
        return self.db.execute(select(func.clock_timestamp())).scalar_one()

    def read_locator(self, claim_id: uuid.UUID) -> ScheduleClaimLocator | None:
        row = self.db.execute(
            select(
                ScheduleDispatchClaim.id,
                ScheduleDispatchClaim.schedule_id,
                ScheduleDispatchClaim.deployment_id,
            ).where(ScheduleDispatchClaim.id == claim_id)
        ).one_or_none()
        if row is None:
            return None
        return ScheduleClaimLocator(
            claim_id=row.id,
            schedule_id=row.schedule_id,
            deployment_id=row.deployment_id,
        )

    def lock_canonical_bundle(
        self, locator: ScheduleClaimLocator
    ) -> ScheduleAdmissionSnapshot | None:
        deployment_locator = self.db.execute(
            select(WorkflowDeployment.app_id).where(
                WorkflowDeployment.id == locator.deployment_id
            )
        ).scalar_one_or_none()
        app = None
        if deployment_locator is not None:
            app = self.db.execute(
                select(App)
                .where(App.id == deployment_locator)
                .with_for_update()
            ).scalar_one_or_none()
        deployment = self.db.execute(
            select(WorkflowDeployment)
            .where(WorkflowDeployment.id == locator.deployment_id)
            .with_for_update()
        ).scalar_one_or_none()
        schedule = self.db.execute(
            select(Schedule)
            .where(Schedule.id == locator.schedule_id)
            .with_for_update()
        ).scalar_one_or_none()
        claim = self.db.execute(
            select(ScheduleDispatchClaim)
            .where(ScheduleDispatchClaim.id == locator.claim_id)
            .with_for_update()
        ).scalar_one_or_none()
        if claim is None:
            return None
        self._claim = claim
        self._schedule = schedule
        return ScheduleAdmissionSnapshot(
            claim_id=claim.id,
            schedule_id=claim.schedule_id,
            claim_deployment_id=claim.deployment_id,
            claim_organization_id=claim.organization_id,
            status=claim.status,
            idempotency_key=claim.idempotency_key,
            attempt_count=claim.attempt_count,
            scheduled_for=claim.scheduled_for,
            app_exists=app is not None,
            deployment_exists=deployment is not None,
            schedule_exists=schedule is not None,
            canonical_schedule_id=schedule.id if schedule is not None else None,
            schedule_deployment_id=(
                schedule.deployment_id if schedule is not None else None
            ),
            app_id=app.id if app is not None else None,
            app_organization_id=app.organization_id if app is not None else None,
            credential_principal_user_id=(
                deployment.created_by if deployment is not None else None
            ),
            workflow_id=app.workflow_id if app is not None else None,
            active_deployment_id=(
                app.active_deployment_id if app is not None else None
            ),
            deployment_id=deployment.id if deployment is not None else None,
            deployment_app_id=(
                deployment.app_id if deployment is not None else None
            ),
            deployment_active=bool(deployment and deployment.is_active),
            deployment_type=deployment.type if deployment is not None else None,
            workflow_version=deployment.version if deployment is not None else None,
            graph_snapshot=(
                deployment.graph_snapshot if deployment is not None else None
            ),
        )

    def mark_canceled(self, *, reason: str, now: datetime) -> None:
        claim = self._require_claim()
        claim.status = STATUS_CANCELED
        claim.safe_reason_code = reason
        claim.lease_owner = None
        claim.lease_expires_at = None
        claim.execution_deadline_at = None
        claim.next_attempt_at = None
        claim.workflow_run_id = None
        claim.started_at = None
        claim.completed_at = now
        claim.updated_at = now

    def mark_budget_deferred(
        self,
        *,
        now: datetime,
        next_attempt_at: datetime | None,
        exhausted: bool,
    ) -> None:
        claim = self._require_claim()
        claim.status = STATUS_DEAD_LETTERED if exhausted else STATUS_PENDING
        claim.safe_reason_code = REASON_BUDGET_EVALUATION_FAILED
        claim.lease_owner = None
        claim.lease_expires_at = None
        claim.execution_deadline_at = None
        claim.next_attempt_at = next_attempt_at
        claim.completed_at = now if exhausted else None
        claim.updated_at = now

    def mark_running(
        self,
        *,
        workflow_run_id: uuid.UUID,
        admission_owner: str,
        now: datetime,
        execution_deadline_at: datetime,
    ) -> None:
        claim = self._require_claim()
        claim.status = STATUS_RUNNING
        claim.lease_owner = admission_owner
        claim.lease_expires_at = None
        claim.execution_deadline_at = execution_deadline_at
        claim.workflow_run_id = workflow_run_id
        claim.enqueued_at = claim.enqueued_at or now
        claim.started_at = now
        claim.next_attempt_at = None
        claim.safe_reason_code = None
        claim.updated_at = now
        if self._schedule is not None:
            self._schedule.last_run_at = now

    def finalize_succeeded(self, **kwargs) -> bool:
        claim = self._lock_finalization_claim(**kwargs)
        if claim is None:
            return False
        now = kwargs["now"]
        claim.status = STATUS_SUCCEEDED
        claim.safe_reason_code = None
        claim.lease_owner = None
        claim.execution_deadline_at = None
        claim.completed_at = now
        claim.updated_at = now
        return True

    def finalize_failed(self, **kwargs) -> bool:
        claim = self._lock_finalization_claim(**kwargs)
        if claim is None:
            return False
        now = kwargs["now"]
        claim.status = STATUS_DEAD_LETTERED
        claim.safe_reason_code = kwargs.get(
            "reason",
            REASON_EXECUTION_FAILED_AFTER_ADMISSION,
        )
        claim.lease_owner = None
        claim.execution_deadline_at = None
        claim.completed_at = now
        claim.updated_at = now
        return True

    def _lock_finalization_claim(
        self,
        *,
        claim_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        admission_owner: str,
        now: datetime,
    ) -> ScheduleDispatchClaim | None:
        del now
        return self.db.execute(
            select(ScheduleDispatchClaim)
            .where(
                ScheduleDispatchClaim.id == claim_id,
                ScheduleDispatchClaim.status == STATUS_RUNNING,
                ScheduleDispatchClaim.workflow_run_id == workflow_run_id,
                ScheduleDispatchClaim.lease_owner == admission_owner,
            )
            .with_for_update()
        ).scalar_one_or_none()

    def _require_claim(self) -> ScheduleDispatchClaim:
        if self._claim is None:
            raise RuntimeError("schedule claim is not locked")
        return self._claim
