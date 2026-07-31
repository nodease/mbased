from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from apps.gateway.application.deployment.schedule_dispatch import (
    ScheduleDispatchUseCase,
)
from apps.gateway.application.deployment.schedule_models import (
    DispatchCanonicalContext,
    DispatchClaimSnapshot,
    SchedulePublishRequest,
    WorkflowRunVisibilityGap,
)
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
)
from apps.shared.domain.schedule_dispatch import (
    MODE_CLAIM,
    REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
    REASON_ENQUEUE_ATTEMPTS_EXHAUSTED,
    REASON_ORGANIZATION_SCOPE_MISMATCH,
    ScheduleDispatchSettings,
)
from apps.shared.domain.workflow_budget import BudgetExecutionDecision

NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)


def _claim():
    return DispatchClaimSnapshot(
        claim_id=uuid.uuid4(),
        schedule_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        idempotency_key=f"schedule:{uuid.uuid4()}",
        attempt_count=0,
        status="pending",
    )


def _context(claim, **overrides):
    values = {
        "app_exists": True,
        "schedule_id": claim.schedule_id,
        "deployment_id": claim.deployment_id,
        "organization_id": claim.organization_id,
        "workflow_id": uuid.uuid4(),
        "deployment_type": DeploymentType.SCHEDULE,
        "deployment_active": True,
        "deployment_current": True,
        "graph_snapshot": {"nodes": [], "edges": []},
    }
    values.update(overrides)
    return DispatchCanonicalContext(**values)


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
    def __init__(self, claim, context):
        self.claim = claim
        self.context = context
        self.dispatching = []
        self.terminal = []
        self.budget_unavailable = []
        self.publish = []
        self.visibility_gaps = []
        self.reported_visibility_gaps = []

    def database_now(self):
        return NOW

    def lock_dispatch_candidates(self, *, now, limit):
        return (self.claim,)

    def load_canonical_context(self, claim):
        return self.context

    def mark_dispatching(self, claim, **kwargs):
        self.dispatching.append((claim, kwargs))

    def mark_pre_dispatch_terminal(self, claim, **kwargs):
        self.terminal.append((claim, kwargs))

    def mark_budget_unavailable(self, claim, **kwargs):
        self.budget_unavailable.append((claim, kwargs))

    def get_claim_for_publish_result(self, claim_id):
        return replace(
            self.claim,
            attempt_count=max(self.claim.attempt_count, 1),
        )

    def record_publish_accepted(self, **kwargs):
        self.publish.append((True, kwargs))
        return True

    def record_publish_failed(self, **kwargs):
        self.publish.append((False, kwargs))
        return True

    def recover_expired_claims(self, **kwargs):
        return 1, 0

    def quarantine_expired_running(self, **kwargs):
        return 2

    def lock_workflow_run_visibility_gaps(self, **kwargs):
        return tuple(self.visibility_gaps)

    def mark_workflow_run_missing_reported(self, claim_id, **kwargs):
        self.reported_visibility_gaps.append((claim_id, kwargs))

    def cleanup_terminal_claims(self, **kwargs):
        return 3


class _Budget:
    def __init__(self, status="allowed"):
        self.status = status
        self.calls = []

    def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        return BudgetExecutionDecision(status=self.status)


class _ConfigurationPreflight:
    def __init__(self, ready=True):
        self.ready = ready
        self.calls = []

    def is_ready(self, **kwargs):
        self.calls.append(kwargs)
        return self.ready


class _Audit:
    def __init__(self):
        self.events = []
        self.budget_blocks = []

    def record_budget_block(self, **kwargs):
        self.budget_blocks.append(kwargs)

    def record_policy_result(self, **kwargs):
        self.events.append(kwargs)

    def record_schedule_configuration_invalid(self, **kwargs):
        raise AssertionError("not used")

    def record_workflow_run_missing(self, **kwargs):
        self.events.append(kwargs)


def _use_case():
    return ScheduleDispatchUseCase(
        settings=ScheduleDispatchSettings(mode=MODE_CLAIM),
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    )


def test_valid_claim_is_leased_and_prepared_for_deterministic_publish():
    claim = _claim()
    repository = _Repository(claim, _context(claim))

    requests = _use_case().prepare_publish_batch(
        repository=repository,
        budget=_Budget(),
        configuration_preflight=_ConfigurationPreflight(),
        audit=_Audit(),
        uow=_Uow(),
        owner="opaque-owner",
    )

    assert requests.requests[0].claim_id == claim.claim_id
    assert requests.requests[0].task_id == claim.idempotency_key
    assert requests.requests[0].lease_owner == "opaque-owner"
    assert len(repository.dispatching) == 1
    assert repository.dispatching[0][1]["attempt_count"] == 1


