import pytest

from apps.shared.services.llm_client.base import EmbeddingProviderResult
from apps.shared.services.llm_client.google_client import GoogleClient


def test_google_client_strips_internal_request_timeout_from_payload_kwargs():
    client = GoogleClient(
        model_id="gemini-2.5-flash",
        credentials={
            "apiKey": "redacted-test-key",
            "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        },
    )

    assert client._sanitize_kwargs(
        {
            "temperature": 0,
            "request_timeout_seconds": 90,
        }
    ) == {"temperature": 0}


@pytest.mark.asyncio
async def test_google_client_applies_internal_timeout_to_http_transport(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeAsyncClient:
        def __init__(self, *, timeout, **kwargs):
            captured["timeout"] = timeout
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, *, headers, json):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.google_client.httpx.AsyncClient",
        FakeAsyncClient,
    )
    client = GoogleClient(
        model_id="gemini-2.5-flash",
        credentials={
            "apiKey": "redacted-test-key",
            "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        },
    )

    await client.invoke(
        [{"role": "user", "content": "hello"}],
        temperature=0,
        request_timeout_seconds=90,
    )

    assert captured["timeout"] == 90
    assert captured["client_kwargs"]["trust_env"] is False
    assert captured["client_kwargs"]["follow_redirects"] is False
    assert captured["payload"] == {
        "model": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0,
    }


def test_google_prepared_embedding_returns_typed_usage(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": [{"embedding": [0.3, 0.4]}],
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.google_client.httpx.Client",
        FakeClient,
    )
    client = GoogleClient(
        model_id="models/text-embedding-safe",
        credentials={
            "apiKey": "redacted-test-key",
            "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        },
    )

    result = client.prepare_embedding_invocation("query").invoke()

    assert result == EmbeddingProviderResult(
        vector=(0.3, 0.4),
        input_tokens=4,
    )
