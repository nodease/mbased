from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from apps.gateway.application.deployment.schedule_errors import (
    ScheduleConfigurationError,
)
from apps.gateway.application.deployment.schedule_models import (
    ScheduleDefinitionSnapshot,
    ScheduleOccurrenceSnapshot,
)
from apps.gateway.application.deployment.schedule_occurrence import (
    ScheduleOccurrenceUseCase,
)
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
)
from apps.shared.domain.schedule_dispatch import (
    MODE_CLAIM,
    MODE_DISABLED,
    REASON_BUDGET_BLOCKED,
    REASON_BUDGET_EVALUATION_FAILED,
    SCHEDULE_CONFIGURATION_INVALID,
    STATUS_CANCELED,
    STATUS_PENDING,
    ScheduleDispatchSettings,
)
from apps.shared.domain.workflow_budget import BudgetExecutionDecision

NOW = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
NEXT = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)


def _occurrence(**overrides):
    values = {
        "schedule_id": uuid.uuid4(),
        "deployment_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(),
        "deployment_type": DeploymentType.SCHEDULE,
        "cron_expression": "0 * * * *",
        "timezone": "UTC",
        "scheduled_for": NOW,
    }
    values.update(overrides)
    return ScheduleOccurrenceSnapshot(**values)


class _Uow:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _Repository:
    def __init__(self, occurrences=(), uninitialized=()):
        self.occurrences = tuple(occurrences)
        self.uninitialized = tuple(uninitialized)
        self.claims = []
        self.advanced = []
        self.initialized = []
        self.invalidated = []

    def database_now(self):
        return NOW

    def lock_due_occurrences(self, *, now, limit):
        assert now == NOW
        return self.occurrences[:limit]

    def lock_uninitialized_schedules(self, limit):
        return self.uninitialized[:limit]

    def create_claim(self, occurrence, **kwargs):
        claim_id = uuid.uuid4()
        self.claims.append((claim_id, occurrence, kwargs))
        return claim_id

    def advance_schedule(self, schedule_id, next_run_at):
        self.advanced.append((schedule_id, next_run_at))

    def initialize_next_run(self, schedule_id, next_run_at):
        self.initialized.append((schedule_id, next_run_at))

    def mark_configuration_invalid(self, schedule_id, error_code):
        self.invalidated.append((schedule_id, error_code))


class _Budget:
    def __init__(self, status="allowed"):
        self.status = status
        self.calls = []

    def evaluate(self, *, workflow_id, now):
        self.calls.append((workflow_id, now))
        return BudgetExecutionDecision(status=self.status)


class _Audit:
    def __init__(self):
        self.policy = []
        self.budget_blocks = []
        self.invalid = []

    def record_budget_block(self, **kwargs):
        self.budget_blocks.append(kwargs)

    def record_policy_result(self, **kwargs):
        self.policy.append(kwargs)

    def record_schedule_configuration_invalid(self, **kwargs):
        self.invalid.append(kwargs)


class _NextFire:
    def __init__(self, error=None):
        self.error = error

    def first_after(self, **kwargs):
        if self.error:
            raise self.error
        return NEXT

    def next_after_occurrence(self, **kwargs):
        if self.error:
            raise self.error
        return NEXT


def _use_case(mode=MODE_CLAIM, next_fire=None):
    return ScheduleOccurrenceUseCase(
        settings=ScheduleDispatchSettings(mode=mode),
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        next_fire=next_fire or _NextFire(),
    )


def test_due_occurrence_creates_claim_and_advances_cursor_in_one_uow():
    occurrence = _occurrence()
    repository = _Repository([occurrence])
    uow = _Uow()

    count = _use_case().claim_due_occurrences(
        repository=repository,
        budget=_Budget(),
        audit=_Audit(),
        uow=uow,
    )

    assert count == 1
    assert repository.claims[0][2]["status"] == STATUS_PENDING
    assert repository.advanced == [(occurrence.schedule_id, NEXT)]
    assert uow.commits == 1
    assert uow.rollbacks == 0


