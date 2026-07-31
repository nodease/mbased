from __future__ import annotations

from datetime import timedelta

from apps.gateway.application.deployment.schedule_errors import (
    ScheduleConfigurationError,
)
from apps.gateway.application.deployment.schedule_ports import (
    BudgetDecisionPort,
    NextFireCalculatorPort,
    ScheduleDispatchAuditRecorderPort,
    ScheduleDispatchRepositoryPort,
    ScheduleDispatchUnitOfWork,
)
from apps.shared.domain.deployment_runtime_policy import (
    SURFACE_SCHEDULE_RUN,
    DeploymentRuntimePolicy,
    is_deployment_type_allowed_for_surface,
)
from apps.shared.domain.schedule_dispatch import (
    REASON_BUDGET_BLOCKED,
    REASON_BUDGET_EVALUATION_FAILED,
    REASON_DEPLOYMENT_TYPE_NOT_ALLOWED,
    SCHEDULE_CONFIGURATION_INVALID,
    STATUS_CANCELED,
    STATUS_PENDING,
    ScheduleDispatchSettings,
    retry_delay_seconds,
    schedule_idempotency_key,
)


class ScheduleOccurrenceUseCase:
    def __init__(
        self,
        *,
        settings: ScheduleDispatchSettings,
        runtime_policy: DeploymentRuntimePolicy,
        next_fire: NextFireCalculatorPort,
    ) -> None:
        self.settings = settings
        self.runtime_policy = runtime_policy
        self.next_fire = next_fire

    def reconcile_uninitialized(
        self,
        *,
        repository: ScheduleDispatchRepositoryPort,
        audit: ScheduleDispatchAuditRecorderPort,
        uow: ScheduleDispatchUnitOfWork,
    ) -> int:
        if not self.settings.claims_new_occurrences:
            return 0
        count = 0
        try:
            now = repository.database_now()
            for schedule in repository.lock_uninitialized_schedules(
                self.settings.occurrence_batch_size
            ):
                try:
                    next_run = self.next_fire.first_after(
                        cron_expression=schedule.cron_expression,
                        timezone_name=schedule.timezone,
                        now=now,
                    )
                except ScheduleConfigurationError:
                    if schedule.organization_id is not None:
                        repository.mark_configuration_invalid(
                            schedule.schedule_id,
                            SCHEDULE_CONFIGURATION_INVALID,
                        )
                        audit.record_schedule_configuration_invalid(
                            organization_id=schedule.organization_id,
                            schedule_id=schedule.schedule_id,
                        )
                    continue
                repository.initialize_next_run(schedule.schedule_id, next_run)
                count += 1
            uow.commit()
            return count
        except Exception:
            uow.rollback()
            raise

    def claim_due_occurrences(
        self,
        *,
        repository: ScheduleDispatchRepositoryPort,
        budget: BudgetDecisionPort,
        audit: ScheduleDispatchAuditRecorderPort,
        uow: ScheduleDispatchUnitOfWork,
    ) -> int:
        if not self.settings.claims_new_occurrences:
            return 0
        count = 0
        try:
            now = repository.database_now()
            occurrences = repository.lock_due_occurrences(
                now=now,
                limit=self.settings.occurrence_batch_size,
            )
            for occurrence in occurrences:
                if occurrence.organization_id is None:
                    continue
                try:
                    next_run = self.next_fire.next_after_occurrence(
                        cron_expression=occurrence.cron_expression,
                        timezone_name=occurrence.timezone,
                        scheduled_for=occurrence.scheduled_for,
                        now=now,
                    )
                except ScheduleConfigurationError:
                    repository.mark_configuration_invalid(
                        occurrence.schedule_id,
                        SCHEDULE_CONFIGURATION_INVALID,
                    )
                    audit.record_schedule_configuration_invalid(
                        organization_id=occurrence.organization_id,
                        schedule_id=occurrence.schedule_id,
                    )
                    continue

                status = STATUS_PENDING
                reason = None
                next_attempt_at = None
                if not is_deployment_type_allowed_for_surface(
                    occurrence.deployment_type,
                    SURFACE_SCHEDULE_RUN,
                    policy=self.runtime_policy,
                ):
                    status = STATUS_CANCELED
                    reason = REASON_DEPLOYMENT_TYPE_NOT_ALLOWED
                else:
                    decision = budget.evaluate(
                        workflow_id=occurrence.workflow_id,
                        now=now,
                    )
                    if decision.status == "blocked":
                        status = STATUS_CANCELED
                        reason = REASON_BUDGET_BLOCKED
                    elif decision.status == "unavailable":
                        reason = REASON_BUDGET_EVALUATION_FAILED
                        next_attempt_at = now + timedelta(
                            seconds=retry_delay_seconds(
                                1,
                                base_seconds=self.settings.retry_base_seconds,
                            )
                        )

                claim_id = repository.create_claim(
                    occurrence,
                    status=status,
                    idempotency_key=schedule_idempotency_key(
                        occurrence.schedule_id,
                        occurrence.scheduled_for,
                    ),
                    now=now,
                    safe_reason_code=reason,
                    next_attempt_at=next_attempt_at,
                )
                repository.advance_schedule(occurrence.schedule_id, next_run)
                if reason == REASON_BUDGET_BLOCKED:
                    if occurrence.workflow_id is None:
                        raise RuntimeError(
                            "budget block requires canonical workflow identity"
                        )
                    audit.record_budget_block(
                        organization_id=occurrence.organization_id,
                        workflow_id=occurrence.workflow_id,
                        claim_id=claim_id,
                    )
                elif reason is not None:
                    audit.record_policy_result(
                        organization_id=occurrence.organization_id,
                        claim_id=claim_id,
                        action=(
                            "schedule_dispatch.canceled"
                            if status == STATUS_CANCELED
                            else "schedule_dispatch.deferred"
                        ),
                        reason=reason,
                    )
                count += 1
            uow.commit()
            return count
        except Exception:
            uow.rollback()
            raise
