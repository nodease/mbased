import json

import pytest

import apps.workflow_engine.services.model_routing_runtime_judge as runtime_judge_module
from apps.workflow_engine.services.model_routing_runtime_judge import (
    ModelRoutingRuntimeJudge,
    RuntimeJudgeResponseError,
)
from apps.shared.services.llm_client.base import ProviderInvocationError


class _JudgeClient:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []

    def invoke_sync(self, *, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return {
            "choices": [{"message": {"content": self.content}}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 18},
        }


class _IncompleteThenCompactJudgeClient(_JudgeClient):
    def invoke_sync(self, *, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if len(self.calls) == 1:
            raise ProviderInvocationError(
                "OpenAI Responses 응답이 완료되지 않았습니다: status=incomplete",
                reason_code="responses_incomplete",
                provider_response_status="incomplete",
                usage={"prompt_tokens": 80, "completion_tokens": 40},
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"task_complexity":3,"decision_impact":2,'
                            '"evidence_synthesis":2,"confidence":0.81,'
                            '"ambiguity_flags":[],"reason_codes":['
                            '"multi_step_reasoning"]}'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 26, "completion_tokens": 12},
        }


def _assessment_payload(**overrides):
    payload = {
        "task_complexity": 2,
        "decision_impact": 1,
        "evidence_synthesis": 2,
        "confidence": 0.87,
        "ambiguity_flags": [],
        "reason_codes": ["multi_step_reasoning"],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_requirement_judge_does_not_receive_or_select_candidate_models():
    client = _JudgeClient(_assessment_payload())

    assessment = ModelRoutingRuntimeJudge.assess_requirements(
        client=client,
        routing_feature_text="여러 문서를 비교해 내부 검토안을 작성해 주세요.",
        structural_facts={
            "input_token_bucket": "medium",
            "schema_required": True,
            "knowledge_enabled": True,
            "retrieved_source_count": 3,
            "downstream_contract_required": True,
        },
    )

    assert assessment.task_requirements == {
        "task_complexity": 2,
        "decision_impact": 1,
        "evidence_synthesis": 2,
    }
    body = json.loads(client.calls[0]["messages"][1]["content"])
    system_prompt = client.calls[0]["messages"][0]["content"]
    assert "candidate_models" not in body
    assert "selected_model_id" not in body
    assert "selected_model_id" not in system_prompt
    assert "input_price_per_1k" not in body
    assert "output_price_per_1k" not in body
    assert assessment.rubric_version == "routing-requirements-v4"


def test_requirement_judge_rejects_direct_model_selection_response():
    client = _JudgeClient(_assessment_payload(selected_model_id="gpt-5.4"))

    with pytest.raises(RuntimeJudgeResponseError, match="must not select model"):
        ModelRoutingRuntimeJudge.assess_requirements(
            client=client,
            routing_feature_text="간단한 안내 요청",
        )


def test_requirement_judge_uses_safe_fallback_for_low_confidence_or_high_impact_uncertainty():
    low_confidence = ModelRoutingRuntimeJudge.assess_requirements(
        client=_JudgeClient(_assessment_payload(confidence=0.59)),
        routing_feature_text="환불 가능 여부를 확인해 주세요.",
    )
    high_impact_uncertain = ModelRoutingRuntimeJudge.assess_requirements(
        client=_JudgeClient(
            _assessment_payload(
                confidence=0.72,
                ambiguity_flags=["high_impact_uncertainty"],
            )
        ),
        routing_feature_text="고객 계정 권한을 변경해 주세요.",
    )

    assert low_confidence.requires_safe_fallback is True
    assert high_impact_uncertain.requires_safe_fallback is True


def test_requirement_judge_does_not_fallback_only_because_context_is_incomplete():
    assessment = ModelRoutingRuntimeJudge.assess_requirements(
        client=_JudgeClient(
            _assessment_payload(
                confidence=0.72,
                ambiguity_flags=["insufficient_context"],
            )
        ),
        routing_feature_text="환불 정책을 설명해 주세요.",
    )

    assert assessment.requires_safe_fallback is False


def test_requirement_judge_allows_only_safe_structural_facts_and_rag_summary():
    client = _JudgeClient(_assessment_payload())

    ModelRoutingRuntimeJudge.assess_requirements(
        client=client,
        routing_feature_text="절대 trace에 남기면 안 되는 고객 요청 원문",
        structural_facts={
            "customerTier": "enterprise",
            "outputMode": "analysis",
            "external_write_reachable": True,
            "irreversible_effect_possible": True,
        },
        rag_context={
            "used": True,
            "source_count": 3,
            "raw_document_content": "절대 Judge에 보내면 안 되는 문서 원문",
        },
    )

    body = json.loads(client.calls[0]["messages"][1]["content"])
    assert body["structural_facts"] == {
        "external_write_reachable": True,
        "irreversible_effect_possible": True,
    }
    assert body["rag_context"] == {"used": True, "source_count": 3}
    assert "raw_document_content" not in str(body)


def test_requirement_judge_retries_incomplete_response_without_changing_contract():
    client = _IncompleteThenCompactJudgeClient("")

    assessment = ModelRoutingRuntimeJudge.assess_requirements(
        client=client,
        routing_feature_text="승인 전 예외 조항과 근거 문서를 함께 검토해 주세요.",
        rag_context={"used": True, "retrieved_chunk_count": 4, "source_count": 2},
    )

    assert len(client.calls) == 2
    assert assessment.usage["prompt_tokens"] == 106
    assert assessment.usage["completion_tokens"] == 52
    assert assessment.usage["total_tokens"] == 158
    assert client.calls[0]["kwargs"]["max_tokens"] == 768
    assert client.calls[1]["kwargs"]["max_tokens"] == 768
    compact_prompt = client.calls[1]["messages"][0]["content"]
    assert "candidate_models" not in compact_prompt
    assert "selected_model_id" not in compact_prompt


def test_requirement_judge_checks_deadline_before_every_provider_attempt():
    client = _IncompleteThenCompactJudgeClient("")
    guard_calls = []

    class _DeadlineExpired(ValueError):
        pass

    def deadline_guard():
        guard_calls.append(len(client.calls))
        if len(guard_calls) == 2:
            raise _DeadlineExpired("expired before compact retry")

    with pytest.raises(_DeadlineExpired, match="expired before compact retry"):
        ModelRoutingRuntimeJudge.assess_requirements(
            client=client,
            routing_feature_text="승인 전 예외 조항을 다시 검토해 주세요.",
            deadline_guard=deadline_guard,
        )

    assert guard_calls == [0, 1]
    assert len(client.calls) == 1


def test_requirement_judge_records_provider_latency(monkeypatch):
    timestamps = iter((10.0, 10.125))
    monkeypatch.setattr(
        runtime_judge_module.time,
        "perf_counter",
        lambda: next(timestamps),
    )
    assessment = ModelRoutingRuntimeJudge.assess_requirements(
        client=_JudgeClient(_assessment_payload()),
        routing_feature_text="짧은 상태 확인 요청",
    )

    assert assessment.usage["latency_ms"] == 125
