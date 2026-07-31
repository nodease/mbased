import json
from types import SimpleNamespace

import pytest

from apps.workflow_engine.services.model_routing_local_classifier import (
    DEFAULT_MULTILINGUAL_E5_MODEL_ID,
    MultilingualE5Embedder,
    MultilingualE5ModelChoiceClassifier,
    MultilingualE5TaskRequirementClassifier,
)
from apps.workflow_engine.services.model_routing_incremental_learning import (
    IncrementalTaskRequirementClassifier,
    learning_mode_for,
)
from apps.workflow_engine.services.model_router import ModelRouter


def test_requirement_classifier_learns_request_capability_not_selected_model_id():
    artifact = None
    for _ in range(20):
        artifact = IncrementalTaskRequirementClassifier.update(
            artifact,
            vector=[1.0, 0.0],
            task_requirements={
                "task_complexity": 0,
                "decision_impact": 0,
                "evidence_synthesis": 0,
            },
        )
        artifact = IncrementalTaskRequirementClassifier.update(
            artifact,
            vector=[0.0, 1.0],
            task_requirements={
                "task_complexity": 3,
                "decision_impact": 3,
                "evidence_synthesis": 2,
            },
        )

    simple = IncrementalTaskRequirementClassifier.predict(
        artifact,
        vector=[1.0, 0.0],
    )
    advanced = IncrementalTaskRequirementClassifier.predict(
        artifact,
        vector=[0.0, 1.0],
    )

    assert simple.requirements["task_complexity"] < advanced.requirements["task_complexity"]
    assert simple.requirements["decision_impact"] < advanced.requirements["decision_impact"]
    assert "selected_model_id" not in str(artifact)


def test_local_learning_feature_uses_runtime_variables_without_fixed_prompt_contract():
    node = SimpleNamespace(
        title="고객 문의 처리",
        system_prompt="모든 실행에 반복되는 고정 시스템 프롬프트",
        user_prompt="고정 문구 뒤에 {{ message }}를 붙입니다.",
        assistant_prompt="모든 실행에 반복되는 고정 어시스턴트 프롬프트",
        referenced_variables=[
            SimpleNamespace(name="message", value_selector=["webhook", "message"]),
        ],
        output_format={
            "type": "json",
            "schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
        },
    )

    feature = ModelRouter.learning_feature_text(
        {
            "webhook": {"message": "VPN 연결 방법"},
            "unrelated": {"fixed": "학습 대상이 아닌 upstream 값"},
        },
        node,
        rag_metadata={"used": True, "retrieved_chunk_count": 2},
    )

    payload = json.loads(feature)
    assert payload["primary_request"] == {"message": "VPN 연결 방법"}
    assert payload["dynamic_context"] == {}
    assert payload["structured_features"]["rag"] == {
        "retrieved_chunk_count": 2,
        "used": True,
    }
    assert "고정 시스템 프롬프트" not in feature
    assert "고정 문구 뒤에" not in feature
    assert "고정 어시스턴트 프롬프트" not in feature
    assert "학습 대상이 아닌 upstream 값" not in feature
    assert "NODE_TASK_CONTEXT:" not in feature
    assert "OUTPUT_CONTRACT:" not in feature
    assert '"type": "json"' not in feature


