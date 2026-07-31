import json
from types import SimpleNamespace

from apps.workflow_engine.services.model_router import ModelRouter
from apps.workflow_engine.services.model_routing_incremental_learning import (
    learning_mode_for,
)
from apps.workflow_engine.services.model_routing_judge_first_policy import (
    build_judge_first_active_policy,
)
from apps.workflow_engine.services.model_routing_learning_batch import (
    ModelRoutingLearningBatchService,
)
from apps.workflow_engine.services.model_routing_local_classifier import (
    DEFAULT_MULTILINGUAL_E5_REVISION,
    MultilingualE5ModelChoiceClassifier,
)
from apps.workflow_engine.services.model_routing_runtime_judge import (
    ModelRoutingRuntimeJudge,
)


class _FeatureEmbedder:
    model_id = "test/judge-first-feature-encoder"

    def encode(self, texts, *, mode="plain"):
        del mode
        return [
            [1.0, 0.0] if "간단" in text else [0.0, 1.0]
            for text in texts
        ]


class _JudgeClient:
    def invoke_sync(self, *, messages, **kwargs):
        del kwargs
        body = json.loads(messages[1]["content"])
        is_simple = "간단" in body["request_feature"]
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "confidence": 0.92,
                                "task_complexity": 1 if is_simple else 3,
                                "decision_impact": 0 if is_simple else 2,
                                "evidence_synthesis": 0 if is_simple else 2,
                                "ambiguity_flags": [],
                                "reason_codes": [
                                    "multi_step_reasoning"
                                ] if not is_simple else [],
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        }


def _node_data():
    return SimpleNamespace(
        model_id="gpt-5-mini",
        fallback_model_id="gpt-4o-mini",
        system_prompt="고객 요청을 처리합니다.",
        user_prompt="{{ message }}",
        assistant_prompt="",
        output_format={"type": "text"},
        knowledgeBases=[],
        knowledgeCollections=[],
    )


