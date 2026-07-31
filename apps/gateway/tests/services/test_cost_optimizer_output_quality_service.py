import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from apps.gateway.services.cost_optimizer_output_quality_service import (
    CostOptimizerOutputQualityService,
)


class _JudgeClient:
    def __init__(self, response):
        self.response = response
        self.messages = None

    def invoke_sync(self, messages, **kwargs):
        self.messages = messages
        return self.response


def _judge_response():
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "variant_left": {
                                "instruction_fulfillment": 71,
                                "relevance_completeness": 74,
                                "clarity_consistency": 70,
                                "factual_reliability": 70,
                            },
                            "variant_right": {
                                "instruction_fulfillment": 86,
                                "relevance_completeness": 82,
                                "clarity_consistency": 84,
                                "factual_reliability": 85,
                            },
                            "confidence": 0.82,
                            "safe_summary": "한 출력이 더 간결하고 요청 범위를 충족합니다.",
                        }
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
    }


def test_fr13_quality_judge_uses_blind_pairwise_variants_and_records_usage():
    db = MagicMock()
    workflow = SimpleNamespace(id=uuid4(), organization_id=uuid4())
    current_user = SimpleNamespace(id=uuid4())
    candidate_row = SimpleNamespace(id=uuid4())
    judge_log = SimpleNamespace(id=uuid4(), total_cost=0.0004)
    client = _JudgeClient(_judge_response())

    with (
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.get_my_available_models",
            return_value=[
                SimpleNamespace(model_id_for_api_call="gpt-4.1-mini", type="chat")
            ],
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.calculate_cost",
            return_value=0.0004,
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.log_usage",
            return_value=judge_log,
        ) as log_usage,
    ):
        result = CostOptimizerOutputQualityService.evaluate(
            db=db,
            workflow=workflow,
            current_user=current_user,
            node_id="llm-triage",
            candidate_row=candidate_row,
            baseline={
                "input": {
                    "message": "정산 파일을 다시 생성해 주세요.",
                    "apiKey": "camel-case-secret",
                    "access_token": "access-token-secret",
                    "password": "password-secret",
                    "private_key": "-----BEGIN PRIVATE KEY-----private-secret-----END PRIVATE KEY-----",
                },
                "output": {"text": "처리하겠습니다."},
            },
            candidate_result={
                "output": {
                    "text": "정산 파일을 재생성하고 결과를 안내하겠습니다.",
                    "usage": {"total_tokens": 200},
                    "metaData": {
                        "rag_summary": {
                            "rawChunkContent": "judge에 보내면 안 되는 원문"
                        },
                        "api_key": "must-not-reach-judge",
                    },
                }
            },
            judge_client=client,
            judge_model_id="gpt-4.1-mini",
            pair_order="baseline_left",
        )

    assert result["status"] == "completed"
    assert result["baseline"]["score"] == 71
    assert result["candidate"]["score"] == 84
    assert result["delta"] == 13
    assert result["confidence"] == "high"
    assert result["judge_cost"] == 0.0004
    assert result["judge_usage_log_id"] == str(judge_log.id)
    payload = json.loads(client.messages[1]["content"])
    assert set(payload) >= {"variant_left", "variant_right"}
    assert "baseline" not in payload
    assert "candidate" not in payload
    assert payload["variant_right"]["output"] == {
        "text": "정산 파일을 재생성하고 결과를 안내하겠습니다."
    }
    assert "must-not-reach-judge" not in client.messages[1]["content"]
    assert "judge에 보내면 안 되는 원문" not in client.messages[1]["content"]
    assert "camel-case-secret" not in client.messages[1]["content"]
    assert "access-token-secret" not in client.messages[1]["content"]
    assert "password-secret" not in client.messages[1]["content"]
    assert "private-secret" not in client.messages[1]["content"]
    log_usage.assert_called_once()
    assert log_usage.call_args.kwargs["node_id"] == "llm-triage:quality-judge"


def test_fr13_quality_judge_restores_candidate_left_scores_to_original_variants():
    judge_log = SimpleNamespace(id=uuid4(), total_cost=0.0004)
    client = _JudgeClient(_judge_response())

    with (
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service."
            "LLMService.calculate_cost",
            return_value=0.0004,
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service."
            "LLMService.log_usage",
            return_value=judge_log,
        ),
    ):
        result = CostOptimizerOutputQualityService.evaluate(
            db=MagicMock(),
            workflow=SimpleNamespace(id=uuid4(), organization_id=uuid4()),
            current_user=SimpleNamespace(id=uuid4()),
            node_id="llm-triage",
            candidate_row=SimpleNamespace(id=uuid4()),
            baseline={"input": {"message": "문의"}, "output": {"text": "A"}},
            candidate_result={"input": {"message": "문의"}, "output": {"text": "B"}},
            judge_client=client,
            judge_model_id="gpt-4.1-mini",
            pair_order="candidate_left",
        )

    assert result["baseline"]["score"] == 84
    assert result["candidate"]["score"] == 71
    assert result["delta"] == -13


