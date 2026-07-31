from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4


def _label(index: int):
    simple = index % 2 == 0
    return SimpleNamespace(
        id=f"label-{index}",
        status="accepted",
        feature_vector=[1.0, 0.0] if simple else [0.0, 1.0],
        encoder_model_id="test-encoder",
        selected_model_id="gpt-4o-mini" if simple else "gpt-5-mini",
        candidate_model_ids=["gpt-4o-mini", "gpt-5-mini"],
        confidence=0.9,
        reason_code="judge_selected",
        task_requirements={
            "task_complexity": 0 if simple else 3,
            "decision_impact": 0 if simple else 3,
            "evidence_synthesis": 0 if simple else 2,
        },
        local_prediction=None,
        local_confidence=None,
        local_distance_score=None,
        local_margin=None,
        routing_feature_hash=f"feature-{index}",
        learning_processed_at=None,
    )


def test_judge_contract_hash_changes_when_learning_contract_changes():
    from apps.workflow_engine.services.model_routing_learner_store import (
        ModelRoutingLearnerStore,
    )

    first = ModelRoutingLearnerStore.judge_contract_hash(
        judge_rubric_version="routing-requirements-v2",
        feature_schema_version="grouped-runtime-v4",
        encoder_model_id="intfloat/multilingual-e5-base",
    )
    same = ModelRoutingLearnerStore.judge_contract_hash(
        judge_rubric_version="routing-requirements-v2",
        feature_schema_version="grouped-runtime-v4",
        encoder_model_id="intfloat/multilingual-e5-base",
    )
    changed = ModelRoutingLearnerStore.judge_contract_hash(
        judge_rubric_version="routing-requirements-v3",
        feature_schema_version="grouped-runtime-v4",
        encoder_model_id="intfloat/multilingual-e5-base",
    )

    assert first == same
    assert first != changed
    assert len(first) == 64


def test_same_task_and_contract_reuses_learner_across_deployments():
    from apps.workflow_engine.services.model_routing_learner_store import (
        ModelRoutingLearnerStore,
    )

    existing = SimpleNamespace(id=uuid4())
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = existing
    db = MagicMock()
    db.query.return_value = query

    result = ModelRoutingLearnerStore.get_or_create(
        db,
        organization_id=uuid4(),
        workflow_id=uuid4(),
        node_id="llm-1",
        node_data={
            "system_prompt": "정책을 요약하세요.",
            "output_format": {"type": "text"},
        },
        downstream_contract={"consumers": []},
    )

    assert result is existing
    db.add.assert_not_called()


def test_learning_batch_updates_learner_state_without_policy_json():
    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchService,
    )

    learner_state = {
        "mode": "judge_first",
        "judged_request_count": 0,
        "local_confidence_threshold": 0.78,
    }
    result = ModelRoutingLearningBatchService.train_labels(
        learner_state=deepcopy(learner_state),
        labels=[_label(index) for index in range(10)],
    )

    assert result.processed_count == 10
    assert result.learner_state["judged_request_count"] == 10
    assert result.learner_state["candidate_requirement_artifact"][
        "trained_example_count"
    ] == 10
    assert "active_policy" not in result.__dict__


def test_judge_first_policy_does_not_embed_learning_artifact():
    from apps.workflow_engine.services.model_routing_judge_first_policy import (
        build_judge_first_active_policy,
    )

    policy = build_judge_first_active_policy(
        policy_version="routing-policy-v1",
        default_model_id="gpt-4.1",
        fallback_model_id="gpt-4.1-mini",
        candidate_model_ids=["gpt-4.1", "gpt-4.1-mini"],
    )

    assert "learning" not in policy


