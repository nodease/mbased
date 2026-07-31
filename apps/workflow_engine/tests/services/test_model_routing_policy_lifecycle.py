from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def test_policy_run_event_is_counted_once_per_workflow_run():
    from apps.workflow_engine.services.model_routing_policy_lifecycle import (
        ModelRoutingPolicyLifecycleService,
    )

    policy = SimpleNamespace(
        enabled=True,
        status="collecting",
        eligible_runs_since_last_refresh=0,
        refresh_every_runs=20,
        refresh_requested_at=None,
    )

    first = ModelRoutingPolicyLifecycleService.apply_run_event(
        policy,
        event_was_created=True,
    )
    duplicate = ModelRoutingPolicyLifecycleService.apply_run_event(
        policy,
        event_was_created=False,
    )

    assert first.should_enqueue_refresh is False
    assert duplicate.should_enqueue_refresh is False
    assert policy.eligible_runs_since_last_refresh == 1


def test_policy_schedules_refresh_exactly_when_threshold_is_reached():
    from apps.workflow_engine.services.model_routing_policy_lifecycle import (
        ModelRoutingPolicyLifecycleService,
    )

    policy = SimpleNamespace(
        enabled=True,
        status="collecting",
        eligible_runs_since_last_refresh=19,
        refresh_every_runs=20,
        refresh_requested_at=None,
    )

    reached = ModelRoutingPolicyLifecycleService.apply_run_event(
        policy,
        event_was_created=True,
    )
    while_refreshing = ModelRoutingPolicyLifecycleService.apply_run_event(
        policy,
        event_was_created=True,
    )

    assert reached.should_enqueue_refresh is True
    assert policy.status == "refreshing"
    assert policy.eligible_runs_since_last_refresh == 21
    assert while_refreshing.should_enqueue_refresh is False


def test_stale_refresh_request_is_requeued_by_next_operational_event():
    from apps.workflow_engine.services.model_routing_policy_lifecycle import (
        ModelRoutingPolicyLifecycleService,
    )

    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    policy = SimpleNamespace(
        enabled=True,
        status="refreshing",
        eligible_runs_since_last_refresh=20,
        refresh_every_runs=20,
        refresh_requested_at=now - timedelta(minutes=11),
    )

    outcome = ModelRoutingPolicyLifecycleService.apply_run_event(
        policy,
        event_was_created=True,
        now=now,
    )

    assert outcome.should_enqueue_refresh is True
    assert policy.refresh_requested_at == now
    assert policy.eligible_runs_since_last_refresh == 21


def test_recent_refresh_request_is_not_enqueued_twice():
    from apps.workflow_engine.services.model_routing_policy_lifecycle import (
        ModelRoutingPolicyLifecycleService,
    )

    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    requested_at = now - timedelta(minutes=2)
    policy = SimpleNamespace(
        enabled=True,
        status="refreshing",
        eligible_runs_since_last_refresh=20,
        refresh_every_runs=20,
        refresh_requested_at=requested_at,
    )

    outcome = ModelRoutingPolicyLifecycleService.apply_run_event(
        policy,
        event_was_created=True,
        now=now,
    )

    assert outcome.should_enqueue_refresh is False
    assert policy.refresh_requested_at == requested_at


@pytest.mark.parametrize(
    ("deployment_id", "trigger_mode", "status", "expected"),
    [
        ("deployment-1", "webhook", "success", True),
        ("deployment-1", "api", "failed", True),
        ("deployment-1", "webhook", "running", False),
        (None, "manual", "success", False),
        ("deployment-1", "manual", "success", False),
        (None, "webhook", "success", False),
    ],
)
def test_only_deployed_operational_runs_are_eligible(
    deployment_id, trigger_mode, status, expected
):
    from apps.workflow_engine.services.model_routing_policy_lifecycle import (
        ModelRoutingPolicyLifecycleService,
    )

    run = SimpleNamespace(
        deployment_id=deployment_id,
        trigger_mode=trigger_mode,
        status=status,
    )

    assert ModelRoutingPolicyLifecycleService.is_eligible_operational_run(run) is expected


def test_pending_result_keeps_existing_active_policy():
    from apps.workflow_engine.services.model_routing_policy_lifecycle import (
        ModelRoutingPolicyLifecycleService,
    )

    policy = SimpleNamespace(
        active_policy={"default_model_id": "gpt-4.1", "rules": []},
        pending_policy=None,
        policy_version="router-policy-v1",
        status="refreshing",
        last_refresh_result=None,
    )
    proposed = {"default_model_id": "gpt-4.1-mini", "rules": []}

    ModelRoutingPolicyLifecycleService.apply_refresh_result(
        policy,
        status="pending_review",
        proposed_policy=proposed,
        policy_version="router-policy-v2",
    )

    assert policy.active_policy["default_model_id"] == "gpt-4.1"
    assert policy.pending_policy == proposed
    assert policy.policy_version == "router-policy-v1"
    assert policy.status == "pending_review"


def test_completed_validation_releases_refresh_lease_and_keeps_runs_arriving_during_validation():
    """검증 중 들어온 운영 run은 다음 갱신 주기를 위한 카운터로 보존한다."""
    from apps.workflow_engine.services.model_routing_policy_lifecycle import (
        ModelRoutingPolicyLifecycleService,
    )

    requested_at = datetime(2026, 7, 14, tzinfo=timezone.utc)
    policy = SimpleNamespace(
        refresh_requested_at=requested_at,
        eligible_runs_since_last_refresh=27,
    )

    ModelRoutingPolicyLifecycleService.complete_refresh_cycle(
        policy,
        eligible_runs_since_last_refresh=7,
    )

    assert policy.refresh_requested_at is None
    assert policy.eligible_runs_since_last_refresh == 7