def test_fr13_quality_judge_records_usage_when_response_cannot_be_scored():
    judge_log = SimpleNamespace(id=uuid4(), total_cost=0.0004)
    client = _JudgeClient(
        {
            "choices": [{"message": {"content": "not-json"}}],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
        }
    )

    with (
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service."
            "LLMService.calculate_cost",
            return_value=0.0004,
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service."
            "LLMService.log_usage",
            return_value=judge_log,
        ) as log_usage,
    ):
        result = CostOptimizerOutputQualityService.evaluate(
            db=MagicMock(),
            workflow=SimpleNamespace(id=uuid4(), organization_id=uuid4()),
            current_user=SimpleNamespace(id=uuid4()),
            node_id="llm-triage",
            candidate_row=SimpleNamespace(id=uuid4()),
            baseline={"input": {"message": "문의"}, "output": {"text": "A"}},
            candidate_result={"input": {"message": "문의"}, "output": {"text": "B"}},
            judge_client=client,
            judge_model_id="gpt-4.1-mini",
            pair_order="baseline_left",
        )

    assert result["status"] == "unavailable"
    assert result["judge_cost"] == 0.0004
    assert result["judge_usage_log_id"] == str(judge_log.id)
    assert result["judge"]["usage"]["total_tokens"] == 200
    log_usage.assert_called_once()


@pytest.mark.parametrize(
    "judge_payload",
    [
        {
            "variant_left": {
                "instruction_fulfillment": 71,
                "relevance_completeness": 74,
            },
            "variant_right": {
                "instruction_fulfillment": 86,
                "relevance_completeness": 82,
                "clarity_consistency": 84,
                "factual_reliability": 85,
            },
            "confidence": 0.82,
        },
        {
            "variant_left": {
                "instruction_fulfillment": 71,
                "relevance_completeness": 74,
                "clarity_consistency": 70,
                "factual_reliability": 70,
            },
            "variant_right": {
                "instruction_fulfillment": 86,
                "relevance_completeness": 82,
                "clarity_consistency": 84,
                "factual_reliability": 85,
            },
        },
        {
            "variant_left": {
                "instruction_fulfillment": 71,
                "relevance_completeness": 74,
                "clarity_consistency": 70,
                "factual_reliability": 70,
            },
            "variant_right": {
                "instruction_fulfillment": 86,
                "relevance_completeness": 82,
                "clarity_consistency": 84,
                "factual_reliability": 85,
            },
            "confidence": "NaN",
        },
        {
            "variant_left": {
                "instruction_fulfillment": 101,
                "relevance_completeness": 74,
                "clarity_consistency": 70,
                "factual_reliability": 70,
            },
            "variant_right": {
                "instruction_fulfillment": 86,
                "relevance_completeness": 82,
                "clarity_consistency": 84,
                "factual_reliability": 85,
            },
            "confidence": 0.82,
        },
    ],
    ids=[
        "missing-dimension",
        "missing-confidence",
        "non-finite-confidence",
        "out-of-range-dimension",
    ],
)
def test_fr13_quality_judge_rejects_incomplete_contract_but_records_usage(
    judge_payload,
):
    judge_log = SimpleNamespace(id=uuid4(), total_cost=0.0004)
    client = _JudgeClient(
        {
            "choices": [
                {"message": {"content": json.dumps(judge_payload)}}
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
        }
    )

    with (
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service."
            "LLMService.calculate_cost",
            return_value=0.0004,
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service."
            "LLMService.log_usage",
            return_value=judge_log,
        ) as log_usage,
    ):
        result = CostOptimizerOutputQualityService.evaluate(
            db=MagicMock(),
            workflow=SimpleNamespace(id=uuid4(), organization_id=uuid4()),
            current_user=SimpleNamespace(id=uuid4()),
            node_id="llm-triage",
            candidate_row=SimpleNamespace(id=uuid4()),
            baseline={"input": {"message": "문의"}, "output": {"text": "A"}},
            candidate_result={"input": {"message": "문의"}, "output": {"text": "B"}},
            judge_client=client,
            judge_model_id="gpt-4.1-mini",
            pair_order="baseline_left",
        )

    assert result["status"] == "unavailable"
    assert result["judge_cost"] == 0.0004
    log_usage.assert_called_once()


