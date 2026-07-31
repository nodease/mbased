from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4


class _FirstQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.value


def _policy(*, strategy_id: str = "judge_bootstrap_incremental_v1"):
    return SimpleNamespace(
        id=uuid4(),
        deployment_id=uuid4(),
        workflow_id=uuid4(),
        node_id="llm-1",
        organization_id=uuid4(),
        judge_user_id=uuid4(),
        execution_subject_user_id=uuid4(),
        active_policy={
            "strategy_id": strategy_id,
            "default_model_id": "gpt-4.1",
            "fallback_model_id": "gpt-4.1-mini",
        },
        learner_id=uuid4(),
        active_learner_version_id=uuid4(),
        pending_policy=None,
        policy_version="judge-first-v1",
        refresh_every_runs=20,
        eligible_runs_since_last_refresh=20,
        refresh_requested_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
        last_refreshed_at=None,
        last_refresh_result=None,
        performance_checkpoint={},
        status="refreshing",
        enabled=True,
    )


def _deployment(*, enabled: bool = True):
    return SimpleNamespace(
        graph_snapshot={
            "nodes": [
                {
                    "id": "llm-1",
                    "data": {
                        "auto_model_routing": enabled,
                        "model_id": "gpt-4.1",
                        "fallback_model_id": "gpt-4.1-mini",
                    },
                }
            ]
        }
    )


def _db(policy, deployment):
    db = MagicMock()
    db.query.side_effect = [_FirstQuery(policy), _FirstQuery(deployment)]
    return db


def test_refresh_reads_learner_summary_without_embedding_it_in_policy():
    from apps.workflow_engine.services.model_routing_policy_refresh_task import (
        PersistedModelRoutingPolicyRefreshService,
    )

    policy = _policy()
    db = _db(policy, _deployment())
    profile = SimpleNamespace(operational_usable_runs=24)

    with (
        patch(
            "apps.workflow_engine.services.model_routing_learner_store."
            "ModelRoutingLearnerStore.runtime_snapshot",
            return_value={
                "id": str(policy.learner_id),
                "mode": "local_first",
                "judged_request_count": 50,
            },
        ),
        patch(
            "apps.workflow_engine.services.model_routing_policy_refresh_task."
            "ModelRoutingOperationalPerformanceService.profile_for_policy",
            return_value=profile,
        ),
        patch(
            "apps.workflow_engine.services.model_routing_policy_refresh_task."
            "ModelRoutingOperationalPerformanceService.checkpoint_snapshot",
            return_value={"total_runs": 24},
        ),
        patch.object(
            PersistedModelRoutingPolicyRefreshService,
            "_remaining_event_count",
            return_value=1,
        ),
        patch.object(
            PersistedModelRoutingPolicyRefreshService,
            "_excluded_run_count",
            return_value=0,
        ),
    ):
        update = PersistedModelRoutingPolicyRefreshService.refresh(
            db,
            policy_id=policy.id,
            trigger="auto_runs",
        )

    assert update.status == "kept_current"
    assert policy.active_policy["strategy_id"] == "judge_bootstrap_incremental_v1"
    assert "learning" not in policy.active_policy
    assert policy.active_policy["default_model_id"] == "gpt-4.1"
    assert update.output_summary["local_router_ready"] is True


def test_refresh_detaches_local_version_when_operational_contract_degrades():
    from apps.workflow_engine.services.model_router import ModelPerformance, NodeRunProfile
    from apps.workflow_engine.services.model_routing_policy_refresh_task import (
        PersistedModelRoutingPolicyRefreshService,
    )

    policy = _policy()
    db = _db(policy, _deployment())
    profile = NodeRunProfile(
        operational_usable_runs=20,
        model_performance={
            "gpt-4.1-mini": ModelPerformance(
                model_id="gpt-4.1-mini",
                run_count=20,
                success_count=17,
                downstream_eval_count=20,
                downstream_success_count=17,
                fallback_count=2,
            )
        },
    )

    with (
        patch(
            "apps.workflow_engine.services.model_routing_learner_store."
            "ModelRoutingLearnerStore.runtime_snapshot",
            return_value={
                "id": str(policy.learner_id),
                "mode": "local_first",
                "judged_request_count": 50,
            },
        ),
        patch(
            "apps.workflow_engine.services.model_routing_policy_refresh_task."
            "ModelRoutingOperationalPerformanceService.profile_for_policy",
            return_value=profile,
        ),
        patch(
            "apps.workflow_engine.services.model_routing_policy_refresh_task."
            "ModelRoutingOperationalPerformanceService.checkpoint_snapshot",
            return_value={"total_runs": 20},
        ),
        patch.object(
            PersistedModelRoutingPolicyRefreshService,
            "_remaining_event_count",
            return_value=0,
        ),
        patch.object(
            PersistedModelRoutingPolicyRefreshService,
            "_excluded_run_count",
            return_value=0,
        ),
    ):
        update = PersistedModelRoutingPolicyRefreshService.refresh(
            db,
            policy_id=policy.id,
            trigger="auto_runs",
        )

    assert policy.active_learner_version_id is None
    assert update.output_summary["local_router_ready"] is False
    assert update.output_summary["learning_mode"] == "judge_first"
    assert update.output_summary["reason_code"] == "operational_contract_degraded"


