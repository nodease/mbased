from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from apps.shared.domain.deployment_runtime_policy import (
    SURFACE_SCHEDULE_RUN,
    DeploymentRuntimePolicy,
    is_deployment_type_allowed_for_surface,
)
from apps.shared.domain.schedule_dispatch import (
    REASON_APP_NOT_FOUND,
    REASON_BUDGET_BLOCKED,
    REASON_BUDGET_EVALUATION_FAILED,
    REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
    REASON_DEPLOYMENT_INACTIVE,
    REASON_DEPLOYMENT_NOT_CURRENT,
    REASON_DEPLOYMENT_NOT_FOUND,
    REASON_DEPLOYMENT_TYPE_NOT_ALLOWED,
    REASON_EXECUTION_FAILED_AFTER_ADMISSION,
    REASON_EXECUTION_OUTCOME_UNKNOWN,
    REASON_ORGANIZATION_SCOPE_MISMATCH,
    REASON_ORGANIZATION_SCOPE_MISSING,
    REASON_SCHEDULE_DEPLOYMENT_MISMATCH,
    REASON_SCHEDULE_NOT_FOUND,
    STATUS_CANCELED,
    STATUS_DEAD_LETTERED,
    STATUS_DISPATCHING,
    STATUS_ENQUEUED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    ScheduleDispatchSettings,
    retry_delay_seconds,
)
from apps.shared.domain.workflow_budget import BudgetExecutionDecision
from apps.shared.domain.workflow_execution_identity import schedule_execution_id

AdmissionStatus = Literal["admitted", "duplicate", "rejected", "deferred"]


@dataclass(frozen=True, slots=True)
class ScheduleClaimLocator:
    claim_id: uuid.UUID
    schedule_id: uuid.UUID
    deployment_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ScheduleAdmissionSnapshot:
    claim_id: uuid.UUID
    schedule_id: uuid.UUID
    claim_deployment_id: uuid.UUID
    claim_organization_id: uuid.UUID
    status: str
    idempotency_key: str
    attempt_count: int
    scheduled_for: datetime
    app_exists: bool
    deployment_exists: bool
    schedule_exists: bool
    canonical_schedule_id: uuid.UUID | None
    schedule_deployment_id: uuid.UUID | None
    app_id: uuid.UUID | None
    app_organization_id: uuid.UUID | None
    credential_principal_user_id: uuid.UUID | None
    workflow_id: uuid.UUID | None
    active_deployment_id: uuid.UUID | None
    deployment_id: uuid.UUID | None
    deployment_app_id: uuid.UUID | None
    deployment_active: bool
    deployment_type: object | None
    workflow_version: int | None
    graph_snapshot: dict | None


@dataclass(frozen=True, slots=True)
class ScheduledExecutionPlan:
    claim_id: uuid.UUID
    workflow_run_id: uuid.UUID
    admission_owner: str
    graph_snapshot: dict
    user_input: dict
    execution_context: dict


@dataclass(frozen=True, slots=True)
class ScheduleAdmissionResult:
    status: AdmissionStatus
    reason: str | None = None
    plan: ScheduledExecutionPlan | None = None
    dead_lettered_reason: str | None = None


class ScheduleAdmissionRepositoryPort(Protocol):
    def database_now(self) -> datetime: ...

    def read_locator(self, claim_id: uuid.UUID) -> ScheduleClaimLocator | None: ...

    def lock_canonical_bundle(
        self, locator: ScheduleClaimLocator
    ) -> ScheduleAdmissionSnapshot | None: ...

    def mark_canceled(self, *, reason: str, now: datetime) -> None: ...

    def mark_budget_deferred(
        self,
        *,
        now: datetime,
        next_attempt_at: datetime | None,
        exhausted: bool,
    ) -> None: ...

    def mark_running(
        self,
        *,
        workflow_run_id: uuid.UUID,
        admission_owner: str,
        now: datetime,
        execution_deadline_at: datetime,
    ) -> None: ...

    def finalize_succeeded(
        self,
        *,
        claim_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        admission_owner: str,
        now: datetime,
    ) -> bool: ...

    def finalize_failed(
        self,
        *,
        claim_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        admission_owner: str,
        now: datetime,
        reason: str = REASON_EXECUTION_FAILED_AFTER_ADMISSION,
    ) -> bool: ...