def test_fr13_quality_judge_ignores_dimensions_outside_the_requested_rubric():
    response = _judge_response()
    payload = json.loads(response["choices"][0]["message"]["content"])
    payload["variant_left"]["unrequested_dimension"] = 0
    payload["variant_right"]["unrequested_dimension"] = 100
    response["choices"][0]["message"]["content"] = json.dumps(payload)

    with (
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service."
            "LLMService.calculate_cost",
            return_value=0.0004,
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service."
            "LLMService.log_usage",
            return_value=SimpleNamespace(id=uuid4(), total_cost=0.0004),
        ),
    ):
        result = CostOptimizerOutputQualityService.evaluate(
            db=MagicMock(),
            workflow=SimpleNamespace(id=uuid4(), organization_id=uuid4()),
            current_user=SimpleNamespace(id=uuid4()),
            node_id="llm-triage",
            candidate_row=SimpleNamespace(id=uuid4()),
            baseline={"input": {"message": "문의"}, "output": {"text": "A"}},
            candidate_result={"input": {"message": "문의"}, "output": {"text": "B"}},
            judge_client=_JudgeClient(response),
            judge_model_id="gpt-4.1-mini",
            pair_order="baseline_left",
        )

    assert result["baseline"]["score"] == 71
    assert result["candidate"]["score"] == 84
    assert "unrequested_dimension" not in result["dimensions"]


def test_fr13_quality_judge_fails_closed_before_provider_call_when_redaction_fails():
    client = _JudgeClient(_judge_response())

    with patch(
        "apps.gateway.services.cost_optimizer_output_quality_service."
        "TraceRedactionService.redact_payload",
        return_value=SimpleNamespace(
            failed=True,
            redacted_payload={"redaction_failed": True},
        ),
    ):
        result = CostOptimizerOutputQualityService.evaluate(
            db=MagicMock(),
            workflow=SimpleNamespace(id=uuid4(), organization_id=uuid4()),
            current_user=SimpleNamespace(id=uuid4()),
            node_id="llm-triage",
            candidate_row=SimpleNamespace(id=uuid4()),
            baseline={
                "input": {"apiKey": "must-not-leak"},
                "output": {"text": "A"},
            },
            candidate_result={"output": {"text": "B"}},
            judge_client=client,
            judge_model_id="gpt-4.1-mini",
            pair_order="baseline_left",
        )

    assert result["status"] == "unavailable"
    assert result["safe_summary"] == "품질 평가를 완료하지 못했습니다."
    assert client.messages is None


def test_fr13_quality_judge_bounds_payload_before_common_redaction():
    captured_payloads = []

    def capture_redaction(payload, policy, payload_kind):
        del policy, payload_kind
        captured_payloads.append(payload)
        return SimpleNamespace(failed=False, redacted_payload=payload)

    with patch(
        "apps.gateway.services.cost_optimizer_output_quality_service."
        "TraceRedactionService.redact_payload",
        side_effect=capture_redaction,
    ):
        result = CostOptimizerOutputQualityService._judge_visible_value(
            {
                "items": list(range(60)),
                "text": "x" * 13000,
                "nested": {"a": {"b": {"c": {"d": {"e": "too-deep"}}}}},
            }
        )

    assert len(captured_payloads) == 1
    bounded = captured_payloads[0]
    assert len(bounded["items"]) == 51
    assert bounded["items"][-1] == "[TRUNCATED]"
    assert len(bounded["text"]) == 12000
    assert "too-deep" not in str(bounded)
    assert result == bounded


def test_fr13_quality_judge_bounds_cyclic_payload_before_recursive_filtering():
    cyclic_payload = {"message": "문의"}
    cyclic_payload["self"] = cyclic_payload

    with patch(
        "apps.gateway.services.cost_optimizer_output_quality_service."
        "TraceRedactionService.redact_payload",
        side_effect=lambda payload, policy, payload_kind: SimpleNamespace(
            failed=False,
            redacted_payload=payload,
        ),
    ):
        result = CostOptimizerOutputQualityService._judge_visible_value(
            cyclic_payload
        )

    assert "[TRUNCATED]" in str(result)


