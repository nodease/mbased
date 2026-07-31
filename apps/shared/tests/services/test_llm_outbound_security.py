from __future__ import annotations

import asyncio

import pytest
from apps.shared.services.egress_guard import EgressGuardError
from apps.shared.services.guarded_http_transport import (
    EgressResponseRejectedError,
    GuardedAsyncHttpTransport,
    GuardedHttpTransport,
)
from apps.shared.services.llm_client.anthropic_client import AnthropicClient
from apps.shared.services.llm_client.base import (
    BaseLLMClient,
    ProviderFailurePhase,
    ProviderInvocationError,
)
from apps.shared.services.llm_client.google_client import GoogleClient
from apps.shared.services.llm_client.openai_client import OpenAIClient


@pytest.mark.parametrize(
    ("client_type", "provider_name"),
    [
        (OpenAIClient, "OpenAI"),
        (GoogleClient, None),
        (AnthropicClient, None),
    ],
)
def test_llm_client_rejects_plain_http_provider_endpoint(
    client_type,
    provider_name,
) -> None:
    kwargs = {
        "model_id": "synthetic-model",
        "credentials": {
            "apiKey": "synthetic-key",
            "baseUrl": "http://provider.example/v1",
        },
    }
    if provider_name is not None:
        kwargs["provider_name"] = provider_name

    with pytest.raises(EgressGuardError) as captured:
        client_type(**kwargs)

    assert captured.value.reason_code in {
        "egress.invalid_url",
        "egress.unsupported_scheme",
    }


def test_llm_client_builds_guarded_sync_and_async_transports() -> None:
    client = OpenAIClient(
        model_id="synthetic-model",
        credentials={
            "apiKey": "synthetic-key",
            "baseUrl": "https://provider.example/v1",
        },
    )

    sync_options = client._sync_http_client_options()
    async_options = client._async_http_client_options()

    assert sync_options["trust_env"] is False
    assert sync_options["follow_redirects"] is False
    assert isinstance(sync_options["transport"], GuardedHttpTransport)
    assert async_options["trust_env"] is False
    assert async_options["follow_redirects"] is False
    assert isinstance(async_options["transport"], GuardedAsyncHttpTransport)

    sync_options["transport"].close()


def test_response_rejection_preserves_outcome_unknown_phase() -> None:
    with pytest.raises(ProviderInvocationError) as captured:
        BaseLLMClient._raise_provider_transport_error(
            EgressResponseRejectedError("egress.response_too_large")
        )

    assert captured.value.reason_code == "provider_response_rejected"
    assert captured.value.failure_phase is ProviderFailurePhase.OUTCOME_UNKNOWN


def test_openai_structured_error_does_not_expose_provider_message() -> None:
    client = OpenAIClient(
        model_id="synthetic-model",
        credentials={
            "apiKey": "synthetic-key",
            "baseUrl": "https://provider.example/v1",
        },
    )

    with pytest.raises(ProviderInvocationError) as captured:
        client._raise_error_response(
            {
                "error": {
                    "message": "prompt and secret must-not-leak",
                    "type": "invalid_request_error",
                    "code": "invalid_request",
                    "param": "messages.0.content",
                }
            },
            400,
        )

    message = str(captured.value)
    assert "must-not-leak" not in message
    assert "prompt" not in message
    assert captured.value.status_code == 400
    assert captured.value.provider_error_code == "invalid_request"


def test_openai_sync_batch_embedding_uses_guarded_transport_and_index_order(
    monkeypatch,
) -> None:
    captured_options = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": [
                    {"index": 1, "embedding": [2.0]},
                    {"index": 0, "embedding": [1.0]},
                ]
            }

    class Client:
        def __init__(self, **kwargs):
            captured_options.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            captured_options["transport"].close()

        def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        Client,
    )
    client = OpenAIClient(
        model_id="text-embedding-3-small",
        credentials={
            "apiKey": "synthetic-key",
            "baseUrl": "https://provider.example/v1",
        },
    )

    result = client.embed_batch_sync(["first", "second"])

    assert result == [[1.0], [2.0]]
    assert captured_options["trust_env"] is False
    assert captured_options["follow_redirects"] is False
    assert isinstance(captured_options["transport"], GuardedHttpTransport)


@pytest.mark.parametrize("client_type", [GoogleClient, AnthropicClient])
def test_provider_http_error_does_not_expose_response_body(
    monkeypatch,
    client_type,
) -> None:
    class Response:
        status_code = 500
        text = "provider secret must-not-leak"

        @staticmethod
        def json():
            return {"error": {"message": "provider secret must-not-leak"}}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    module = (
        "apps.shared.services.llm_client.google_client.httpx.AsyncClient"
        if client_type is GoogleClient
        else "apps.shared.services.llm_client.anthropic_client.httpx.AsyncClient"
    )
    monkeypatch.setattr(module, Client)
    client = client_type(
        model_id="synthetic-model",
        credentials={
            "apiKey": "synthetic-key",
            "baseUrl": "https://provider.example/v1",
        },
    )

    with pytest.raises(ProviderInvocationError) as captured:
        asyncio.run(client.invoke([{"role": "user", "content": "hello"}]))

    assert "must-not-leak" not in str(captured.value)
    assert "secret" not in str(captured.value)
    assert captured.value.status_code == 500
