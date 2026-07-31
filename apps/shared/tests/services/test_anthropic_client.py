import asyncio

import pytest
from apps.shared.services.llm_client.anthropic_client import AnthropicClient
from apps.shared.services.llm_client.base import LLMResponseValidationError


def _client() -> AnthropicClient:
    return AnthropicClient(
        model_id="claude-example",
        credentials={
            "apiKey": "test-placeholder",
            "baseUrl": "https://example.invalid",
        },
    )


def test_anthropic_conversion_preserves_valid_usage():
    response = _client()._convert_to_openai_format(
        {
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }
    )

    assert response["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
        "total_tokens": 15,
    }


def test_anthropic_conversion_does_not_synthesize_missing_usage():
    response = _client()._convert_to_openai_format(
        {"content": [{"type": "text", "text": "ok"}]}
    )

    assert response["usage"] == {}


def test_anthropic_invoke_preserves_safe_usage_on_invalid_content(monkeypatch):
    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "content": ["invalid-provider-content"],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "provider_detail": "must-not-cross-boundary",
                },
            }

    class MockAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, *_args, **_kwargs):
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.anthropic_client.httpx.AsyncClient",
        MockAsyncClient,
    )

    with pytest.raises(LLMResponseValidationError) as exc_info:
        asyncio.run(_client().invoke([{"role": "user", "content": "hello"}]))

    assert exc_info.value.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
    }
    assert "provider_detail" not in exc_info.value.usage