def test_fr13_quality_judge_is_partial_when_no_usable_judge_model_exists():
    db = MagicMock()

    with patch(
        "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.get_my_available_models",
        return_value=[],
    ):
        result = CostOptimizerOutputQualityService.evaluate(
            db=db,
            workflow=SimpleNamespace(id=uuid4(), organization_id=uuid4()),
            current_user=SimpleNamespace(id=uuid4()),
            node_id="llm-triage",
            candidate_row=SimpleNamespace(id=uuid4()),
            baseline={"input": {"message": "문의"}, "output": {"text": "A"}},
            candidate_result={"output": {"text": "B"}},
        )

    assert result == {
        "status": "unavailable",
        "baseline": {"score": None},
        "candidate": {"score": None},
        "delta": None,
        "dimensions": {},
        "confidence": "unavailable",
        "safe_summary": "품질 평가에 사용할 수 있는 LLM credential/model이 없습니다.",
        "judge_cost": None,
        "judge_usage_log_id": None,
    }


def test_fr13_quality_judge_tries_next_model_when_first_model_is_not_usable_in_org():
    db = MagicMock()
    workflow = SimpleNamespace(id=uuid4(), organization_id=uuid4())
    current_user = SimpleNamespace(id=uuid4())
    candidate_row = SimpleNamespace(id=uuid4())
    usable_client = _JudgeClient(_judge_response())

    with (
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.get_my_available_models",
            return_value=[
                SimpleNamespace(model_id_for_api_call="unavailable-model", type="chat"),
                SimpleNamespace(model_id_for_api_call="usable-model", type="chat"),
            ],
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.get_client_for_user",
            side_effect=[ValueError("credential denied"), usable_client],
        ) as get_client,
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.calculate_cost",
            return_value=0.0004,
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.log_usage",
            return_value=SimpleNamespace(id=uuid4(), total_cost=0.0004),
        ),
    ):
        result = CostOptimizerOutputQualityService.evaluate(
            db=db,
            workflow=workflow,
            current_user=current_user,
            node_id="llm-triage",
            candidate_row=candidate_row,
            baseline={"input": {"message": "문의"}, "output": {"text": "A"}},
            candidate_result={"input": {"message": "문의"}, "output": {"text": "B"}},
            pair_order="baseline_left",
        )

    assert result["status"] == "completed"
    assert result["judge"]["model_id"] == "usable-model"
    assert get_client.call_count == 2


def test_fr13_quality_judge_skips_legacy_completion_model_mislabeled_as_chat():
    db = MagicMock()
    workflow = SimpleNamespace(id=uuid4(), organization_id=uuid4())
    current_user = SimpleNamespace(id=uuid4())
    candidate_row = SimpleNamespace(id=uuid4())
    usable_client = _JudgeClient(_judge_response())

    with (
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.get_my_available_models",
            return_value=[
                SimpleNamespace(
                    model_id_for_api_call="babbage-002",
                    name="Babbage 002",
                    type="chat",
                    is_active=True,
                ),
                SimpleNamespace(
                    model_id_for_api_call="gpt-4.1-mini",
                    name="GPT-4.1 mini",
                    type="chat",
                    is_active=True,
                ),
            ],
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.get_client_for_user",
            return_value=usable_client,
        ) as get_client,
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.calculate_cost",
            return_value=0.0004,
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.log_usage",
            return_value=SimpleNamespace(id=uuid4(), total_cost=0.0004),
        ),
    ):
        result = CostOptimizerOutputQualityService.evaluate(
            db=db,
            workflow=workflow,
            current_user=current_user,
            node_id="llm-triage",
            candidate_row=candidate_row,
            baseline={"input": {"message": "문의"}, "output": {"text": "A"}},
            candidate_result={"input": {"message": "문의"}, "output": {"text": "B"}},
            pair_order="baseline_left",
        )

    assert result["status"] == "completed"
    assert result["judge"]["model_id"] == "gpt-4.1-mini"
    assert get_client.call_args.args[2] == "gpt-4.1-mini"


@pytest.mark.parametrize(
    ("available_models", "expected_model_id"),
    [
        (
            [
                SimpleNamespace(
                    model_id_for_api_call="claude-haiku-4-5-20251001",
                    name="Claude Haiku 4.5",
                    type="chat",
                    is_active=True,
                ),
                SimpleNamespace(
                    model_id_for_api_call="claude-sonnet-4-5-20250929",
                    name="Claude Sonnet 4.5",
                    type="chat",
                    is_active=True,
                ),
            ],
            "claude-sonnet-4-5-20250929",
        ),
        (
            [
                SimpleNamespace(
                    model_id_for_api_call="models/gemini-2.5-flash-lite",
                    name="Gemini 2.5 Flash-Lite",
                    type="chat",
                    is_active=True,
                ),
                SimpleNamespace(
                    model_id_for_api_call="models/gemini-2.5-flash",
                    name="Gemini 2.5 Flash",
                    type="chat",
                    is_active=True,
                ),
            ],
            "models/gemini-2.5-flash",
        ),
    ],
)
def test_fr13_quality_judge_prefers_balanced_model_for_single_provider_org(
    available_models,
    expected_model_id,
):
    with patch(
        "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.get_my_available_models",
        return_value=available_models,
    ):
        selected = CostOptimizerOutputQualityService._select_judge_model(
            db=MagicMock(),
            user_id=uuid4(),
        )

    assert selected == expected_model_id


