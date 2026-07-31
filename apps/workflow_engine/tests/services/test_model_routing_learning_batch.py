from copy import deepcopy
from types import SimpleNamespace


def _label(index: int, *, accepted: bool = True):
    simple = index % 2 == 0
    requirements = {
        "task_complexity": 0 if simple else 3,
        "decision_impact": 0 if simple else 3,
        "evidence_synthesis": 0 if simple else 2,
    }
    return SimpleNamespace(
        id=f"label-{index}",
        status="accepted" if accepted else "rejected",
        feature_vector=[1.0, 0.0] if simple else [0.0, 1.0],
        encoder_model_id="test-encoder",
        selected_model_id="gpt-4o-mini" if simple else "gpt-5-mini",
        candidate_model_ids=["gpt-4o-mini", "gpt-5-mini"],
        confidence=0.9,
        reason_code="judge_selected",
        task_requirements=requirements,
        local_prediction=None,
        local_confidence=None,
        local_distance_score=None,
        local_margin=None,
        learning_processed_at=None,
    )


def test_async_first_batch_initializes_without_recording_fake_predictions():
    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchService,
    )

    labels = [_label(index) for index in range(12)]
    learner_state = {"mode": "judge_first"}

    result = ModelRoutingLearningBatchService.train_labels(
        learner_state=deepcopy(learner_state),
        labels=labels,
        batch_size=10,
    )

    assert result.processed_count == 10
    assert result.remaining_count == 2
    assert all(label.learning_processed_at is not None for label in labels[:10])
    assert all(label.learning_processed_at is None for label in labels[10:])
    assert labels[0].local_prediction is None
    assert labels[1].local_prediction is None
    assert result.learner_state["judged_request_count"] == 10
    assert result.learner_state["candidate_requirement_artifact"][
        "trained_example_count"
    ] == 10
    assert result.learner_state["candidate_requirement_artifact"][
        "encoder_model_id"
    ] == "test-encoder"
    assert result.learner_state["validation_window"] == []
    assert result.learner_state["recent_evaluation"]["sample_count"] == 0


def test_async_batch_uses_a_separate_validation_window_for_readiness():
    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchService,
    )

    labels = [_label(index) for index in range(55)]
    state = {"mode": "judge_first", "judged_request_count": 45}
    for _ in range(6):
        result = ModelRoutingLearningBatchService.train_labels(
            learner_state=state,
            labels=labels,
            batch_size=10,
        )
        state = result.learner_state

    evaluation = state["recent_evaluation"]
    assert evaluation["sample_count"] == 5
    assert set(evaluation["axis_mean_errors"]) == {
        "task_complexity",
        "decision_impact",
        "evidence_synthesis",
    }
    assert evaluation["judge_label_diversity"] == 2
    assert evaluation["local_prediction_diversity"] >= 1
    assert set(evaluation["axis_accuracies"]) == {
        "task_complexity",
        "decision_impact",
        "evidence_synthesis",
    }
    assert "high_risk_underestimation_count" in evaluation
    assert len(state["validation_window"]) == 5


def test_first_hundred_labels_are_evaluated_before_each_is_trained_once():
    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchService,
    )

    labels = [_label(index) for index in range(100)]
    state = {"mode": "judge_first"}
    for _ in range(10):
        result = ModelRoutingLearningBatchService.train_labels(
            learner_state=state,
            labels=labels,
            batch_size=10,
        )
        state = result.learner_state

    assert state["judged_request_count"] == 100
    assert state["candidate_requirement_artifact"]["trained_example_count"] == 100
    assert state["recent_evaluation"]["sample_count"] == 50
    assert len(state["validation_window"]) == 50
    assert all(label.learning_processed_at is not None for label in labels)