def test_local_learning_feature_separates_primary_context_and_structured_values():
    node = SimpleNamespace(
        user_prompt=(
            "{{request}} {{context}} {{constraints}} {{customerTier}} {{outputMode}}"
        ),
        referenced_variables=[
            SimpleNamespace(name="request", value_selector=["webhook", "request"]),
            SimpleNamespace(name="context", value_selector=["webhook", "context"]),
            SimpleNamespace(name="constraints", value_selector=["webhook", "constraints"]),
            SimpleNamespace(name="customerTier", value_selector=["webhook", "customerTier"]),
            SimpleNamespace(name="outputMode", value_selector=["webhook", "outputMode"]),
        ],
    )

    payload = json.loads(
        ModelRouter.learning_feature_text(
            {
                "webhook": {
                    "request": "결제 장애의 영향 범위를 판단해 주세요.",
                    "context": "복구 작업은 아직 시작되지 않았습니다.",
                    "constraints": ["확정되지 않은 보상을 약속하지 않습니다."],
                    "customerTier": "enterprise",
                    "outputMode": "analysis",
                }
            },
            node,
        )
    )

    assert payload["primary_request"] == {
        "request": "결제 장애의 영향 범위를 판단해 주세요."
    }
    assert payload["dynamic_context"] == {
        "constraints": ["확정되지 않은 보상을 약속하지 않습니다."],
        "context": "복구 작업은 아직 시작되지 않았습니다.",
    }
    assert payload["structured_features"] == {
        "customerTier": "enterprise",
        "outputMode": "analysis",
        "request_evidence": {
            "comparison_requested": False,
            "comparison_criterion_count": 0,
            "context_char_bucket": 0,
            "context_field_count": 1,
            "context_value_count": 1,
            "multi_source_context": False,
            "retrieved_source_count": 0,
            "synthesis_requested": False,
        },
        "requested_action": {
            "approval_decision": True,
            "confidence": 1.0,
            "external_send_requested": False,
            "information_request": False,
            "informational_scope": False,
            "negated_action": False,
            "recommendation_request": False,
            "state_change_requested": False,
            "workflow_effect_match": False,
        },
        "routing_contract": {
            "control_gate_present": False,
            "customer_facing": False,
            "customer_output_reachable": False,
            "downstream_contract_required": False,
            "external_read_reachable": False,
            "external_write_reachable": False,
            "has_file_input": False,
            "human_approval_required": False,
            "input_token_bucket": 0,
            "irreversible_effect_possible": False,
            "knowledge_enabled": False,
            "local_execution_reachable": False,
            "reachable_effect_count": 0,
            "schema_required": False,
        },
    }


def test_learning_feature_excludes_declared_value_when_prompt_does_not_use_it():
    node = SimpleNamespace(
        user_prompt="문의: {{message}}",
        referenced_variables=[
            SimpleNamespace(name="message", value_selector=["webhook", "message"]),
            SimpleNamespace(
                name="customerTier",
                value_selector=["webhook", "customerTier"],
            ),
        ],
    )
    business = {
        "webhook": {
            "message": "환불 정책을 설명해 주세요.",
            "customerTier": "business",
            "attachment": {"filename": "ignored.pdf"},
        }
    }
    enterprise = {
        "webhook": {
            "message": "환불 정책을 설명해 주세요.",
            "customerTier": "enterprise",
            "attachment": {"filename": "ignored.pdf"},
        }
    }

    assert ModelRouter.learning_feature_text(
        business, node
    ) == ModelRouter.learning_feature_text(enterprise, node)
    assert ModelRouter.runtime_requirement_facts(
        inputs=business, node_data=node
    ) == ModelRouter.runtime_requirement_facts(inputs=enterprise, node_data=node)
    assert ModelRouter.runtime_requirement_facts(
        inputs=business, node_data=node
    )["file_input_present"] is False


def test_learning_feature_keeps_value_when_prompt_uses_it():
    node = SimpleNamespace(
        user_prompt="{{customerTier}} 고객의 문의: {{message}}",
        referenced_variables=[
            SimpleNamespace(name="message", value_selector=["webhook", "message"]),
            SimpleNamespace(
                name="customerTier",
                value_selector=["webhook", "customerTier"],
            ),
        ],
    )

    assert ModelRouter.learning_feature_text(
        {"webhook": {"message": "문의", "customerTier": "business"}}, node
    ) != ModelRouter.learning_feature_text(
        {"webhook": {"message": "문의", "customerTier": "enterprise"}}, node
    )


def test_learning_feature_uses_nested_routing_context_and_graph_effect_profile():
    node = SimpleNamespace(
        referenced_variables=[
            SimpleNamespace(name="request", value_selector=["webhook", "request"]),
        ],
        model_routing_context={"customer_facing": True},
    )

    payload = json.loads(
        ModelRouter.learning_feature_text(
            {"webhook": {"request": "이 고객의 환불을 승인해 주세요."}},
            node,
            effect_profile={
                "control_gate_present": True,
                "customer_output_reachable": True,
                "downstream_contract_required": True,
                "external_read_reachable": False,
                "external_write_reachable": True,
                "irreversible_effect_possible": True,
                "local_execution_reachable": False,
                "reachable_effect_count": 1,
            },
        )
    )

    assert payload["structured_features"]["routing_contract"] == {
        "control_gate_present": True,
        "customer_facing": True,
        "customer_output_reachable": True,
        "downstream_contract_required": True,
        "external_read_reachable": False,
        "external_write_reachable": True,
        "has_file_input": False,
        "human_approval_required": False,
        "input_token_bucket": 0,
        "irreversible_effect_possible": True,
        "knowledge_enabled": False,
        "local_execution_reachable": False,
        "reachable_effect_count": 1,
        "schema_required": False,
    }