def test_judge_labels_gradually_enable_confident_local_routing(monkeypatch):
    monkeypatch.setattr(
        ModelRouter,
        "should_audit_local_prediction",
        classmethod(lambda cls, feature_text: False),
    )
    candidates = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-5.4"]
    active_policy = build_judge_first_active_policy(
        policy_version="judge-first-e2e-v1",
        default_model_id="gpt-5.4",
        fallback_model_id="gpt-4o-mini",
        candidate_model_ids=candidates,
    )
    policy = {"active_policy": active_policy}

    initial = ModelRouter.resolve_policy(
        policy,
        inputs={"message": "간단 사용 안내"},
        node_data=_node_data(),
        available_model_ids=candidates,
        routing_feature_text="간단 사용 안내",
    )
    assert initial.requires_runtime_judge is True
    assert initial.decision_source == "runtime_judge_pending"

    embedder = _FeatureEmbedder()
    labels = []
    selected_models: set[str] = set()
    for index in range(100):
        feature = "간단 사용 안내" if index % 2 == 0 else "복잡 규정 종합 판단"
        assessment = ModelRoutingRuntimeJudge.assess_requirements(
            client=_JudgeClient(),
            routing_feature_text=feature,
        )
        selected_model_id = ModelRouter.select_candidate_for_requirements(
            candidate_model_ids=candidates,
            requirements=assessment.task_requirements,
            default_model_id="gpt-5.4",
        )
        assert selected_model_id is not None
        selected_models.add(selected_model_id)
        learning_feature = ModelRouter.learning_feature_text(
            {"message": feature},
            _node_data(),
        )
        vector, encoder_model_id = MultilingualE5ModelChoiceClassifier.vectorize(
            learning_feature,
            artifact=None,
            embedder=embedder,
        )
        labels.append(
            SimpleNamespace(
                status="accepted",
                feature_vector=vector,
                encoder_model_id=encoder_model_id,
                selected_model_id=selected_model_id,
                candidate_model_ids=candidates,
                confidence=assessment.confidence,
                reason_code="requirements_candidate_selected",
                task_requirements=assessment.task_requirements,
                routing_feature_hash=None,
                local_prediction=None,
                local_confidence=None,
                local_distance_score=None,
                local_margin=None,
                learning_processed_at=None,
            )
        )

    learner_state = {
        "mode": "judge_first",
        "judged_request_count": 0,
        "local_confidence_threshold": 0.78,
    }
    for _ in range(10):
        learned = ModelRoutingLearningBatchService.train_labels(
            learner_state=learner_state,
            labels=labels,
            batch_size=10,
        )
        learner_state = learned.learner_state
    recent = learner_state["recent_evaluation"]

    mode = learning_mode_for(
        judged_request_count=100,
        distinct_selected_model_count=len(selected_models),
        success_rate=1.0,
        schema_pass_rate=1.0,
        downstream_success_rate=1.0,
        fallback_rate=0.0,
        recent_judge_match_rate=recent["judge_match_rate"],
        recent_axis_accuracies=recent["axis_accuracies"],
        recent_axis_mean_errors=recent["axis_mean_errors"],
        recent_judge_label_diversity=recent["judge_label_diversity"],
        recent_local_prediction_diversity=recent["local_prediction_diversity"],
        recent_contract_pass_rate=recent["contract_pass_rate"],
        recent_evaluation_sample_count=recent["sample_count"],
        high_risk_underestimation_count=recent[
            "high_risk_underestimation_count"
        ],
    )
    assert mode == "local_first"

    monkeypatch.setitem(
        MultilingualE5ModelChoiceClassifier._embedder_cache,
        (embedder.model_id, DEFAULT_MULTILINGUAL_E5_REVISION),
        embedder,
    )
    policy["active_policy"] = active_policy
    learner_state["mode"] = mode
    learner_state["local_confidence_threshold"] = 0.78
    learner_state["local_requirement_artifact"] = dict(
        learner_state["candidate_requirement_artifact"]
    )
    policy["learner"] = learner_state

    simple = ModelRouter.resolve_policy(
        policy,
        inputs={"message": "간단 사용 안내"},
        node_data=_node_data(),
        available_model_ids=candidates,
        routing_feature_text="간단 사용 안내",
    )
    complex_request = ModelRouter.resolve_policy(
        policy,
        inputs={"message": "복잡 규정 종합 판단"},
        node_data=_node_data(),
        available_model_ids=candidates,
        routing_feature_text="복잡 규정 종합 판단",
    )

    assert simple.selected_model_id == "gpt-4.1-mini"
    assert complex_request.selected_model_id == "gpt-5.4"
    assert simple.decision_source == "local_router"
    assert complex_request.decision_source == "local_router"
    assert simple.requires_runtime_judge is False
    assert complex_request.requires_runtime_judge is False