def test_fr13_quality_judge_adds_groundedness_only_for_rag_variants():
    response = _judge_response()
    judge_payload = json.loads(response["choices"][0]["message"]["content"])
    judge_payload["variant_left"]["groundedness"] = 70
    judge_payload["variant_right"]["groundedness"] = 80
    response["choices"][0]["message"]["content"] = json.dumps(judge_payload)
    client = _JudgeClient(response)

    with (
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.calculate_cost",
            return_value=0.0004,
        ),
        patch(
            "apps.gateway.services.cost_optimizer_output_quality_service.LLMService.log_usage",
            return_value=SimpleNamespace(id=uuid4(), total_cost=0.0004),
        ),
    ):
        result = CostOptimizerOutputQualityService.evaluate(
            db=MagicMock(),
            workflow=SimpleNamespace(id=uuid4(), organization_id=uuid4()),
            current_user=SimpleNamespace(id=uuid4()),
            node_id="llm-triage",
            candidate_row=SimpleNamespace(id=uuid4()),
            baseline={
                "input": {"message": "문의"},
                "output": {"text": "A"},
                "trace": {"rag_summary": {"retrieved_chunk_count": 3}},
            },
            candidate_result={"input": {"message": "문의"}, "output": {"text": "B"}},
            judge_client=client,
            judge_model_id="gpt-4.1-mini",
            pair_order="baseline_left",
        )

    payload = json.loads(client.messages[1]["content"])
    assert "groundedness" in payload["dimensions"]
    assert payload["variant_left"]["rag_summary"] == {"retrieved_chunk_count": 3}
    assert result["status"] == "completed"
    assert "groundedness" in result["dimensions"]


def test_quality_judge_does_not_reward_unsupported_specific_instructions():
    """권위 있는 근거가 없으면 그럴듯한 추측보다 불확실성 공개를 우선한다."""
    messages = CostOptimizerOutputQualityService._build_messages(
        baseline={"input": {"message": "버튼 위치를 알려 주세요."}, "output": {"text": "오른쪽 위입니다."}},
        candidate_result={
            "input": {"message": "버튼 위치를 알려 주세요."},
            "output": {"text": "근거가 없어 정확한 위치를 확인할 수 없습니다."},
        },
        pair_order="baseline_left",
        dimensions=CostOptimizerOutputQualityService.BASE_DIMENSIONS,
    )

    payload = json.loads(messages[1]["content"])

    assert "factual_reliability" in CostOptimizerOutputQualityService.BASE_DIMENSIONS
    assert payload["evaluation_policy"]["unsupported_specific_claims"] == "penalize"
    assert payload["evaluation_policy"]["transparent_uncertainty"] == "do_not_penalize"
    assert payload["variant_left"]["authoritative_evidence_available"] is False
    assert payload["variant_right"]["authoritative_evidence_available"] is False
    assert "authoritative evidence" in messages[0]["content"]


@pytest.mark.parametrize(
    ("rag_summary", "expected"),
    [
        (
            {"retrieved_chunk_count": 2, "evidence_sufficient": True},
            True,
        ),
        ({"retrieved_chunk_count": 0, "evidence_sufficient": True}, False),
        ({"retrieved_chunk_count": 2, "evidence_sufficient": False}, False),
        ({"retrieved_chunk_count": "2", "evidence_sufficient": True}, False),
        ({"retrieved_chunk_count": 0.5, "evidence_sufficient": True}, False),
        ({"retrieved_chunk_count": 1.0, "evidence_sufficient": True}, False),
        ({"retrieved_chunk_count": True, "evidence_sufficient": True}, False),
        ({"evidence_sufficient": True}, False),
        ("malformed", False),
    ],
)
def test_quality_judge_requires_sufficient_positive_rag_evidence(
    rag_summary,
    expected,
):
    variant = CostOptimizerOutputQualityService._variant_from_result(
        {
            "input": {"message": "정책을 알려 주세요."},
            "output": {"text": "답변"},
            "trace": {"rag_summary": rag_summary},
        }
    )

    assert variant["authoritative_evidence_available"] is expected