def test_learning_feature_distinguishes_information_from_requested_state_change():
    node = SimpleNamespace(
        user_prompt="{{request}}",
        referenced_variables=[
            SimpleNamespace(name="request", value_selector=["webhook", "request"]),
        ],
    )
    effect_profile = {
        "customer_output_reachable": True,
        "external_write_reachable": True,
        "irreversible_effect_possible": True,
    }

    information = json.loads(
        ModelRouter.learning_feature_text(
            {"webhook": {"request": "환불 승인 절차를 설명해 주세요."}},
            node,
            effect_profile=effect_profile,
        )
    )
    execution = json.loads(
        ModelRouter.learning_feature_text(
            {"webhook": {"request": "이 고객의 환불을 승인하고 처리해 주세요."}},
            node,
            effect_profile=effect_profile,
        )
    )

    information_action = information["structured_features"]["requested_action"]
    execution_action = execution["structured_features"]["requested_action"]
    assert information_action["information_request"] is True
    assert information_action["approval_decision"] is False
    assert information_action["state_change_requested"] is False
    assert execution_action["information_request"] is False
    assert execution_action["approval_decision"] is True
    assert execution_action["state_change_requested"] is True
    assert execution_action["workflow_effect_match"] is True


def test_learning_feature_does_not_treat_negated_action_as_execution():
    node = SimpleNamespace(
        user_prompt="{{request}}",
        referenced_variables=[
            SimpleNamespace(name="request", value_selector=["webhook", "request"]),
        ],
    )

    payload = json.loads(
        ModelRouter.learning_feature_text(
            {"webhook": {"request": "환불을 승인하거나 처리하지 마세요."}},
            node,
            effect_profile={"external_write_reachable": True},
        )
    )

    action = payload["structured_features"]["requested_action"]
    assert action["negated_action"] is True
    assert action["approval_decision"] is False
    assert action["state_change_requested"] is False


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        (
            "관리자 권한으로 변경하는 방법을 알려 주세요.",
            {"information_request": True, "state_change_requested": False},
        ),
        (
            "이 사용자의 권한을 관리자로 변경해 주세요.",
            {"information_request": False, "state_change_requested": True},
        ),
        (
            "두 요금제를 비교해서 하나를 추천해 주세요.",
            {"recommendation_request": True, "state_change_requested": False},
        ),
        (
            "고객에게 장애 안내 메일을 보내 주세요.",
            {"external_send_requested": True},
        ),
        (
            "이 데이터는 삭제하지 마세요.",
            {"negated_action": True, "state_change_requested": False},
        ),
        (
            "이 배포를 승인할 수 있는지 확인해 주세요.",
            {"information_request": True, "approval_decision": False},
        ),
        (
            "Refund policy overview",
            {"state_change_requested": False},
        ),
        (
            "Email delivery failure",
            {"external_send_requested": False},
        ),
    ],
)
def test_learning_feature_extracts_cross_domain_requested_actions(request_text, expected):
    node = SimpleNamespace(
        user_prompt="{{request}}",
        referenced_variables=[
            SimpleNamespace(name="request", value_selector=["webhook", "request"]),
        ],
    )

    payload = json.loads(
        ModelRouter.learning_feature_text(
            {"webhook": {"request": request_text}},
            node,
            effect_profile={
                "customer_output_reachable": True,
                "external_write_reachable": True,
                "irreversible_effect_possible": True,
            },
        )
    )

    action = payload["structured_features"]["requested_action"]
    assert {key: action[key] for key in expected} == expected