def test_validation_prediction_uses_axis_specific_requirement_classifier(monkeypatch):
    from apps.workflow_engine.services.model_routing_incremental_learning import (
        TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION,
        IncrementalTaskRequirementClassifier,
    )
    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchService,
    )
    from apps.workflow_engine.services.model_routing_local_classifier import (
        MultilingualE5TaskRequirementClassifier,
    )

    artifact = IncrementalTaskRequirementClassifier.initialize_from_requirements(
        [
            {
                "task_complexity": 1,
                "decision_impact": 2,
                "evidence_synthesis": 1,
            }
        ],
        dimensions=2,
    )
    artifact.update(
        {
            "encoder_model_id": "test-encoder",
            "feature_schema_version": TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION,
            "trained_example_count": 50,
        }
    )
    learner_state = {
        "mode": "judge_first",
        "judged_request_count": 50,
        "candidate_requirement_artifact": artifact,
    }
    label = _label(51)

    def fail_legacy_predict(*_args, **_kwargs):
        raise AssertionError("축별 vector를 우회하는 직접 predict를 사용했습니다.")

    monkeypatch.setattr(
        IncrementalTaskRequirementClassifier,
        "predict",
        fail_legacy_predict,
    )
    monkeypatch.setattr(
        MultilingualE5TaskRequirementClassifier,
        "predict_from_vector",
        lambda *_args, **_kwargs: SimpleNamespace(
            requirements={
                "task_complexity": 1,
                "decision_impact": 2,
                "evidence_synthesis": 1,
            },
            confidence=0.8,
            distance_score=0.9,
            margin=0.7,
        ),
    )

    result = ModelRoutingLearningBatchService.train_labels(
        learner_state=learner_state,
        labels=[label],
        batch_size=1,
    )

    assert result.learner_state["recent_evaluation"]["sample_count"] == 1
    assert label.local_prediction == {
        "task_complexity": 1,
        "decision_impact": 2,
        "evidence_synthesis": 1,
    }


def test_async_batch_restarts_learning_when_feature_schema_changes():
    from apps.workflow_engine.services.model_routing_incremental_learning import (
        TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION,
    )
    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchService,
    )

    learner_state = {
            "mode": "local_first",
            "judged_request_count": 79,
            "validation_window": [{"old": True}],
            "local_requirement_artifact": {
                "feature_schema_version": "prompt_context_v1",
                "trained_example_count": 79,
                "weights": {},
            },
    }

    result = ModelRoutingLearningBatchService.train_labels(
        learner_state=learner_state,
        labels=[_label(0)],
        batch_size=1,
    )

    learning = result.learner_state
    artifact = learning["candidate_requirement_artifact"]
    assert artifact["feature_schema_version"] == TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION
    assert artifact["trained_example_count"] == 1
    assert learning["judged_request_count"] == 1
    assert learning["recent_evaluation"]["sample_count"] == 0


def test_batch_result_can_report_deferred_training():
    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchResult,
    )

    result = ModelRoutingLearningBatchResult(
        learner_state={},
        processed_count=0,
        remaining_count=3,
        deferred_seconds=300,
    )

    assert result.deferred_seconds == 300


def test_batch_skips_an_invalid_rejected_label_without_blocking_other_learning():
    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchService,
    )

    invalid = _label(0, accepted=False)
    invalid.task_requirements = None
    valid = _label(1)

    result = ModelRoutingLearningBatchService.train_labels(
        learner_state={"mode": "judge_first"},
        labels=[invalid, valid],
    )

    assert result.processed_count == 2
    assert invalid.learning_processed_at is not None
    assert valid.learning_processed_at is not None
    assert result.learner_state["judged_request_count"] == 1
    assert result.learner_state["recent_evaluation"]["sample_count"] == 0


def test_valid_rejected_label_still_trains_requirement_levels():
    from apps.workflow_engine.services.model_routing_learning_batch import (
        ModelRoutingLearningBatchService,
    )

    rejected = _label(0, accepted=False)
    accepted = _label(1)

    result = ModelRoutingLearningBatchService.train_labels(
        learner_state={"mode": "judge_first"},
        labels=[rejected, accepted],
    )

    assert result.learner_state["judged_request_count"] == 2
    assert result.learner_state["candidate_requirement_artifact"][
        "trained_example_count"
    ] == 2
    assert result.learner_state["recent_evaluation"]["sample_count"] == 0