def test_quality_gate_publishes_immutable_version_and_attaches_active_policies():
    from apps.shared.db.models.model_routing_policy import (
        LLMNodeModelRoutingLearnerVersion,
    )
    from apps.workflow_engine.services.model_routing_learner_store import (
        ModelRoutingLearnerStore,
    )

    learner = SimpleNamespace(
        id=uuid4(),
        status="collecting",
        judged_request_count=100,
        candidate_artifact={
            "kind": "multilingual_e5_task_requirements_ordinal_v2",
            "weights": {"task_complexity": [0.1, 0.2]},
        },
        recent_evaluation={
            "sample_count": 50,
            "judge_match_rate": 0.8,
            "axis_accuracies": {
                "task_complexity": 0.9,
                "decision_impact": 0.9,
                "evidence_synthesis": 0.9,
            },
            "axis_mean_errors": {
                "task_complexity": 0.2,
                "decision_impact": 0.1,
                "evidence_synthesis": 0.3,
            },
            "judge_label_diversity": 3,
            "local_prediction_diversity": 2,
            "contract_pass_rate": 1.0,
            "high_risk_underestimation_count": 0,
        },
    )
    policy = SimpleNamespace(
        id=uuid4(),
        enabled=True,
        active_learner_version_id=None,
        policy_version="routing-policy-v1",
    )
    version_query = MagicMock()
    version_query.filter.return_value.filter.return_value.first.return_value = None
    policy_query = MagicMock()
    policy_query.filter.return_value.filter.return_value.all.return_value = [policy]
    db = MagicMock()
    db.query.side_effect = [version_query, policy_query]

    def assign_version_id(value):
        if isinstance(value, LLMNodeModelRoutingLearnerVersion) and value.id is None:
            value.id = uuid4()

    db.add.side_effect = assign_version_id
    with (
        patch.object(ModelRoutingLearnerStore, "latest_version", return_value=None),
        patch.object(
            ModelRoutingLearnerStore,
            "_outcome_rates",
            return_value={
                "success_rate": 1.0,
                "schema_pass_rate": 1.0,
                "downstream_success_rate": 1.0,
                "fallback_rate": 0.0,
            },
        ),
    ):
        version = ModelRoutingLearnerStore.publish_if_qualified(
            db,
            learner=learner,
        )

    assert version is not None
    assert version.version == 1
    assert version.sample_count == 100
    assert version.evaluation_summary["operational_contract"]["success_rate"] == 1.0
    assert learner.status == "ready"
    assert policy.active_learner_version_id == version.id
    assert policy.policy_version == "routing-policy-learner-v1"