def test_learning_feature_counts_request_specific_evidence_and_comparison_signals():
    node = SimpleNamespace(
        user_prompt="{{request}} {{context}}",
        referenced_variables=[
            SimpleNamespace(name="request", value_selector=["webhook", "request"]),
            SimpleNamespace(name="context", value_selector=["webhook", "context"]),
        ],
    )

    single = json.loads(
        ModelRouter.learning_feature_text(
            {
                "webhook": {
                    "request": "정책 내용을 요약해 주세요.",
                    "context": ["정책 A"],
                }
            },
            node,
            rag_metadata={"used": True, "source_count": 1},
        )
    )
    multiple = json.loads(
        ModelRouter.learning_feature_text(
            {
                "webhook": {
                    "request": "세 자료의 차이를 비교하고 근거를 종합해 결론을 내려 주세요.",
                    "context": ["정책 A", "정책 B", "고객 상태 C"],
                }
            },
            node,
            rag_metadata={"used": True, "source_count": 3},
        )
    )

    single_evidence = single["structured_features"]["request_evidence"]
    multiple_evidence = multiple["structured_features"]["request_evidence"]
    assert single_evidence["context_value_count"] == 1
    assert single_evidence["comparison_requested"] is False
    assert multiple_evidence["context_value_count"] == 3
    assert multiple_evidence["comparison_requested"] is True
    assert multiple_evidence["synthesis_requested"] is True
    assert multiple_evidence["retrieved_source_count"] == 3
    assert multiple_evidence["multi_source_context"] is True


def test_learning_feature_counts_explicit_comparison_criteria():
    node = SimpleNamespace(
        user_prompt="{{request}}",
        referenced_variables=[
            SimpleNamespace(name="request", value_selector=["webhook", "request"]),
        ],
    )

    payload = json.loads(
        ModelRouter.learning_feature_text(
            {
                "webhook": {
                    "request": "두 방안을 비용, 응답 속도, 품질 기준으로 비교해 주세요."
                }
            },
            node,
        )
    )

    evidence = payload["structured_features"]["request_evidence"]
    assert evidence["comparison_requested"] is True
    assert evidence["comparison_criterion_count"] == 3


def test_learning_feature_does_not_count_constraints_as_evidence_sources():
    node = SimpleNamespace(
        user_prompt="{{request}} {{context}} {{constraints}}",
        referenced_variables=[
            SimpleNamespace(name="request", value_selector=["webhook", "request"]),
            SimpleNamespace(name="context", value_selector=["webhook", "context"]),
            SimpleNamespace(
                name="constraints",
                value_selector=["webhook", "constraints"],
            ),
        ],
    )

    payload = json.loads(
        ModelRouter.learning_feature_text(
            {
                "webhook": {
                    "request": "정책을 설명해 주세요.",
                    "context": "정책 A",
                    "constraints": [
                        "확정하지 않습니다.",
                        "JSON으로 답합니다.",
                        "필수 필드를 포함합니다.",
                    ],
                }
            },
            node,
        )
    )

    evidence = payload["structured_features"]["request_evidence"]
    assert evidence["context_field_count"] == 1
    assert evidence["context_value_count"] == 1
    assert evidence["multi_source_context"] is False


class _GroupedEmbedder:
    model_id = "grouped-test-embedder"

    def __init__(self):
        self.texts: list[str] = []

    def encode(self, texts, *, mode="plain"):
        self.texts = list(texts)
        vectors = {
            "primary_request": [1.0, 0.0],
            "dynamic_context": [0.0, 1.0],
            "structured_features": [1.0, 1.0],
        }
        return [
            vectors[json.loads(text.removeprefix("query: "))["group"]]
            for text in texts
        ]