class UnitOfWorkPort(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class BudgetDecisionPort(Protocol):
    def evaluate(
        self, *, workflow_id: uuid.UUID | None, now: datetime
    ) -> BudgetExecutionDecision: ...


class ScheduleConfigurationPreflightPort(Protocol):
    def is_ready(
        self,
        *,
        graph_snapshot: dict | None,
        organization_id: uuid.UUID,
    ) -> bool: ...


class ScheduleAdmissionAuditPort(Protocol):
    def record_budget_block(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        claim_id: uuid.UUID,
    ) -> None: ...

    def record_claim_result(
        self,
        *,
        organization_id: uuid.UUID,
        claim_id: uuid.UUID,
        action: str,
        reason: str,
    ) -> None: ...


class ScheduledDeploymentExecutionUseCase:
    def __init__(
        self,
        *,
        settings: ScheduleDispatchSettings,
        runtime_policy: DeploymentRuntimePolicy,
    ) -> None:
        self.settings = settings
        self.runtime_policy = runtime_policy

    def admit(
        self,
        *,
        repository: ScheduleAdmissionRepositoryPort,
        budget: BudgetDecisionPort,
        configuration_preflight: ScheduleConfigurationPreflightPort,
        audit: ScheduleAdmissionAuditPort,
        uow: UnitOfWorkPort,
        claim_id: uuid.UUID,
        task_id: str,
        admission_owner: str,
    ) -> ScheduleAdmissionResult:
        if not self.settings.processes_existing_claims:
            return ScheduleAdmissionResult("deferred", "schedule_dispatch_disabled")
        try:
            locator = repository.read_locator(claim_id)
            if locator is None:
                return ScheduleAdmissionResult("rejected", REASON_SCHEDULE_NOT_FOUND)
            snapshot = repository.lock_canonical_bundle(locator)
            if snapshot is None:
                return ScheduleAdmissionResult("rejected", REASON_SCHEDULE_NOT_FOUND)
            if snapshot.idempotency_key != task_id:
                return ScheduleAdmissionResult("rejected", "task_id_mismatch")
            if snapshot.status in {
                STATUS_RUNNING,
                STATUS_SUCCEEDED,
                STATUS_CANCELED,
                STATUS_DEAD_LETTERED,
            }:
                return ScheduleAdmissionResult("duplicate", snapshot.status)
            if snapshot.status == STATUS_PENDING:
                return ScheduleAdmissionResult("rejected", STATUS_PENDING)
            if snapshot.status not in {STATUS_DISPATCHING, STATUS_ENQUEUED}:
                return ScheduleAdmissionResult("rejected", "invalid_claim_status")

            now = repository.database_now()
            reason = self._canonical_rejection_reason(snapshot)
            if reason is not None:
                repository.mark_canceled(reason=reason, now=now)
                audit.record_claim_result(
                    organization_id=snapshot.claim_organization_id,
                    claim_id=snapshot.claim_id,
                    action="schedule_dispatch.canceled",
                    reason=reason,
                )
                uow.commit()
                return ScheduleAdmissionResult("rejected", reason)

            if not configuration_preflight.is_ready(
                graph_snapshot=snapshot.graph_snapshot,
                organization_id=snapshot.claim_organization_id,
            ):
                repository.mark_canceled(
                    reason=REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
                    now=now,
                )
                audit.record_claim_result(
                    organization_id=snapshot.claim_organization_id,
                    claim_id=snapshot.claim_id,
                    action="schedule_dispatch.canceled",
                    reason=REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
                )
                uow.commit()
                return ScheduleAdmissionResult(
                    "rejected",
                    REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
                )

            decision = budget.evaluate(workflow_id=snapshot.workflow_id, now=now)
            transition_now = repository.database_now()
            if decision.status == "blocked":
                repository.mark_canceled(
                    reason=REASON_BUDGET_BLOCKED,
                    now=transition_now,
                )
                if snapshot.workflow_id is None:
                    raise RuntimeError(
                        "budget block requires canonical workflow identity"
                    )
                audit.record_budget_block(
                    organization_id=snapshot.claim_organization_id,
                    workflow_id=snapshot.workflow_id,
                    claim_id=snapshot.claim_id,
                )
                uow.commit()
                return ScheduleAdmissionResult("rejected", REASON_BUDGET_BLOCKED)
            if decision.status == "unavailable":
                exhausted = snapshot.attempt_count >= self.settings.max_attempts
                repository.mark_budget_deferred(
                    now=transition_now,
                    exhausted=exhausted,
                    next_attempt_at=(
                        None
                        if exhausted
                        else transition_now
                        + timedelta(
                            seconds=retry_delay_seconds(
                                max(snapshot.attempt_count, 1),
                                base_seconds=self.settings.retry_base_seconds,
                            )
                        )
                    ),
                )
                audit.record_claim_result(
                    organization_id=snapshot.claim_organization_id,
                    claim_id=snapshot.claim_id,
                    action=(
                        "schedule_dispatch.failed"
                        if exhausted
                        else "schedule_dispatch.deferred"
                    ),
                    reason=REASON_BUDGET_EVALUATION_FAILED,
                )
                uow.commit()
                return ScheduleAdmissionResult(
                    "deferred",
                    REASON_BUDGET_EVALUATION_FAILED,
                    dead_lettered_reason=(
                        REASON_BUDGET_EVALUATION_FAILED if exhausted else None
                    ),
                )

            workflow_run_id = uuid.uuid4()
            repository.mark_running(
                workflow_run_id=workflow_run_id,
                admission_owner=admission_owner,
                now=transition_now,
                execution_deadline_at=transition_now
                + timedelta(seconds=self.settings.execution_deadline_seconds),
            )
            uow.commit()
            return ScheduleAdmissionResult(
                "admitted",
                plan=ScheduledExecutionPlan(
                    claim_id=snapshot.claim_id,
                    workflow_run_id=workflow_run_id,
                    admission_owner=admission_owner,
                    graph_snapshot=snapshot.graph_snapshot or {},
                    user_input={
                        "triggered_at": snapshot.scheduled_for.isoformat(),
                        "schedule_id": str(snapshot.schedule_id),
                    },
                    execution_context={
                        "user_id": None,
                        "credential_principal": {
                            "subject_type": "user",
                            "subject_id": str(snapshot.credential_principal_user_id),
                        },
                        "workflow_id": str(snapshot.workflow_id),
                        "organization_id": str(snapshot.app_organization_id),
                        "app_id": str(snapshot.app_id),
                        "deployment_id": str(snapshot.deployment_id),
                        "workflow_version": snapshot.workflow_version,
                        "trigger_mode": "schedule",
                        "workflow_run_id": str(workflow_run_id),
                        "workflow_task_id": task_id,
                        "idempotency_key": task_id,
                        "execution_id": str(schedule_execution_id(snapshot.claim_id)),
                    },
                ),
            )
        except Exception:
            uow.rollback()
            raise

    def finalize(
        self,
        *,
        repository: ScheduleAdmissionRepositoryPort,
        uow: UnitOfWorkPort,
        plan: ScheduledExecutionPlan,
        succeeded: bool,
        failure_reason: str = REASON_EXECUTION_FAILED_AFTER_ADMISSION,
    ) -> bool:
        try:
            now = repository.database_now()
            method = (
                repository.finalize_succeeded
                if succeeded
                else repository.finalize_failed
            )
            kwargs = {
                "claim_id": plan.claim_id,
                "workflow_run_id": plan.workflow_run_id,
                "admission_owner": plan.admission_owner,
                "now": now,
            }
            if not succeeded:
                if failure_reason not in {
                    REASON_EXECUTION_FAILED_AFTER_ADMISSION,
                    REASON_EXECUTION_OUTCOME_UNKNOWN,
                }:
                    raise ValueError("invalid schedule execution failure reason")
                kwargs["reason"] = failure_reason
            changed = method(
                **kwargs,
            )
            uow.commit()
            return changed
        except Exception:
            uow.rollback()
            raise

    def _canonical_rejection_reason(
        self, snapshot: ScheduleAdmissionSnapshot
    ) -> str | None:
        if not snapshot.schedule_exists:
            return REASON_SCHEDULE_NOT_FOUND
        if snapshot.canonical_schedule_id != snapshot.schedule_id:
            return REASON_SCHEDULE_NOT_FOUND
        if snapshot.schedule_deployment_id != snapshot.claim_deployment_id:
            return REASON_SCHEDULE_DEPLOYMENT_MISMATCH
        if not snapshot.deployment_exists or snapshot.deployment_id is None:
            return REASON_DEPLOYMENT_NOT_FOUND
        if not snapshot.app_exists:
            return REASON_APP_NOT_FOUND
        if snapshot.credential_principal_user_id is None:
            return REASON_APP_NOT_FOUND
        if snapshot.deployment_app_id != snapshot.app_id:
            return REASON_APP_NOT_FOUND
        if snapshot.app_organization_id is None:
            return REASON_ORGANIZATION_SCOPE_MISSING
        if snapshot.app_organization_id != snapshot.claim_organization_id:
            return REASON_ORGANIZATION_SCOPE_MISMATCH
        if not snapshot.deployment_active:
            return REASON_DEPLOYMENT_INACTIVE
        if snapshot.active_deployment_id != snapshot.deployment_id:
            return REASON_DEPLOYMENT_NOT_CURRENT
        if not is_deployment_type_allowed_for_surface(
            snapshot.deployment_type,
            SURFACE_SCHEDULE_RUN,
            policy=self.runtime_policy,
        ):
            return REASON_DEPLOYMENT_TYPE_NOT_ALLOWED
        if not snapshot.workflow_id or not snapshot.graph_snapshot:
            return REASON_DEPLOYMENT_NOT_FOUND
        return None
