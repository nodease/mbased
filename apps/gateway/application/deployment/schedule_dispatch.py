from __future__ import annotations

from datetime import timedelta

from apps.gateway.application.deployment.schedule_models import (
    DispatchCanonicalContext,
    DispatchClaimSnapshot,
    SchedulePublishBatch,
    SchedulePublishRequest,
    SchedulePublishResult,
    ScheduleRecoveryResult,
)
from apps.gateway.application.deployment.schedule_ports import (
    BudgetDecisionPort,
    ScheduleConfigurationPreflightPort,
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
    REASON_APP_NOT_FOUND,
    REASON_BUDGET_BLOCKED,
    REASON_BUDGET_EVALUATION_FAILED,
    REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
    REASON_DEPLOYMENT_INACTIVE,
    REASON_DEPLOYMENT_NOT_CURRENT,
    REASON_DEPLOYMENT_NOT_FOUND,
    REASON_DEPLOYMENT_TYPE_NOT_ALLOWED,
    REASON_ENQUEUE_ATTEMPTS_EXHAUSTED,
    REASON_ORGANIZATION_SCOPE_MISMATCH,
    REASON_ORGANIZATION_SCOPE_MISSING,
    REASON_SCHEDULE_DEPLOYMENT_MISMATCH,
    REASON_SCHEDULE_NOT_FOUND,
    STATUS_CANCELED,
    STATUS_DEAD_LETTERED,
    STATUS_DISPATCHING,
    ScheduleDispatchSettings,
    next_dispatch_attempt,
    retry_delay_seconds,
)