def test_dispatch_lease_uses_fresh_db_clock_after_budget_evaluation():
    claim = _claim()
    later = NOW + timedelta(seconds=30)

    class _ClockRepository(_Repository):
        def __init__(self, claim, context):
            super().__init__(claim, context)
            self.times = iter([NOW, NOW + timedelta(seconds=1), later])

        def database_now(self):
            return next(self.times)

    repository = _ClockRepository(claim, _context(claim))

    _use_case().prepare_publish_batch(
        repository=repository,
        budget=_Budget(),
        configuration_preflight=_ConfigurationPreflight(),
        audit=_Audit(),
        uow=_Uow(),
        owner="opaque-owner",
    )

    transition = repository.dispatching[0][1]
    assert transition["now"] == later
    assert transition["lease_expires_at"] > later


def test_canonical_organization_mismatch_cancels_before_publish():
    claim = _claim()
    repository = _Repository(
        claim,
        _context(claim, organization_id=uuid.uuid4()),
    )
    audit = _Audit()

    requests = _use_case().prepare_publish_batch(
        repository=repository,
        budget=_Budget(),
        configuration_preflight=_ConfigurationPreflight(),
        audit=audit,
        uow=_Uow(),
        owner="opaque-owner",
    )

    assert requests.requests == ()
    assert repository.terminal[0][1]["reason"] == REASON_ORGANIZATION_SCOPE_MISMATCH
    assert audit.events[0]["reason"] == REASON_ORGANIZATION_SCOPE_MISMATCH


def test_budget_unavailable_is_not_published():
    claim = _claim()
    repository = _Repository(claim, _context(claim))

    audit = _Audit()
    requests = _use_case().prepare_publish_batch(
        repository=repository,
        budget=_Budget("unavailable"),
        configuration_preflight=_ConfigurationPreflight(),
        audit=audit,
        uow=_Uow(),
        owner="opaque-owner",
    )

    assert requests.requests == ()
    assert len(repository.budget_unavailable) == 1
    assert repository.budget_unavailable[0][1]["attempt_count"] == 1
    assert audit.events[0]["action"] == "schedule_dispatch.deferred"


def test_budget_block_records_canonical_policy_audit():
    claim = _claim()
    context = _context(claim)
    repository = _Repository(claim, context)
    audit = _Audit()

    requests = _use_case().prepare_publish_batch(
        repository=repository,
        budget=_Budget("blocked"),
        configuration_preflight=_ConfigurationPreflight(),
        audit=audit,
        uow=_Uow(),
        owner="opaque-owner",
    )

    assert requests.requests == ()
    assert audit.budget_blocks == [
        {
            "organization_id": claim.organization_id,
            "workflow_id": context.workflow_id,
            "claim_id": claim.claim_id,
        }
    ]


def test_pending_claim_at_attempt_limit_is_dead_lettered_without_publish():
    claim = replace(_claim(), attempt_count=5)
    repository = _Repository(claim, _context(claim))

    audit = _Audit()
    requests = _use_case().prepare_publish_batch(
        repository=repository,
        budget=_Budget(),
        configuration_preflight=_ConfigurationPreflight(),
        audit=audit,
        uow=_Uow(),
        owner="opaque-owner",
    )

    assert requests.requests == ()
    assert requests.enqueue_attempts_exhausted == 1
    assert repository.dispatching == []
    assert repository.terminal[0][1] == {
        "status": "dead_lettered",
        "reason": REASON_ENQUEUE_ATTEMPTS_EXHAUSTED,
        "now": NOW,
    }
    assert audit.events[0]["action"] == "schedule_dispatch.failed"


def test_final_budget_unavailable_attempt_is_dead_lettered_at_exact_limit():
    claim = replace(_claim(), attempt_count=4)
    repository = _Repository(claim, _context(claim))

    audit = _Audit()
    requests = _use_case().prepare_publish_batch(
        repository=repository,
        budget=_Budget("unavailable"),
        configuration_preflight=_ConfigurationPreflight(),
        audit=audit,
        uow=_Uow(),
        owner="opaque-owner",
    )

    assert requests.requests == ()
    assert requests.budget_evaluation_failed == 1
    transition = repository.budget_unavailable[0][1]
    assert transition["attempt_count"] == 5
    assert transition["exhausted"] is True
    assert transition["next_attempt_at"] is None
    assert audit.events[0]["action"] == "schedule_dispatch.failed"


def test_publish_result_is_recorded_in_separate_transaction():
    claim = _claim()
    repository = _Repository(claim, _context(claim))
    request = (
        _use_case()
        .prepare_publish_batch(
            repository=repository,
            budget=_Budget(),
            configuration_preflight=_ConfigurationPreflight(),
            audit=_Audit(),
            uow=_Uow(),
            owner="opaque-owner",
        )
        .requests[0]
    )
    result_uow = _Uow()

    result = _use_case().record_publish_result(
        repository=repository,
        uow=result_uow,
        request=request,
        accepted=True,
    )

    assert result.changed is True
    assert result.dead_lettered_reason is None
    assert repository.publish[0][0] is True
    assert result_uow.commits == 1