@pytest.mark.parametrize(
    ("decision", "status", "reason"),
    [
        ("blocked", STATUS_CANCELED, REASON_BUDGET_BLOCKED),
        ("unavailable", STATUS_PENDING, REASON_BUDGET_EVALUATION_FAILED),
    ],
)
def test_budget_decision_is_persisted_with_same_occurrence(decision, status, reason):
    occurrence = _occurrence()
    repository = _Repository([occurrence])
    audit = _Audit()

    _use_case().claim_due_occurrences(
        repository=repository,
        budget=_Budget(decision),
        audit=audit,
        uow=_Uow(),
    )

    kwargs = repository.claims[0][2]
    assert kwargs["status"] == status
    assert kwargs["safe_reason_code"] == reason
    if decision == "blocked":
        assert audit.budget_blocks[0] == {
            "organization_id": occurrence.organization_id,
            "workflow_id": occurrence.workflow_id,
            "claim_id": repository.claims[0][0],
        }
        assert audit.policy == []
    else:
        assert audit.policy[0]["claim_id"] == repository.claims[0][0]


def test_runtime_policy_rejection_uses_canonical_canceled_audit_action():
    occurrence = _occurrence(deployment_type=DeploymentType.WEBAPP)
    repository = _Repository([occurrence])
    budget = _Budget()
    audit = _Audit()

    _use_case().claim_due_occurrences(
        repository=repository,
        budget=budget,
        audit=audit,
        uow=_Uow(),
    )

    assert budget.calls == []
    assert repository.claims[0][2]["status"] == STATUS_CANCELED
    assert audit.policy[0]["action"] == "schedule_dispatch.canceled"


def test_missing_organization_fails_closed_without_cursor_advance():
    repository = _Repository([_occurrence(organization_id=None)])

    count = _use_case().claim_due_occurrences(
        repository=repository,
        budget=_Budget(),
        audit=_Audit(),
        uow=_Uow(),
    )

    assert count == 0
    assert repository.claims == []
    assert repository.advanced == []


def test_disabled_mode_does_not_read_or_commit_occurrences():
    repository = _Repository([_occurrence()])
    uow = _Uow()

    count = _use_case(MODE_DISABLED).claim_due_occurrences(
        repository=repository,
        budget=_Budget(),
        audit=_Audit(),
        uow=uow,
    )

    assert count == 0
    assert uow.commits == 0


def test_uninitialized_schedule_uses_first_fire_calculator():
    occurrence = _occurrence()
    definition = ScheduleDefinitionSnapshot(
        schedule_id=occurrence.schedule_id,
        deployment_id=occurrence.deployment_id,
        organization_id=occurrence.organization_id,
        workflow_id=occurrence.workflow_id,
        deployment_type=occurrence.deployment_type,
        cron_expression=occurrence.cron_expression,
        timezone=occurrence.timezone,
    )
    repository = _Repository(uninitialized=[definition])

    count = _use_case().reconcile_uninitialized(
        repository=repository,
        audit=_Audit(),
        uow=_Uow(),
    )

    assert count == 1
    assert repository.initialized == [(definition.schedule_id, NEXT)]


def test_invalid_uninitialized_schedule_is_durably_quarantined():
    definition = ScheduleDefinitionSnapshot(
        schedule_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_type=DeploymentType.SCHEDULE,
        cron_expression="invalid",
        timezone="UTC",
    )
    repository = _Repository(uninitialized=[definition])
    audit = _Audit()

    count = _use_case(
        next_fire=_NextFire(ScheduleConfigurationError("hidden"))
    ).reconcile_uninitialized(
        repository=repository,
        audit=audit,
        uow=_Uow(),
    )

    assert count == 0
    assert repository.invalidated == [
        (definition.schedule_id, SCHEDULE_CONFIGURATION_INVALID)
    ]
    assert audit.invalid == [
        {
            "organization_id": definition.organization_id,
            "schedule_id": definition.schedule_id,
        }
    ]


def test_invalid_due_schedule_is_durably_quarantined():
    occurrence = _occurrence()
    repository = _Repository([occurrence])
    audit = _Audit()

    count = _use_case(
        next_fire=_NextFire(ScheduleConfigurationError("hidden"))
    ).claim_due_occurrences(
        repository=repository,
        budget=_Budget(),
        audit=audit,
        uow=_Uow(),
    )

    assert count == 0
    assert repository.claims == []
    assert repository.invalidated == [
        (occurrence.schedule_id, SCHEDULE_CONFIGURATION_INVALID)
    ]


def test_occurrence_failure_rolls_back_claim_and_cursor_transaction():
    class _BrokenRepository(_Repository):
        def advance_schedule(self, schedule_id, next_run_at):
            raise RuntimeError("db failure")

    uow = _Uow()
    with pytest.raises(RuntimeError):
        _use_case().claim_due_occurrences(
            repository=_BrokenRepository([_occurrence()]),
            budget=_Budget(),
            audit=_Audit(),
            uow=uow,
        )

    assert uow.commits == 0
    assert uow.rollbacks == 1