def test_refresh_migrates_legacy_policy_without_reusing_legacy_rules():
    from apps.workflow_engine.services.model_routing_policy_refresh_task import (
        PersistedModelRoutingPolicyRefreshService,
    )

    policy = _policy(strategy_id="prior_guided_adaptive_v1")
    policy.active_policy["rules"] = [{"when": {"keyword_any": ["SLA"]}}]
    db = _db(policy, _deployment())
    profile = SimpleNamespace(operational_usable_runs=3)

    with (
        patch(
            "apps.workflow_engine.services.model_routing_policy_refresh_task."
            "LLMService.get_runtime_available_model_ids_for_user",
            return_value=["gpt-4.1", "gpt-4.1-mini"],
        ),
        patch(
            "apps.workflow_engine.services.model_routing_policy_refresh_task."
            "ModelRoutingOperationalPerformanceService.profile_for_policy",
            return_value=profile,
        ),
        patch(
            "apps.workflow_engine.services.model_routing_policy_refresh_task."
            "ModelRoutingOperationalPerformanceService.checkpoint_snapshot",
            return_value={"total_runs": 3},
        ),
        patch.object(
            PersistedModelRoutingPolicyRefreshService,
            "_remaining_event_count",
            return_value=0,
        ),
        patch.object(
            PersistedModelRoutingPolicyRefreshService,
            "_excluded_run_count",
            return_value=0,
        ),
        patch(
            "apps.workflow_engine.services.model_routing_learner_store."
            "ModelRoutingLearnerStore.runtime_snapshot",
            return_value={
                "id": str(policy.learner_id),
                "mode": "judge_first",
                "judged_request_count": 0,
            },
        ),
    ):
        update = PersistedModelRoutingPolicyRefreshService.refresh(
            db,
            policy_id=policy.id,
            trigger="manual_refresh",
        )

    assert update.status == "kept_current"
    assert policy.active_policy["strategy_id"] == "judge_bootstrap_incremental_v1"
    assert "rules" not in policy.active_policy
    assert "learning" not in policy.active_policy


def test_refresh_keeps_policy_when_auto_routing_is_off():
    from apps.workflow_engine.services.model_routing_policy_refresh_task import (
        PersistedModelRoutingPolicyRefreshService,
    )

    policy = _policy()
    db = _db(policy, _deployment(enabled=False))
    with patch.object(
        PersistedModelRoutingPolicyRefreshService,
        "_remaining_event_count",
        return_value=0,
    ):
        update = PersistedModelRoutingPolicyRefreshService.refresh(
            db,
            policy_id=policy.id,
            trigger="manual_refresh",
        )

    assert update.status == "kept_current"
    assert policy.active_policy["strategy_id"] == "judge_bootstrap_incremental_v1"
    assert policy.refresh_requested_at is None


def test_legacy_migration_fails_closed_without_execution_subject():
    from apps.workflow_engine.services.model_routing_policy_refresh_task import (
        PersistedModelRoutingPolicyRefreshService,
    )

    policy = _policy(strategy_id="prior_guided_adaptive_v1")
    policy.execution_subject_user_id = None
    policy.judge_user_id = None
    db = _db(policy, _deployment())

    update = PersistedModelRoutingPolicyRefreshService.refresh(
        db,
        policy_id=policy.id,
        trigger="manual_refresh",
    )

    assert update.status == "failed"
    assert update.error_code == "ValueError"
    assert policy.active_policy["strategy_id"] == "prior_guided_adaptive_v1"
