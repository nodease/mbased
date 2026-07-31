from apps.shared.services.llm_client.openai_client import OpenAIClient


def _payload_for(model_id: str) -> dict:
    client = OpenAIClient(
        model_id=model_id,
        credentials={"apiKey": "test-key", "baseUrl": "https://example.test/v1"},
    )
    return client._build_responses_request_payload(
        {"max_tokens": 320, "response_format": {"type": "json_object"}},
        [{"role": "user", "content": "JSON object로 답하세요."}],
    )


def test_gpt_54_uses_low_reasoning_effort_not_unsupported_minimal():
    assert _payload_for("gpt-5.4")["reasoning"] == {"effort": "low"}


def test_gpt_5_mini_keeps_minimal_reasoning_effort():
    assert _payload_for("gpt-5-mini")["reasoning"] == {"effort": "minimal"}


def test_gpt_51_omits_reasoning_effort_when_model_capability_is_unknown():
    payload = _payload_for("gpt-5.1")

    assert "reasoning" not in payload