def test_quality_gate_reactivates_matching_immutable_version_without_duplicate():
    from apps.shared.db.models.model_routing_policy import (
        LLMNodeModelRoutingLearnerVersion,
    )
    from apps.workflow_engine.services.model_routing_learner_store import (
        ModelRoutingLearnerStore,
    )

    artifact = {
        "kind": "multilingual_e5_task_requirements_ordinal_v2",
        "weights": {"task_complexity": [0.1, 0.2]},
    }
    artifact_hash = hashlib.sha256(
        json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    learner = SimpleNamespace(
        id=uuid4(),
        status="collecting",
        judged_request_count=120,
        candidate_artifact=artifact,
        recent_evaluation={
            "sample_count": 50,
            "judge_match_rate": 0.9,
            "axis_accuracies": {
                "task_complexity": 0.9,
                "decision_impact": 0.9,
                "evidence_synthesis": 0.9,
            },
            "axis_mean_errors": {
                "task_complexity": 0.1,
                "decision_impact": 0.1,
                "evidence_synthesis": 0.1,
            },
            "judge_label_diversity": 3,
            "local_prediction_diversity": 3,
            "contract_pass_rate": 1.0,
            "high_risk_underestimation_count": 0,
        },
    )
    previous_matching = SimpleNamespace(
        id=uuid4(),
        learner_id=learner.id,
        version=1,
        artifact_hash=artifact_hash,
    )
    current = SimpleNamespace(
        id=uuid4(),
        learner_id=learner.id,
        version=2,
        artifact_hash="different-hash",
    )
    policy = SimpleNamespace(
        id=uuid4(),
        enabled=True,
        active_learner_version_id=current.id,
        policy_version="routing-policy-learner-v2",
    )
    version_query = MagicMock()
    version_query.filter.return_value.filter.return_value.first.return_value = (
        previous_matching
    )
    policy_query = MagicMock()
    policy_query.filter.return_value.filter.return_value.all.return_value = [policy]
    db = MagicMock()
    db.query.side_effect = [version_query, policy_query]

    with (
        patch.object(ModelRoutingLearnerStore, "latest_version", return_value=current),
        patch.object(
            ModelRoutingLearnerStore,
            "_outcome_rates",
            return_value={
                "success_rate": 1.0,
                "schema_pass_rate": 1.0,
                "downstream_success_rate": 1.0,
                "fallback_rate": 0.0,
            },
        ),
    ):
        version = ModelRoutingLearnerStore.publish_if_qualified(
            db,
            learner=learner,
        )

    assert version is previous_matching
    assert policy.active_learner_version_id == previous_matching.id
    assert policy.policy_version == "routing-policy-learner-v1"
    assert not any(
        isinstance(call.args[0], LLMNodeModelRoutingLearnerVersion)
        for call in db.add.call_args_list
    )


def test_schema_not_applicable_is_neutral_for_learner_version_gate():
    from apps.workflow_engine.services.model_routing_learner_store import (
        ModelRoutingLearnerStore,
    )

    labels = [
        SimpleNamespace(
            execution_succeeded=True,
            schema_status="not_applicable",
            downstream_status="passed",
            fallback_used=False,
        )
        for _ in range(50)
    ]
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = labels
    db = MagicMock()
    db.query.return_value = query

    rates = ModelRoutingLearnerStore._outcome_rates(db, learner_id=uuid4())

    assert rates["schema_pass_rate"] == 1.0
    assert rates["success_rate"] == 1.0
    assert rates["downstream_success_rate"] == 1.0
    assert rates["fallback_rate"] == 0.0


def test_quality_gate_does_not_publish_an_empty_artifact():
    from apps.workflow_engine.services.model_routing_learner_store import (
        ModelRoutingLearnerStore,
    )

    learner = SimpleNamespace(
        id=uuid4(),
        status="collecting",
        judged_request_count=50,
        candidate_artifact={},
        recent_evaluation={
            "sample_count": 20,
            "judge_match_rate": 1.0,
            "axis_mean_errors": {
                "task_complexity": 0.0,
                "decision_impact": 0.0,
                "evidence_synthesis": 0.0,
            },
            "judge_label_diversity": 2,
            "local_prediction_diversity": 2,
            "contract_pass_rate": 1.0,
        },
    )
    with patch.object(
        ModelRoutingLearnerStore,
        "_outcome_rates",
        return_value={
            "success_rate": 1.0,
            "schema_pass_rate": 1.0,
            "downstream_success_rate": 1.0,
            "fallback_rate": 0.0,
        },
    ):
        version = ModelRoutingLearnerStore.publish_if_qualified(
            MagicMock(),
            learner=learner,
        )

    assert version is None
    assert learner.status == "collecting"


def test_runtime_snapshot_does_not_reactivate_a_detached_version():
    from apps.workflow_engine.services.model_routing_learner_store import (
        ModelRoutingLearnerStore,
    )

    learner_id = uuid4()
    learner = SimpleNamespace(
        id=learner_id,
        status="degraded",
        task_fingerprint="task-fingerprint",
        judged_request_count=60,
        local_confidence_threshold=0.78,
        recent_evaluation={"contract_pass_rate": 0.8},
    )
    learner_query = MagicMock()
    learner_query.filter.return_value.first.return_value = learner
    db = MagicMock()
    db.query.return_value = learner_query

    snapshot = ModelRoutingLearnerStore.runtime_snapshot(
        db,
        learner_id=learner_id,
        version_id=None,
    )

    assert snapshot is not None
    assert snapshot["mode"] == "judge_first"
    assert snapshot["active_version"] is None
    assert snapshot["local_requirement_artifact"] is None
    assert db.query.call_count == 1


def test_queue_runtime_label_does_not_lock_learner_during_embedding():
    from apps.workflow_engine.services.model_routing_learner_store import (
        ModelRoutingLearnerStore,
    )

    contract = ModelRoutingLearnerStore.current_contract()
    learner = SimpleNamespace(
        id=uuid4(),
        judge_contract_hash=contract["judge_contract_hash"],
        candidate_artifact={},
    )
    query = MagicMock()
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.first.side_effect = [learner, None, None]
    db = MagicMock()
    db.query.return_value = query

    with (
        patch(
            "apps.workflow_engine.services.model_routing_learner_store."
            "MultilingualE5ModelChoiceClassifier.vectorize",
            return_value=([0.1] * 135, "test-encoder"),
        ),
        patch(
            "apps.workflow_engine.services.model_routing_learner_store."
            "MultilingualE5TaskRequirementClassifier.predict_from_vector",
            return_value=SimpleNamespace(
                requirements={
                    "task_complexity": 1,
                    "decision_impact": 1,
                    "evidence_synthesis": 1,
                },
                confidence=0.8,
                distance_score=0.9,
                margin=0.7,
            ),
        ),
    ):
        result = ModelRoutingLearnerStore.queue_runtime_judge_label(
            db,
            learner_id=learner.id,
            source_policy_id=uuid4(),
            workflow_run_id=uuid4(),
            node_id="llm-1",
            routing_feature_text="safe feature",
            learning_feature_text="safe learning feature",
            selected_model_id="gpt-4.1-mini",
            candidate_model_ids=["gpt-4.1-mini", "gpt-4.1"],
            confidence=0.9,
            reason_code="judge_selected",
            task_requirements={
                "task_complexity": 1,
                "decision_impact": 1,
                "evidence_synthesis": 1,
            },
        )

    assert result["learning_queued"] is True
    query.with_for_update.assert_not_called()