def test_publish_deadline_clock_is_read_after_claim_lock():
    claim = _claim()
    events = []

    class _OrderedRepository(_Repository):
        def get_claim_for_publish_result(self, claim_id):
            events.append("lock")
            return super().get_claim_for_publish_result(claim_id)

        def database_now(self):
            events.append("clock")
            return NOW

        def record_publish_accepted(self, **kwargs):
            events.append("accepted")
            return super().record_publish_accepted(**kwargs)

    repository = _OrderedRepository(claim, _context(claim))
    request = (
        _use_case()
        .prepare_publish_batch(
            repository=repository,
            budget=_Budget(),
            configuration_preflight=_ConfigurationPreflight(),
            audit=_Audit(),
            uow=_Uow(),
            owner="opaque-owner",
        )
        .requests[0]
    )
    events.clear()

    _use_case().record_publish_result(
        repository=repository,
        uow=_Uow(),
        request=request,
        accepted=True,
    )

    assert events == ["lock", "clock", "accepted"]


def test_configuration_preflight_block_cancels_before_budget_and_publish():
    claim = _claim()
    context = _context(claim)
    repository = _Repository(claim, context)
    preflight = _ConfigurationPreflight(ready=False)
    budget = _Budget()
    audit = _Audit()

    batch = _use_case().prepare_publish_batch(
        repository=repository,
        budget=budget,
        configuration_preflight=preflight,
        audit=audit,
        uow=_Uow(),
        owner="opaque-owner",
    )

    assert batch.requests == ()
    assert budget.calls == []
    assert repository.dispatching == []
    assert repository.terminal[0][1]["reason"] == (
        REASON_CONFIGURATION_PREFLIGHT_BLOCKED
    )
    assert audit.events[0] == {
        "organization_id": claim.organization_id,
        "claim_id": claim.claim_id,
        "action": "schedule_dispatch.canceled",
        "reason": REASON_CONFIGURATION_PREFLIGHT_BLOCKED,
    }
    assert preflight.calls == [
        {
            "graph_snapshot": context.graph_snapshot,
            "organization_id": claim.organization_id,
        }
    ]


def test_configuration_preflight_infrastructure_failure_rolls_back():
    claim = _claim()
    repository = _Repository(claim, _context(claim))
    uow = _Uow()

    class _FailingPreflight:
        def is_ready(self, **kwargs):
            raise RuntimeError("preflight unavailable")

    with pytest.raises(RuntimeError, match="preflight unavailable"):
        _use_case().prepare_publish_batch(
            repository=repository,
            budget=_Budget(),
            configuration_preflight=_FailingPreflight(),
            audit=_Audit(),
            uow=uow,
            owner="opaque-owner",
        )

    assert uow.commits == 0
    assert uow.rollbacks == 1
    assert repository.dispatching == []
    assert repository.terminal == []


def test_terminal_publish_failure_returns_dead_letter_reason():
    claim = replace(_claim(), attempt_count=5, status="dispatching")
    repository = _Repository(claim, _context(claim))
    request = SchedulePublishRequest(
        claim_id=claim.claim_id,
        task_id=claim.idempotency_key,
        lease_owner="opaque-owner",
    )

    result = _use_case().record_publish_result(
        repository=repository,
        uow=_Uow(),
        request=request,
        accepted=False,
    )

    assert result.changed is True
    assert result.dead_lettered_reason == REASON_ENQUEUE_ATTEMPTS_EXHAUSTED


def test_ambiguous_publish_failure_after_early_admission_is_not_dead_letter_signal():
    claim = replace(_claim(), attempt_count=5, status="running")
    repository = _Repository(claim, _context(claim))
    request = SchedulePublishRequest(
        claim_id=claim.claim_id,
        task_id=claim.idempotency_key,
        lease_owner="opaque-owner",
    )

    result = _use_case().record_publish_result(
        repository=repository,
        uow=_Uow(),
        request=request,
        accepted=False,
    )

    assert result.changed is True
    assert result.dead_lettered_reason is None


def test_critical_recovery_scans_delivery_and_execution_deadlines():
    claim = _claim()
    repository = _Repository(claim, _context(claim))

    recovery = _use_case().recover_critical(
        repository=repository,
        uow=_Uow(),
    )
    assert recovery.retried == 1
    assert recovery.enqueue_attempts_exhausted == 0
    assert recovery.execution_outcome_unknown == 2

    assert (
        _use_case().cleanup_terminal_claims(
            repository=repository,
            uow=_Uow(),
        )
        == 3
    )


def test_recovery_reports_each_missing_workflow_run_once_without_replay():
    claim = _claim()
    repository = _Repository(claim, _context(claim))
    repository.visibility_gaps = [
        WorkflowRunVisibilityGap(
            claim_id=claim.claim_id,
            organization_id=claim.organization_id,
            workflow_run_id=uuid.uuid4(),
        )
    ]
    audit = _Audit()

    result = _use_case().maintain_visibility(
        repository=repository,
        audit=audit,
        uow=_Uow(),
    )

    assert result == 1
    assert audit.events[-1] == {
        "organization_id": claim.organization_id,
        "claim_id": claim.claim_id,
    }
    assert repository.reported_visibility_gaps[0][0] == claim.claim_id