class ScheduleDispatchUseCase:
    def __init__(
        self,
        *,
        settings: ScheduleDispatchSettings,
        runtime_policy: DeploymentRuntimePolicy,
    ) -> None:
        self.settings = settings
        self.runtime_policy = runtime_policy

    def prepare_publish_batch(
        self,
        *,
        repository: ScheduleDispatchRepositoryPort,
        budget: BudgetDecisionPort,
        configuration_preflight: ScheduleConfigurationPreflightPort,
        audit: ScheduleDispatchAuditRecorderPort,
        uow: ScheduleDispatchUnitOfWork,
        owner: str,
    ) -> SchedulePublishBatch:
        if not self.settings.processes_existing_claims:
            return SchedulePublishBatch(requests=())
        prepared: list[SchedulePublishRequest] = []
        enqueue_attempts_exhausted = 0
        budget_evaluation_failed = 0
        try:
            scan_now = repository.database_now()
            for claim in repository.lock_dispatch_candidates(
                now=scan_now,
                limit=self.settings.dispatch_batch_size,
            ):
                context = repository.load_canonical_context(claim)
                reason = self._canonical_rejection_reason(claim, context)
                if reason is not None:
                    transition_now = repository.database_now()
                    repository.mark_pre_dispatch_terminal(
                        claim,
                        status=STATUS_CANCELED,
                        reason=reason,
                        now=transition_now,
                    )
                    audit.record_policy_result(
                        organization_id=claim.organization_id,
                        claim_id=claim.claim_id,
                        action="schedule_dispatch.canceled",
                        reason=reason,
                    )
                    continue

                if not configuration_preflight.is_ready(
                    graph_snapshot=context.graph_snapshot,
                    organization_id=claim.organization_id,
                ):
                    transition_now = repository.database_now()
                    repository.mark_pre_dispatch_terminal(
                        claim,
                        status=STATUS_CANCELED,
                        reason=REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
                        now=transition_now,
                    )
                    audit.record_policy_result(
                        organization_id=claim.organization_id,
                        claim_id=claim.claim_id,
                        action="schedule_dispatch.canceled",
                        reason=REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
                    )
                    continue

                decision_now = repository.database_now()
                decision = budget.evaluate(
                    workflow_id=context.workflow_id,
                    now=decision_now,
                )
                transition_now = repository.database_now()
                if decision.status == "blocked":
                    repository.mark_pre_dispatch_terminal(
                        claim,
                        status=STATUS_CANCELED,
                        reason=REASON_BUDGET_BLOCKED,
                        now=transition_now,
                    )
                    if context.workflow_id is None:
                        raise RuntimeError(
                            "budget block requires canonical workflow identity"
                        )
                    audit.record_budget_block(
                        organization_id=claim.organization_id,
                        workflow_id=context.workflow_id,
                        claim_id=claim.claim_id,
                    )
                    continue

                next_attempt, already_exhausted = next_dispatch_attempt(
                    claim.attempt_count,
                    max_attempts=self.settings.max_attempts,
                )
                if already_exhausted and claim.attempt_count >= self.settings.max_attempts:
                    repository.mark_pre_dispatch_terminal(
                        claim,
                        status=STATUS_DEAD_LETTERED,
                        reason=REASON_ENQUEUE_ATTEMPTS_EXHAUSTED,
                        now=transition_now,
                    )
                    audit.record_policy_result(
                        organization_id=claim.organization_id,
                        claim_id=claim.claim_id,
                        action="schedule_dispatch.failed",
                        reason=REASON_ENQUEUE_ATTEMPTS_EXHAUSTED,
                    )
                    enqueue_attempts_exhausted += 1
                    continue
                if decision.status == "unavailable":
                    exhausted = next_attempt >= self.settings.max_attempts
                    repository.mark_budget_unavailable(
                        claim,
                        now=transition_now,
                        exhausted=exhausted,
                        attempt_count=next_attempt,
                        next_attempt_at=(
                            None
                            if exhausted
                            else transition_now
                            + timedelta(
                                seconds=retry_delay_seconds(
                                    next_attempt,
                                    base_seconds=self.settings.retry_base_seconds,
                                )
                            )
                        ),
                    )
                    audit.record_policy_result(
                        organization_id=claim.organization_id,
                        claim_id=claim.claim_id,
                        action=(
                            "schedule_dispatch.failed"
                            if exhausted
                            else "schedule_dispatch.deferred"
                        ),
                        reason=REASON_BUDGET_EVALUATION_FAILED,
                    )
                    if exhausted:
                        budget_evaluation_failed += 1
                    continue

                repository.mark_dispatching(
                    claim,
                    owner=owner,
                    now=transition_now,
                    attempt_count=next_attempt,
                    lease_expires_at=transition_now
                    + timedelta(seconds=self.settings.lease_seconds),
                )
                prepared.append(
                    SchedulePublishRequest(
                        claim_id=claim.claim_id,
                        task_id=claim.idempotency_key,
                        lease_owner=owner,
                    )
                )
            uow.commit()
            return SchedulePublishBatch(
                requests=tuple(prepared),
                enqueue_attempts_exhausted=enqueue_attempts_exhausted,
                budget_evaluation_failed=budget_evaluation_failed,
            )
        except Exception:
            uow.rollback()
            raise

    def record_publish_result(
        self,
        *,
        repository: ScheduleDispatchRepositoryPort,
        uow: ScheduleDispatchUnitOfWork,
        request: SchedulePublishRequest,
        accepted: bool,
    ) -> SchedulePublishResult:
        try:
            exhausted = False
            claim = repository.get_claim_for_publish_result(request.claim_id)
            # The row lock may wait. Read PostgreSQL wall clock only after the
            # canonical claim is locked so delivery/retry deadlines are fresh.
            now = repository.database_now()
            if accepted:
                changed = repository.record_publish_accepted(
                    claim_id=request.claim_id,
                    owner=request.lease_owner,
                    now=now,
                    delivery_deadline_at=now
                    + timedelta(seconds=self.settings.delivery_timeout_seconds),
                )
            else:
                next_attempt = claim.attempt_count
                exhausted = next_attempt >= self.settings.max_attempts
                changed = repository.record_publish_failed(
                    claim_id=request.claim_id,
                    owner=request.lease_owner,
                    now=now,
                    exhausted=exhausted,
                    next_attempt_at=(
                        None
                        if exhausted
                        else now
                        + timedelta(
                            seconds=retry_delay_seconds(
                                max(next_attempt, 1),
                                base_seconds=self.settings.retry_base_seconds,
                            )
                        )
                    ),
                )
            uow.commit()
            return SchedulePublishResult(
                changed=changed,
                dead_lettered_reason=(
                    REASON_ENQUEUE_ATTEMPTS_EXHAUSTED
                    if (
                        changed
                        and not accepted
                        and exhausted
                        and claim.status == STATUS_DISPATCHING
                    )
                    else None
                ),
            )
        except Exception:
            uow.rollback()
            raise

    def recover_critical(
        self,
        *,
        repository: ScheduleDispatchRepositoryPort,
        uow: ScheduleDispatchUnitOfWork,
    ) -> ScheduleRecoveryResult:
        if not self.settings.processes_existing_claims:
            return ScheduleRecoveryResult(0, 0, 0)
        try:
            now = repository.database_now()
            retried, exhausted = repository.recover_expired_claims(
                now=now,
                limit=self.settings.recovery_batch_size,
                max_attempts=self.settings.max_attempts,
                retry_base_seconds=self.settings.retry_base_seconds,
            )
            unknown = repository.quarantine_expired_running(
                now=now,
                limit=self.settings.recovery_batch_size,
            )
            uow.commit()
            return ScheduleRecoveryResult(retried, exhausted, unknown)
        except Exception:
            uow.rollback()
            raise

    def maintain_visibility(
        self,
        *,
        repository: ScheduleDispatchRepositoryPort,
        audit: ScheduleDispatchAuditRecorderPort,
        uow: ScheduleDispatchUnitOfWork,
    ) -> int:
        try:
            now = repository.database_now()
            visibility_gaps = repository.lock_workflow_run_visibility_gaps(
                now=now,
                grace_seconds=self.settings.workflow_run_visibility_timeout_seconds,
                limit=self.settings.recovery_batch_size,
            )
            for gap in visibility_gaps:
                audit.record_workflow_run_missing(
                    organization_id=gap.organization_id,
                    claim_id=gap.claim_id,
                )
                repository.mark_workflow_run_missing_reported(
                    gap.claim_id,
                    now=now,
                )
            uow.commit()
            return len(visibility_gaps)
        except Exception:
            uow.rollback()
            raise

    def cleanup_terminal_claims(
        self,
        *,
        repository: ScheduleDispatchRepositoryPort,
        uow: ScheduleDispatchUnitOfWork,
    ) -> int:
        try:
            now = repository.database_now()
            cleaned = repository.cleanup_terminal_claims(
                now=now,
                retention_days=self.settings.retention_days,
                dead_letter_retention_days=self.settings.dead_letter_retention_days,
                limit=self.settings.cleanup_batch_size,
            )
            uow.commit()
            return cleaned
        except Exception:
            uow.rollback()
            raise

    def _canonical_rejection_reason(
        self,
        claim: DispatchClaimSnapshot,
        context: DispatchCanonicalContext,
    ) -> str | None:
        if context.schedule_id is None:
            return REASON_SCHEDULE_NOT_FOUND
        if context.schedule_id != claim.schedule_id:
            return REASON_SCHEDULE_NOT_FOUND
        if context.deployment_id is None:
            return REASON_DEPLOYMENT_NOT_FOUND
        if context.deployment_id != claim.deployment_id:
            return REASON_SCHEDULE_DEPLOYMENT_MISMATCH
        if not context.app_exists:
            return REASON_APP_NOT_FOUND
        if context.organization_id is None:
            return REASON_ORGANIZATION_SCOPE_MISSING
        if context.organization_id != claim.organization_id:
            return REASON_ORGANIZATION_SCOPE_MISMATCH
        if not context.deployment_active:
            return REASON_DEPLOYMENT_INACTIVE
        if not context.deployment_current:
            return REASON_DEPLOYMENT_NOT_CURRENT
        if not is_deployment_type_allowed_for_surface(
            context.deployment_type,
            SURFACE_SCHEDULE_RUN,
            policy=self.runtime_policy,
        ):
            return REASON_DEPLOYMENT_TYPE_NOT_ALLOWED
        return None
