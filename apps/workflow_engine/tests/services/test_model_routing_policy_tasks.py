from unittest.mock import MagicMock, patch
from uuid import uuid4


def test_operational_run_task_dispatches_refresh_for_due_policy():
    """운영 run 집계 결과가 due policy를 반환하면 refresh task를 dispatch한다."""
    from apps.workflow_engine import tasks

    policy_id = uuid4()
    learner_id = uuid4()
    session = MagicMock()
    sent = []

    with (
        patch.object(tasks, "SessionLocal", return_value=session),
        patch.object(
            tasks.celery_app,
            "send_task",
            side_effect=lambda *args, **kwargs: sent.append((args, kwargs)),
        ),
        patch(
            "apps.workflow_engine.services.model_routing_policy_store.ModelRoutingPolicyStore.record_completed_deployed_run",
            return_value=[policy_id],
        ),
        patch(
            "apps.workflow_engine.services.model_routing_policy_store.ModelRoutingPolicyStore.pending_learning_learner_ids_for_run",
            return_value=[learner_id],
        ),
    ):
        result = tasks.record_model_routing_operational_run.__wrapped__(str(uuid4()))

    assert result == {"status": "success", "scheduled_policy_ids": [str(policy_id)]}
    session.commit.assert_called_once()
    assert sent == [
        (
            ("workflow.model_routing.train_local_router",),
            {
                "args": [str(learner_id), False],
                "kwargs": {},
                "argsrepr": "[workflow arguments redacted]",
                "kwargsrepr": "{workflow arguments redacted}",
            },
        ),
        (
            ("workflow.model_routing.refresh_policy",),
            {
                "args": [str(policy_id), "score_change"],
                "kwargs": {},
                "argsrepr": "[workflow arguments redacted]",
                "kwargsrepr": "{workflow arguments redacted}",
            },
        )
    ]


def test_local_router_training_task_defers_a_small_batch_without_blocking_run():
    from apps.workflow_engine import tasks
    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchResult,
        ModelRoutingLearningBatchService,
    )

    learner_id = uuid4()
    session = MagicMock()
    sent = []
    result = ModelRoutingLearningBatchResult(
        learner_state={},
        processed_count=0,
        remaining_count=4,
        deferred_seconds=300,
    )

    with (
        patch.object(tasks, "SessionLocal", return_value=session),
        patch.object(
            ModelRoutingLearningBatchService,
            "train_pending",
            return_value=result,
        ),
        patch.object(
            tasks.celery_app,
            "send_task",
            side_effect=lambda *args, **kwargs: sent.append((args, kwargs)),
        ),
    ):
        task_result = tasks.train_model_routing_local_router.__wrapped__(
            str(learner_id), False
        )

    session.commit.assert_called_once()
    assert task_result["status"] == "deferred"
    assert sent[0][0] == ("workflow.model_routing.train_local_router",)
    assert sent[0][1]["args"] == [str(learner_id), True]
    assert sent[0][1]["countdown"] == 300


def test_duplicate_auto_refresh_delivery_is_skipped_after_request_is_consumed():
    """publish 재시도로 같은 auto refresh가 다시 와도 judge를 다시 호출하지 않는다."""
    from apps.workflow_engine import tasks
    from apps.workflow_engine.services.model_routing_policy_store import (
        ModelRoutingPolicyStore,
    )
    from apps.workflow_engine.services.model_routing_policy_refresh_task import (
        PersistedModelRoutingPolicyRefreshService,
    )

    session = MagicMock()
    policy_id = uuid4()

    with (
        patch.object(tasks, "SessionLocal", return_value=session),
        patch.object(
            ModelRoutingPolicyStore,
            "claim_pending_auto_refresh",
            return_value=None,
        ) as claim_pending,
        patch.object(PersistedModelRoutingPolicyRefreshService, "refresh") as refresh,
    ):
        result = tasks.refresh_model_routing_policy.__wrapped__(
            str(policy_id), "score_change"
        )

    assert result == {"status": "skipped", "update_id": None, "result": None}
    claim_pending.assert_called_once_with(session, policy_id=str(policy_id))
    refresh.assert_not_called()


def test_deployment_bootstrap_task_reconciles_judge_first_policy_without_extra_task():
    """배포 직후 Judge-first 정책을 정합화하고 별도 검증 task는 예약하지 않는다."""
    from apps.workflow_engine import tasks
    from apps.workflow_engine.services.model_routing_policy_refresh_task import (
        PersistedModelRoutingPolicyRefreshService,
    )

    session = MagicMock()
    policy_id = uuid4()
    update_id = uuid4()
    update = MagicMock(id=update_id, status="applied")
    sent = []

    with (
        patch.object(tasks, "SessionLocal", return_value=session),
        patch.object(
            PersistedModelRoutingPolicyRefreshService,
            "refresh",
            return_value=update,
        ) as refresh,
        patch.object(
            tasks.celery_app,
            "send_task",
            side_effect=lambda *args, **kwargs: sent.append((args, kwargs)),
        ),
    ):
        result = tasks.bootstrap_model_routing_policy.__wrapped__(str(policy_id))

    refresh.assert_called_once_with(
        session,
        policy_id=str(policy_id),
        trigger="deployment_bootstrap",
    )
    session.commit.assert_called_once()
    assert result == {
        "status": "applied",
        "policy_id": str(policy_id),
        "update_id": str(update_id),
    }
    assert sent == []