def test_one_hundred_accepted_labels_enable_local_router_on_next_request(monkeypatch):
    """운영 label 100건과 최근 50건 gate 뒤에만 local router를 활성화한다."""
    monkeypatch.setattr(
        ModelRouter,
        "should_audit_local_prediction",
        classmethod(lambda cls, feature_text: False),
    )
    from apps.workflow_engine.services.model_routing_learner_store import (
        ModelRoutingLearnerStore,
    )

    candidates = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-5-mini"]
    active_policy = build_judge_first_active_policy(
        policy_version="judge-first-e2e-v2",
        default_model_id="gpt-5-mini",
        fallback_model_id="gpt-4o-mini",
        candidate_model_ids=candidates,
    )
    embedder = _FeatureEmbedder()
    simple_learning_feature = ModelRouter.learning_feature_text(
        {"message": "간단 사용 안내"},
        _node_data(),
    )
    simple_vector, encoder_model_id = MultilingualE5ModelChoiceClassifier.vectorize(
        simple_learning_feature,
        artifact=None,
        embedder=embedder,
    )
    initial_label = SimpleNamespace(
        status="pending",
        feature_vector=simple_vector,
        encoder_model_id=encoder_model_id,
        selected_model_id="gpt-4.1-mini",
        candidate_model_ids=candidates,
        confidence=0.94,
        reason_code="simple_response",
        task_requirements={
            "task_complexity": 1,
            "decision_impact": 0,
            "evidence_synthesis": 0,
        },
        routing_feature_hash=None,
        outcome_reason=None,
    )

    # finalize는 계약 결과만 확정하고 요청 경로에서 가중치를 바꾸지 않는다.
    assert ModelRoutingLearnerStore._finalize_label(
        label=initial_label,
        contract_passed=True,
        reason="contract_passed",
        execution_succeeded=True,
        schema_status="passed",
        downstream_status="passed",
        fallback_used=False,
    )
    assert initial_label.status == "accepted"

    labels = [initial_label]
    initial_label.local_prediction = None
    initial_label.local_confidence = None
    initial_label.local_distance_score = None
    initial_label.local_margin = None
    initial_label.learning_processed_at = None

    # 서로 다른 두 Judge 선택을 계약 통과 label로 100개 확정한다.
    for index in range(99):
        simple = index % 2 == 0
        feature = "간단 사용 안내" if simple else "복잡 규정 종합 판단"
        learning_feature = ModelRouter.learning_feature_text(
            {"message": feature},
            _node_data(),
        )
        feature_vector, encoder_model_id = MultilingualE5ModelChoiceClassifier.vectorize(
            learning_feature,
            artifact=None,
            embedder=embedder,
        )
        label = SimpleNamespace(
            status="pending",
            feature_vector=feature_vector,
            encoder_model_id=encoder_model_id,
            selected_model_id="gpt-4.1-mini" if simple else "gpt-5-mini",
            candidate_model_ids=candidates,
            confidence=0.94,
            reason_code="simple_response" if simple else "multi_constraint",
            task_requirements={
                "task_complexity": 1 if simple else 3,
                "decision_impact": 0 if simple else 2,
                "evidence_synthesis": 0 if simple else 2,
            },
            routing_feature_hash=None,
            outcome_reason=None,
        )
        assert ModelRoutingLearnerStore._finalize_label(
            label=label,
            contract_passed=True,
            reason="contract_passed",
            execution_succeeded=True,
            schema_status="passed",
            downstream_status="passed",
            fallback_used=False,
        )
        label.local_prediction = None
        label.local_confidence = None
        label.local_distance_score = None
        label.local_margin = None
        label.learning_processed_at = None
        labels.append(label)

    learner_state = {
        "mode": "judge_first",
        "judged_request_count": 0,
        "local_confidence_threshold": 0.78,
    }
    for _ in range(10):
        learned = ModelRoutingLearningBatchService.train_labels(
            learner_state=learner_state,
            labels=labels,
            batch_size=10,
        )
        learner_state = learned.learner_state
    assert learner_state["judged_request_count"] == 100
    recent = learner_state["recent_evaluation"]
    mode = learning_mode_for(
        judged_request_count=100,
        success_rate=1.0,
        schema_pass_rate=1.0,
        downstream_success_rate=1.0,
        fallback_rate=0.0,
        recent_judge_match_rate=recent["judge_match_rate"],
        recent_axis_accuracies=recent["axis_accuracies"],
        recent_axis_mean_errors=recent["axis_mean_errors"],
        recent_judge_label_diversity=recent["judge_label_diversity"],
        recent_local_prediction_diversity=recent["local_prediction_diversity"],
        recent_contract_pass_rate=recent["contract_pass_rate"],
        recent_evaluation_sample_count=recent["sample_count"],
        high_risk_underestimation_count=recent[
            "high_risk_underestimation_count"
        ],
    )
    assert mode == "local_first"

    monkeypatch.setitem(
        MultilingualE5ModelChoiceClassifier._embedder_cache,
        (embedder.model_id, DEFAULT_MULTILINGUAL_E5_REVISION),
        embedder,
    )
    decision = ModelRouter.resolve_policy(
        {
            "active_policy": active_policy,
            "learner": {
                **learner_state,
                "mode": "local_first",
                "local_requirement_artifact": dict(
                    learner_state["candidate_requirement_artifact"]
                ),
            },
        },
        inputs={"message": "간단 사용 안내"},
        node_data=_node_data(),
        available_model_ids=candidates,
        routing_feature_text="간단 사용 안내",
    )
    assert decision.decision_source == "local_router"
    assert decision.selected_model_id == "gpt-4.1-mini"