def test_grouped_learning_vector_prioritizes_primary_request_without_dropping_context():
    embedder = _GroupedEmbedder()
    feature = json.dumps(
        {
            "primary_request": {"request": "핵심 요청"},
            "dynamic_context": {"context": "추가 문맥"},
            "structured_features": {"customerTier": "enterprise"},
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    vector, model_id = MultilingualE5ModelChoiceClassifier.vectorize(
        feature,
        artifact=None,
        embedder=embedder,
    )

    assert len(vector) == 160
    assert sum(value * value for value in vector[:128]) == pytest.approx(1.0)
    assert vector[128:] == pytest.approx([0.0] * 32)
    assert model_id == "grouped-test-embedder"
    assert len(embedder.texts) == 2
    assert all(text.startswith("query: ") for text in embedder.texts)


class _SameSemanticEmbedder:
    model_id = "same-semantic-test-embedder"

    def encode(self, texts, *, mode="plain"):
        return [[1.0, 0.0] for _text in texts]


def test_decision_impact_learns_graph_effects_even_when_semantic_vectors_are_equal():
    def feature(*, external_write: bool) -> str:
        return json.dumps(
            {
                "primary_request": {"request": "환불 요청을 처리해 주세요."},
                "dynamic_context": {},
                "structured_features": {
                    "routing_contract": {
                        "customer_facing": True,
                        "external_write_reachable": external_write,
                        "irreversible_effect_possible": external_write,
                        "reachable_effect_count": int(external_write),
                    }
                },
            },
            ensure_ascii=False,
        )

    low_vector, encoder_id = MultilingualE5ModelChoiceClassifier.vectorize(
        feature(external_write=False),
        artifact=None,
        embedder=_SameSemanticEmbedder(),
    )
    high_vector, _ = MultilingualE5ModelChoiceClassifier.vectorize(
        feature(external_write=True),
        artifact=None,
        embedder=_SameSemanticEmbedder(),
    )
    artifact = None
    for _ in range(60):
        artifact = MultilingualE5TaskRequirementClassifier.update_from_vector(
            artifact,
            vector=low_vector,
            encoder_model_id=encoder_id,
            task_requirements={
                "task_complexity": 1,
                "decision_impact": 0,
                "evidence_synthesis": 1,
            },
        )
        artifact = MultilingualE5TaskRequirementClassifier.update_from_vector(
            artifact,
            vector=high_vector,
            encoder_model_id=encoder_id,
            task_requirements={
                "task_complexity": 1,
                "decision_impact": 3,
                "evidence_synthesis": 1,
            },
        )

    low = MultilingualE5TaskRequirementClassifier.predict_from_vector(
        artifact,
        vector=low_vector,
    )
    high = MultilingualE5TaskRequirementClassifier.predict_from_vector(
        artifact,
        vector=high_vector,
    )

    assert low.requirements["decision_impact"] < high.requirements["decision_impact"]
    assert low.requirements["task_complexity"] == high.requirements["task_complexity"]
    assert low.requirements["evidence_synthesis"] == high.requirements["evidence_synthesis"]


def test_axis_specific_dynamic_features_separate_impact_and_evidence_requirements():
    def feature(*, executes_action: bool, combines_evidence: bool) -> str:
        return json.dumps(
            {
                "primary_request": {"request": "같은 주제의 요청"},
                "dynamic_context": {},
                "structured_features": {
                    "requested_action": {
                        "approval_decision": executes_action,
                        "state_change_requested": executes_action,
                        "workflow_effect_match": executes_action,
                        "confidence": 1.0,
                    },
                    "request_evidence": {
                        "context_field_count": 2 if combines_evidence else 0,
                        "context_value_count": 3 if combines_evidence else 0,
                        "context_char_bucket": 2 if combines_evidence else 0,
                        "comparison_requested": combines_evidence,
                        "synthesis_requested": combines_evidence,
                        "retrieved_source_count": 3 if combines_evidence else 0,
                        "multi_source_context": combines_evidence,
                    },
                },
            },
            ensure_ascii=False,
        )

    low_vector, encoder_id = MultilingualE5ModelChoiceClassifier.vectorize(
        feature(executes_action=False, combines_evidence=False),
        artifact=None,
        embedder=_SameSemanticEmbedder(),
    )
    high_vector, _ = MultilingualE5ModelChoiceClassifier.vectorize(
        feature(executes_action=True, combines_evidence=True),
        artifact=None,
        embedder=_SameSemanticEmbedder(),
    )
    artifact = None
    for _ in range(60):
        artifact = MultilingualE5TaskRequirementClassifier.update_from_vector(
            artifact,
            vector=low_vector,
            encoder_model_id=encoder_id,
            task_requirements={
                "task_complexity": 1,
                "decision_impact": 0,
                "evidence_synthesis": 0,
            },
        )
        artifact = MultilingualE5TaskRequirementClassifier.update_from_vector(
            artifact,
            vector=high_vector,
            encoder_model_id=encoder_id,
            task_requirements={
                "task_complexity": 1,
                "decision_impact": 3,
                "evidence_synthesis": 3,
            },
        )

    low = MultilingualE5TaskRequirementClassifier.predict_from_vector(
        artifact,
        vector=low_vector,
    )
    high = MultilingualE5TaskRequirementClassifier.predict_from_vector(
        artifact,
        vector=high_vector,
    )

    assert low.requirements["decision_impact"] < high.requirements["decision_impact"]
    assert low.requirements["evidence_synthesis"] < high.requirements["evidence_synthesis"]
    assert low.requirements["task_complexity"] == high.requirements["task_complexity"]


def test_local_router_uses_multilingual_e5_base_by_default(monkeypatch):
    monkeypatch.delenv("MODEL_ROUTING_EMBEDDING_MODEL_ID", raising=False)

    embedder = MultilingualE5Embedder()

    assert DEFAULT_MULTILINGUAL_E5_MODEL_ID == "intfloat/multilingual-e5-base"
    assert embedder.model_id == DEFAULT_MULTILINGUAL_E5_MODEL_ID


def test_local_learning_feature_is_unchanged_when_only_fixed_prompts_change():
    inputs = {"start": {"question": "휴가 규정을 알려 주세요."}}
    variables = [
        SimpleNamespace(name="question", value_selector=["start", "question"]),
    ]
    first = SimpleNamespace(
        title="첫 제목",
        system_prompt="첫 시스템 프롬프트",
        user_prompt="질문: {{ question }}",
        assistant_prompt="첫 어시스턴트 프롬프트",
        referenced_variables=variables,
    )
    second = SimpleNamespace(
        title="완전히 다른 제목",
        system_prompt="완전히 다른 시스템 프롬프트",
        user_prompt="다른 고정 문구: {{ question }}",
        assistant_prompt="완전히 다른 어시스턴트 프롬프트",
        referenced_variables=variables,
    )

    assert ModelRouter.learning_feature_text(
        inputs,
        first,
    ) == ModelRouter.learning_feature_text(inputs, second)


def test_local_learning_feature_falls_back_to_runtime_inputs_without_variable_metadata():
    node = SimpleNamespace(
        title="레거시 LLM 노드",
        system_prompt="고정 시스템 프롬프트",
        user_prompt="고정 사용자 프롬프트",
        assistant_prompt="",
        referenced_variables=[],
    )

    feature = ModelRouter.learning_feature_text(
        {"message": "비밀번호 재설정 방법을 알려 주세요."},
        node,
    )

    assert '"message": "비밀번호 재설정 방법을 알려 주세요."' in feature
    assert "고정 시스템 프롬프트" not in feature
    assert "고정 사용자 프롬프트" not in feature


def test_local_mode_rejects_a_collapsed_judge_label_distribution():
    assert learning_mode_for(
        judged_request_count=50,
        recent_judge_match_rate=0.95,
        recent_axis_mean_errors={
            "task_complexity": 0.1,
            "decision_impact": 0.1,
            "evidence_synthesis": 0.1,
        },
        recent_judge_label_diversity=3,
        recent_local_prediction_diversity=1,
        recent_contract_pass_rate=1.0,
        success_rate=1.0,
        schema_pass_rate=1.0,
        downstream_success_rate=1.0,
        fallback_rate=0.0,
    ) == "judge_first"


def test_local_mode_requires_recent_pre_learning_accuracy_and_contract_quality():
    healthy = {
        "judged_request_count": 100,
        "recent_judge_match_rate": 0.75,
        "recent_axis_accuracies": {
            "task_complexity": 0.9,
            "decision_impact": 0.9,
            "evidence_synthesis": 0.9,
        },
        "recent_axis_mean_errors": {
            "task_complexity": 0.3,
            "decision_impact": 0.2,
            "evidence_synthesis": 0.4,
        },
        "recent_judge_label_diversity": 3,
        "recent_local_prediction_diversity": 3,
        "recent_contract_pass_rate": 0.95,
        "recent_evaluation_sample_count": 50,
        "high_risk_underestimation_count": 0,
        "success_rate": 0.98,
        "schema_pass_rate": 0.99,
        "downstream_success_rate": 0.99,
        "fallback_rate": 0.01,
    }

    assert learning_mode_for(**healthy) == "local_first"
    assert learning_mode_for(**{**healthy, "recent_judge_match_rate": 0.74}) == "judge_first"
    assert learning_mode_for(
        **{
            **healthy,
            "recent_axis_mean_errors": {
                "task_complexity": 0.6,
                "decision_impact": 0.2,
                "evidence_synthesis": 0.4,
            },
        }
    ) == "judge_first"
    assert learning_mode_for(**{**healthy, "recent_contract_pass_rate": 0.9}) == "judge_first"
