from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ScheduleDefinitionSnapshot:
    schedule_id: uuid.UUID
    deployment_id: uuid.UUID
    organization_id: uuid.UUID | None
    workflow_id: uuid.UUID | None
    deployment_type: object
    cron_expression: str
    timezone: str


@dataclass(frozen=True, slots=True)
class ScheduleOccurrenceSnapshot(ScheduleDefinitionSnapshot):
    scheduled_for: datetime


@dataclass(frozen=True, slots=True)
class DispatchClaimSnapshot:
    claim_id: uuid.UUID
    schedule_id: uuid.UUID
    deployment_id: uuid.UUID
    organization_id: uuid.UUID
    idempotency_key: str
    attempt_count: int
    status: str


@dataclass(frozen=True, slots=True)
class DispatchCanonicalContext:
    app_exists: bool
    schedule_id: uuid.UUID | None
    deployment_id: uuid.UUID | None
    organization_id: uuid.UUID | None
    workflow_id: uuid.UUID | None
    deployment_type: object | None
    deployment_active: bool
    deployment_current: bool
    graph_snapshot: dict | None


@dataclass(frozen=True, slots=True)
class SchedulePublishRequest:
    claim_id: uuid.UUID
    task_id: str
    lease_owner: str


@dataclass(frozen=True, slots=True)
class SchedulePublishBatch:
    requests: tuple[SchedulePublishRequest, ...]
    enqueue_attempts_exhausted: int = 0
    budget_evaluation_failed: int = 0


@dataclass(frozen=True, slots=True)
class SchedulePublishResult:
    changed: bool
    dead_lettered_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduleRecoveryResult:
    retried: int
    enqueue_attempts_exhausted: int
    execution_outcome_unknown: int


@dataclass(frozen=True, slots=True)
class WorkflowRunVisibilityGap:
    claim_id: uuid.UUID
    organization_id: uuid.UUID
    workflow_run_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ScheduleOutcomeReviewSnapshot:
    claim_id: uuid.UUID
    organization_id: uuid.UUID
    status: str
    safe_reason_code: str | None
    outcome_reviewed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ScheduleRollbackBlockers:
    nonterminal_claims: int
    unreviewed_outcome_unknown_claims: int

    @property
    def ready(self) -> bool:
        return self.nonterminal_claims == 0 and self.unreviewed_outcome_unknown_claims == 0
