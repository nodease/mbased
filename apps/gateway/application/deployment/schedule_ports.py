from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from apps.gateway.application.deployment.schedule_models import (
    DispatchCanonicalContext,
    DispatchClaimSnapshot,
    ScheduleDefinitionSnapshot,
    ScheduleOccurrenceSnapshot,
    ScheduleOutcomeReviewSnapshot,
    SchedulePublishRequest,
    ScheduleRollbackBlockers,
    WorkflowRunVisibilityGap,
)
from apps.shared.domain.workflow_budget import BudgetExecutionDecision


class ScheduleDispatchUnitOfWork(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ScheduleDispatchRepositoryPort(Protocol):
    def database_now(self) -> datetime: ...

    def lock_uninitialized_schedules(
        self, limit: int
    ) -> Sequence[ScheduleDefinitionSnapshot]: ...

    def lock_due_occurrences(
        self, *, now: datetime, limit: int
    ) -> Sequence[ScheduleOccurrenceSnapshot]: ...

    def initialize_next_run(
        self, schedule_id: uuid.UUID, next_run_at: datetime
    ) -> None: ...

    def mark_configuration_invalid(
        self, schedule_id: uuid.UUID, error_code: str
    ) -> None: ...

    def create_claim(
        self,
        occurrence: ScheduleOccurrenceSnapshot,
        *,
        status: str,
        idempotency_key: str,
        now: datetime,
        safe_reason_code: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> uuid.UUID: ...

    def advance_schedule(
        self, schedule_id: uuid.UUID, next_run_at: datetime
    ) -> None: ...

    def lock_dispatch_candidates(
        self, *, now: datetime, limit: int
    ) -> Sequence[DispatchClaimSnapshot]: ...

    def load_canonical_context(
        self, claim: DispatchClaimSnapshot
    ) -> DispatchCanonicalContext: ...

    def mark_dispatching(
        self,
        claim: DispatchClaimSnapshot,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
        attempt_count: int,
    ) -> None: ...

    def mark_pre_dispatch_terminal(
        self,
        claim: DispatchClaimSnapshot,
        *,
        status: str,
        reason: str,
        now: datetime,
    ) -> None: ...

    def mark_budget_unavailable(
        self,
        claim: DispatchClaimSnapshot,
        *,
        now: datetime,
        next_attempt_at: datetime | None,
        exhausted: bool,
        attempt_count: int,
    ) -> None: ...

    def record_publish_accepted(
        self,
        *,
        claim_id: uuid.UUID,
        owner: str,
        now: datetime,
        delivery_deadline_at: datetime,
    ) -> bool: ...

    def get_claim_for_publish_result(
        self, claim_id: uuid.UUID
    ) -> DispatchClaimSnapshot: ...

    def record_publish_failed(
        self,
        *,
        claim_id: uuid.UUID,
        owner: str,
        now: datetime,
        next_attempt_at: datetime | None,
        exhausted: bool,
    ) -> bool: ...

    def recover_expired_claims(
        self,
        *,
        now: datetime,
        limit: int,
        max_attempts: int,
        retry_base_seconds: int,
    ) -> tuple[int, int]: ...

    def quarantine_expired_running(self, *, now: datetime, limit: int) -> int: ...

    def lock_workflow_run_visibility_gaps(
        self,
        *,
        now: datetime,
        grace_seconds: int,
        limit: int,
    ) -> Sequence[WorkflowRunVisibilityGap]: ...

    def mark_workflow_run_missing_reported(
        self, claim_id: uuid.UUID, *, now: datetime
    ) -> None: ...

    def cleanup_terminal_claims(
        self,
        *,
        now: datetime,
        retention_days: int,
        dead_letter_retention_days: int,
        limit: int,
    ) -> int: ...

    def claim_age_seconds(self, *, now: datetime) -> tuple[float, float]: ...

    def lock_outcome_review_claim(
        self, claim_id: uuid.UUID
    ) -> ScheduleOutcomeReviewSnapshot | None: ...

    def mark_outcome_reviewed(
        self,
        claim_id: uuid.UUID,
        *,
        reviewed_at: datetime,
        audit_id: uuid.UUID,
        resolution: str,
    ) -> None: ...

    def count_rollback_blockers(self) -> ScheduleRollbackBlockers: ...


class NextFireCalculatorPort(Protocol):
    def first_after(
        self, *, cron_expression: str, timezone_name: str, now: datetime
    ) -> datetime: ...

    def next_after_occurrence(
        self,
        *,
        cron_expression: str,
        timezone_name: str,
        scheduled_for: datetime,
        now: datetime,
    ) -> datetime: ...


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


class ScheduleDispatchAuditRecorderPort(Protocol):
    def record_budget_block(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_id: uuid.UUID,
        claim_id: uuid.UUID,
    ) -> None: ...

    def record_policy_result(
        self,
        *,
        organization_id: uuid.UUID,
        claim_id: uuid.UUID,
        action: str,
        reason: str,
    ) -> None: ...

    def record_schedule_configuration_invalid(
        self,
        *,
        organization_id: uuid.UUID,
        schedule_id: uuid.UUID,
    ) -> None: ...

    def record_workflow_run_missing(
        self,
        *,
        organization_id: uuid.UUID,
        claim_id: uuid.UUID,
    ) -> None: ...

    def record_outcome_reviewed(
        self,
        *,
        organization_id: uuid.UUID,
        claim_id: uuid.UUID,
        operation_correlation_id: str,
        outcome_resolution_code: str,
    ) -> uuid.UUID: ...


class ScheduleTaskPublisherPort(Protocol):
    def publish(self, request: SchedulePublishRequest) -> None: ...
