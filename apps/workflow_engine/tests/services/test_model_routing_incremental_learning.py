from apps.workflow_engine.services.model_routing_incremental_learning import (
    IncrementalModelChoiceClassifier,
    TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION,
    learning_mode_for,
)
from apps.workflow_engine.services.model_router import ModelRouter
from types import SimpleNamespace


def test_incremental_classifier_learns_judge_labels_without_storing_input_text():
    artifact = None
    vectors = {
        "simple": [1.0, 0.0],
        "complex": [0.0, 1.0],
    }

    for _ in range(12):
        artifact = IncrementalModelChoiceClassifier.update(
            artifact,
            vector=vectors["simple"],
            selected_model_id="gpt-4o-mini",
            candidate_model_ids=["gpt-4o-mini", "gpt-5-mini"],
        )
        artifact = IncrementalModelChoiceClassifier.update(
            artifact,
            vector=vectors["complex"],
            selected_model_id="gpt-5-mini",
            candidate_model_ids=["gpt-4o-mini", "gpt-5-mini"],
        )

    simple = IncrementalModelChoiceClassifier.predict(
        artifact,
        vector=vectors["simple"],
        available_model_ids=["gpt-4o-mini", "gpt-5-mini"],
    )
    complex_request = IncrementalModelChoiceClassifier.predict(
        artifact,
        vector=vectors["complex"],
        available_model_ids=["gpt-4o-mini", "gpt-5-mini"],
    )

    assert simple.selected_model_id == "gpt-4o-mini"
    assert complex_request.selected_model_id == "gpt-5-mini"
    assert simple.confidence >= 0.75
    assert "simple" not in str(artifact)
    assert "complex" not in str(artifact)


def test_learning_mode_stays_judge_first_until_outcomes_are_diverse_and_healthy():
    assert learning_mode_for(
        judged_request_count=23,
        distinct_selected_model_count=2,
        success_rate=1.0,
        schema_pass_rate=1.0,
        downstream_success_rate=1.0,
        fallback_rate=0.0,
    ) == "judge_first"

    assert learning_mode_for(
        judged_request_count=100,
        distinct_selected_model_count=1,
        success_rate=1.0,
        schema_pass_rate=1.0,
        downstream_success_rate=1.0,
        fallback_rate=0.0,
    ) == "judge_first"

    assert learning_mode_for(
        judged_request_count=100,
        distinct_selected_model_count=2,
        success_rate=0.98,
        schema_pass_rate=0.99,
        downstream_success_rate=0.99,
        fallback_rate=0.01,
        recent_judge_match_rate=0.75,
        recent_axis_accuracies={
            "task_complexity": 0.9,
            "decision_impact": 0.9,
            "evidence_synthesis": 0.9,
        },
        recent_axis_mean_errors={
            "task_complexity": 0.3,
            "decision_impact": 0.2,
            "evidence_synthesis": 0.4,
        },
        recent_judge_label_diversity=2,
        recent_local_prediction_diversity=2,
        recent_contract_pass_rate=0.95,
        recent_evaluation_sample_count=50,
        high_risk_underestimation_count=0,
    ) == "local_first"

    assert learning_mode_for(
        judged_request_count=100,
        distinct_selected_model_count=2,
        success_rate=0.98,
        schema_pass_rate=0.80,
        downstream_success_rate=0.99,
        fallback_rate=0.01,
        recent_judge_match_rate=0.75,
        recent_axis_accuracies={
            "task_complexity": 0.9,
            "decision_impact": 0.9,
            "evidence_synthesis": 0.9,
        },
        recent_axis_mean_errors={
            "task_complexity": 0.3,
            "decision_impact": 0.2,
            "evidence_synthesis": 0.4,
        },
        recent_judge_label_diversity=2,
        recent_local_prediction_diversity=2,
        recent_contract_pass_rate=0.95,
        recent_evaluation_sample_count=50,
        high_risk_underestimation_count=0,
    ) == "judge_first"


def test_policy_uses_runtime_judge_first_then_local_router(monkeypatch):
    policy = {
        "active_policy": {
            "strategy_id": "judge_bootstrap_incremental_v1",
            "default_model_id": "gpt-5-mini",
            "fallback_model_id": "gpt-4o-mini",
            "learning": {
                "mode": "judge_first",
                "local_router_artifact": {},
            },
        }
    }
    node_data = SimpleNamespace(
        model_id="gpt-5-mini",
        fallback_model_id="gpt-4o-mini",
        knowledgeBases=[],
        knowledgeCollections=[],
        output_format={"type": "text"},
        system_prompt="",
        user_prompt="{{ message }}",
        assistant_prompt="",
        parameters={},
        title="support",
    )

    first = ModelRouter.resolve_policy(
        policy,
        inputs={"message": "문의"},
        node_data=node_data,
        available_model_ids=["gpt-4o-mini", "gpt-5-mini"],
        routing_feature_text="짧은 문의",
    )
    assert first.requires_runtime_judge is True
    assert first.decision_source == "runtime_judge_pending"

    class _Prediction:
        requirements = {
            "task_complexity": 1,
            "decision_impact": 0,
            "evidence_synthesis": 0,
        }
        confidence = 0.92

    from apps.workflow_engine.services.model_routing_local_classifier import (
        MultilingualE5TaskRequirementClassifier,
    )

    monkeypatch.setattr(
        MultilingualE5TaskRequirementClassifier, "predict", lambda *_args, **_kwargs: _Prediction()
    )
    selection_calls: list[list[str]] = []

    def _select_available_candidate(
        _cls,
        *,
        candidate_model_ids,
        **_kwargs,
    ):
        candidates = list(candidate_model_ids)
        selection_calls.append(candidates)
        return candidates[0]

    monkeypatch.setattr(
        ModelRouter,
        "select_candidate_for_requirements",
        classmethod(_select_available_candidate),
    )
    policy["learner"] = {
        "mode": "local_first",
        "local_confidence_threshold": 0.78,
        "local_requirement_artifact": {
            "kind": "multilingual_e5_task_requirements_ordinal_v2",
            "feature_schema_version": TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION,
        },
    }
    local = ModelRouter.resolve_policy(
        policy,
        inputs={"message": "문의"},
        node_data=node_data,
        available_model_ids=["gpt-4o-mini", "gpt-5-mini"],
        routing_feature_text="짧은 문의",
    )
    assert selection_calls == [["gpt-4o-mini", "gpt-5-mini"]]
    assert local.selected_model_id == selection_calls[0][0]
    assert local.decision_source == "local_router"
    assert local.requires_runtime_judge is False
