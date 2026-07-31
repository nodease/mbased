"""
OpenAIClient 단위 테스트.
- 성공 시 HTTP 응답을 그대로 반환하는지
- 4xx/5xx일 때 ValueError를 발생시키는지
- 토큰 카운트가 최소 1 이상으로 계산되는지 검증
"""

from copy import deepcopy

import httpx
import pytest

from apps.shared.services.llm_client import OpenAIClient
from apps.shared.services.llm_client.base import (
    BaseLLMClient,
    EmbeddingProviderResult,
    LLMResponseValidationError,
    ProviderInvocationError,
)
from apps.shared.services.guarded_http_transport import GuardedHttpTransport


@pytest.mark.asyncio
async def test_openai_invoke_success(monkeypatch):
    """invoke 성공 시 응답 반환 확인"""
    messages = [{"role": "user", "content": "hi"}]
    dummy_response = {"id": "abc", "choices": []}

    class MockResponse:
        status_code = 200
        text = ""
        
        def json(self):
            return dummy_response

    class MockAsyncClient:
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def post(self, url, **kwargs):
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.AsyncClient",
        lambda **kw: MockAsyncClient()
    )

    client = OpenAIClient(
        model_id="gpt-4o",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )
    resp = await client.invoke(messages)
    assert resp == dummy_response


@pytest.mark.asyncio
async def test_openai_chat_completions_keeps_generation_params(monkeypatch):
    """Chat Completions 모델은 지원하는 생성 파라미터를 유지한다."""
    requested = {}

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": []}

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            requested["url"] = url
            requested["payload"] = kwargs["json"]
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.AsyncClient",
        lambda **_kwargs: MockAsyncClient(),
    )

    client = OpenAIClient(
        model_id="gpt-4o",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )
    parameters = {
        "temperature": 0.7,
        "top_p": 0.9,
        "presence_penalty": 0.5,
        "frequency_penalty": 0.5,
        "stop": ["END"],
    }

    await client.invoke([{"role": "user", "content": "hi"}], **parameters)

    assert requested["url"] == "https://api.openai.com/v1/chat/completions"
    for parameter, value in parameters.items():
        assert requested["payload"][parameter] == value


@pytest.mark.asyncio
async def test_openai_invoke_uses_responses_for_new_model_families(monkeypatch):
    """Responses 전용 모델군은 chat/completions를 거치지 않고 responses를 호출한다."""
    messages = [{"role": "user", "content": "hi"}]
    requested_urls = []
    requested_payloads = []

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "error": None,
                "output_text": "hello",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            requested_urls.append(url)
            requested_payloads.append(kwargs.get("json", {}))
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.AsyncClient",
        lambda **kw: MockAsyncClient()
    )

    client = OpenAIClient(
        model_id="gpt-5",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    parameters = {
        "max_tokens": 10,
        "top_p": 0.9,
        "presence_penalty": 0.5,
        "frequency_penalty": 0.5,
        "stop": ["END"],
        "text": {"verbosity": "low"},
        "response_format": {"type": "json_object"},
    }
    original_parameters = deepcopy(parameters)
    original_messages = deepcopy(messages)

    resp = await client.invoke(messages, **parameters)

    assert requested_urls == ["https://api.openai.com/v1/responses"]
    assert requested_payloads[0]["max_output_tokens"] == 10
    assert "max_tokens" not in requested_payloads[0]
    assert "max_completion_tokens" not in requested_payloads[0]
    for parameter in ("top_p", "presence_penalty", "frequency_penalty", "stop"):
        assert parameter not in requested_payloads[0]
    assert requested_payloads[0]["text"] == {
        "verbosity": "low",
        "format": {"type": "json_object"},
    }
    assert parameters == original_parameters
    assert messages == original_messages
    assert resp["choices"][0]["message"]["content"] == "hello"
    assert resp["usage"]["prompt_tokens"] == 2
    assert resp["usage"]["completion_tokens"] == 3
    assert resp["usage"]["total_tokens"] == 5


@pytest.mark.asyncio
async def test_openai_invoke_does_not_fallback_to_completions_for_responses_models(monkeypatch):
    """Responses 모델군은 responses 실패 후 legacy completions로 내려가지 않는다."""
    messages = [{"role": "user", "content": "hi"}]
    requested_urls = []

    class MockResponse:
        status_code = 404
        text = '{"error":{"message":"model not found","type":"invalid_request_error"}}'

        def json(self):
            return {
                "error": {
                    "message": "model not found",
                    "type": "invalid_request_error",
                }
            }

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            requested_urls.append(url)
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.AsyncClient",
        lambda **kw: MockAsyncClient()
    )

    client = OpenAIClient(
        model_id="o1-pro",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    with pytest.raises(ProviderInvocationError) as captured:
        await client.invoke(messages)

    assert requested_urls == ["https://api.openai.com/v1/responses"]
    assert captured.value.status_code == 404
    assert "model not found" not in str(captured.value)


@pytest.mark.asyncio
async def test_openai_invoke_failure(monkeypatch):
    """invoke 실패 시 ValueError 발생 확인"""
    messages = [{"role": "user", "content": "hi"}]

    class MockResponse:
        status_code = 401
        text = "unauthorized"

    class MockAsyncClient:
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def post(self, url, **kwargs):
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.AsyncClient",
        lambda **kw: MockAsyncClient()
    )

    client = OpenAIClient(
        model_id="gpt-4o",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )
    with pytest.raises(ValueError):
        await client.invoke(messages)


def test_openai_token_estimate():
    """토큰 수 추정 동작 확인"""
    # tiktoken 모킹
    class DummyEnc:
        def encode(self, text):
            return list(text)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.tiktoken.encoding_for_model",
        lambda *_args, **_kwargs: DummyEnc(),
    )
    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.tiktoken.get_encoding",
        lambda *_args, **_kwargs: DummyEnc(),
    )

    client = OpenAIClient(
        model_id="gpt-4o",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )
    messages = [{"role": "user", "content": "hello world"}]
    tokens = client.get_num_tokens(messages)
    assert tokens >= 1


def test_openai_embed_sync_uses_sync_http_client(monkeypatch):
    """gevent 워커 경로에서 async wrapper 없이 동기 HTTP client로 임베딩한다."""
    requested = {}

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    class MockClient:
        def __init__(self, **kwargs):
            requested["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            requested["url"] = url
            requested["payload"] = kwargs["json"]
            return MockResponse()

    def fail_async_client(*_args, **_kwargs):
        raise AssertionError("embed_sync must not use AsyncClient")

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )
    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.AsyncClient",
        fail_async_client,
    )

    client = OpenAIClient(
        model_id="text-embedding-3-small",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    assert client.embed_sync("hello") == [0.1, 0.2, 0.3]
    assert requested["client_kwargs"]["timeout"] == 30
    assert requested["client_kwargs"]["trust_env"] is False
    assert requested["client_kwargs"]["follow_redirects"] is False
    assert isinstance(requested["client_kwargs"]["transport"], GuardedHttpTransport)
    assert requested["url"] == "https://api.openai.com/v1/embeddings"
    assert requested["payload"] == {"model": "text-embedding-3-small", "input": "hello"}


def test_openai_embed_sync_request_error_is_wrapped(monkeypatch):
    """동기 임베딩 네트워크 오류는 provider 오류로 감싸고 async client를 사용하지 않는다."""

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *_args, **_kwargs):
            raise httpx.ConnectTimeout("timeout")

    def fail_async_client(*_args, **_kwargs):
        raise AssertionError("embed_sync must not use AsyncClient")

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )
    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.AsyncClient",
        fail_async_client,
    )

    client = OpenAIClient(
        model_id="text-embedding-3-small",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    with pytest.raises(ProviderInvocationError) as captured:
        client.embed_sync("hello")
    assert captured.value.reason_code == "provider_timeout"


def test_openai_embed_sync_http_error_includes_status(monkeypatch):
    """동기 임베딩 HTTP 오류는 status를 포함해 호출 실패로 보고한다."""

    class MockResponse:
        status_code = 401
        text = "unauthorized"

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *_args, **_kwargs):
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )

    client = OpenAIClient(
        model_id="text-embedding-3-small",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    with pytest.raises(ProviderInvocationError) as captured:
        client.embed_sync("hello")
    assert captured.value.status_code == 401


def test_openai_embed_sync_malformed_response_is_parse_error(monkeypatch):
    """동기 임베딩 응답이 JSON이 아니거나 shape가 다르면 파싱 실패로 고정한다."""

    class MockResponse:
        status_code = 200
        text = "not-json"

        def json(self):
            raise ValueError("bad json")

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *_args, **_kwargs):
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )

    client = OpenAIClient(
        model_id="text-embedding-3-small",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    with pytest.raises(ValueError, match="OpenAI 임베딩 응답 파싱 실패"):
        client.embed_sync("hello")


def test_openai_prepared_embedding_returns_typed_usage_and_is_single_use(monkeypatch):
    requested = {}

    class MockResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": [{"embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            }

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            requested["url"] = url
            requested["payload"] = kwargs["json"]
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )
    client = OpenAIClient(
        model_id="text-embedding-3-small",
        credentials={
            "apiKey": "redacted-test-key",
            "baseUrl": "https://api.openai.com/v1",
        },
    )
    prepared = client.prepare_embedding_invocation("sensitive-query-sentinel")

    assert prepared.canonical_request_bytes > 0
    assert prepared.requested_input_tokens == prepared.canonical_request_bytes
    assert "sensitive-query-sentinel" not in repr(prepared)
    assert prepared.invoke() == EmbeddingProviderResult(
        vector=(0.1, 0.2),
        input_tokens=3,
    )
    assert requested["payload"] == {
        "model": "text-embedding-3-small",
        "input": "sensitive-query-sentinel",
    }
    with pytest.raises(LLMResponseValidationError, match="already used"):
        prepared.invoke()


def test_openai_prepared_embedding_rejects_missing_usage(monkeypatch):
    class MockResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"embedding": [0.1]}]}

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )
    client = OpenAIClient(
        model_id="text-embedding-3-small",
        credentials={
            "apiKey": "redacted-test-key",
            "baseUrl": "https://api.openai.com/v1",
        },
    )

    with pytest.raises(LLMResponseValidationError):
        client.prepare_embedding_invocation("query").invoke()


def test_openai_invoke_sync_uses_responses_sync_client(monkeypatch):
    """GPT-5.x sync 호출은 gevent 워커에서 Responses endpoint를 동기 호출한다."""
    requested = {}

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "error": None,
                "output_text": "hello",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }

    class MockClient:
        def __init__(self, **kwargs):
            requested["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            requested["url"] = url
            requested["payload"] = kwargs["json"]
            requested["timeout"] = kwargs["timeout"]
            return MockResponse()

    def fail_async_client(*_args, **_kwargs):
        raise AssertionError("invoke_sync must not use AsyncClient for GPT-5.x")

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )
    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.AsyncClient",
        fail_async_client,
    )

    client = OpenAIClient(
        model_id="gpt-5.4-mini",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    resp = client.invoke_sync(
        [{"role": "user", "content": "hi"}],
        max_tokens=10,
        request_timeout_seconds=90,
    )

    assert requested["client_kwargs"]["timeout"] == 60
    assert requested["client_kwargs"]["trust_env"] is False
    assert requested["client_kwargs"]["follow_redirects"] is False
    assert isinstance(requested["client_kwargs"]["transport"], GuardedHttpTransport)
    assert requested["url"] == "https://api.openai.com/v1/responses"
    assert requested["payload"]["model"] == "gpt-5.4-mini"
    assert requested["payload"]["max_output_tokens"] == 10
    assert requested["payload"]["input"][0]["role"] == "user"
    assert requested["timeout"] == 90
    assert "request_timeout_seconds" not in requested["payload"]
    assert resp["choices"][0]["message"]["content"] == "hello"
    assert resp["usage"]["total_tokens"] == 5


def test_openai_invoke_sync_responses_strips_unsupported_generation_params(monkeypatch):
    """GPT-5.5 Responses 요청은 지원하지 않는 기본 LLM 파라미터를 보내지 않는다."""
    requested = {}
    default_parameters = {
        "temperature": 0.7,
        "top_p": 1.0,
        "max_tokens": 4096,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
        "stop": [],
        "text": {"verbosity": "low"},
        "response_format": {"type": "json_object"},
    }
    original_parameters = deepcopy(default_parameters)
    messages = [{"role": "user", "content": "hi"}]
    original_messages = deepcopy(messages)

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "output_text": "hello",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, _url, **kwargs):
            requested["payload"] = kwargs["json"]
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )

    client = OpenAIClient(
        model_id="gpt-5.5",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    client.invoke_sync(messages, **default_parameters)

    assert default_parameters == original_parameters
    assert messages == original_messages
    assert requested["payload"]["max_output_tokens"] == 4096
    assert requested["payload"]["temperature"] == 1
    assert requested["payload"]["text"] == {
        "verbosity": "low",
        "format": {"type": "json_object"},
    }
    for parameter in ("top_p", "presence_penalty", "frequency_penalty", "stop"):
        assert parameter not in requested["payload"]


def test_openai_responses_json_format_adds_json_word_to_input(monkeypatch):
    """Responses JSON mode는 OpenAI 검증을 위해 input message에도 json 단어를 포함한다."""
    requested = {}

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "error": None,
                "output_text": "{}",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, _url, **kwargs):
            requested["payload"] = kwargs["json"]
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )

    client = OpenAIClient(
        model_id="gpt-5-mini",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    client.invoke_sync(
        [
            {"role": "system", "content": "응답은 규칙을 따라 작성하세요."},
            {"role": "user", "content": "SLA 위반 여부를 판단해 주세요."},
        ],
        response_format={"type": "json_object"},
    )

    input_text = "\n".join(
        block["text"]
        for item in requested["payload"]["input"]
        for block in item["content"]
        if block.get("type") == "input_text"
    )
    assert "json" in input_text.casefold()
    assert requested["payload"]["text"]["format"] == {"type": "json_object"}
    assert requested["payload"]["reasoning"] == {"effort": "minimal"}


def test_openai_responses_builds_strict_json_schema_format():
    client = OpenAIClient(
        model_id="gpt-5.5-pro",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    response_format = client.build_json_schema_response_format(
        name="agent_builder_intent",
        schema={
            "type": "object",
            "properties": {
                "request_type": {"type": "string", "default": "new_workflow"},
                "edit": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {"placement": {"type": "string"}},
                        },
                        {"type": "null"},
                    ]
                },
            },
        },
    )

    assert response_format["type"] == "json_schema"
    assert response_format["name"] == "agent_builder_intent"
    assert response_format["strict"] is True
    schema = response_format["schema"]
    assert schema["required"] == ["request_type", "edit"]
    assert schema["additionalProperties"] is False
    assert "default" not in schema["properties"]["request_type"]
    nested = schema["properties"]["edit"]["anyOf"][0]
    assert nested["required"] == ["placement"]
    assert nested["additionalProperties"] is False


def test_openai_chat_builds_strict_json_schema_format():
    client = OpenAIClient(
        model_id="gpt-4.1",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    response_format = client.build_json_schema_response_format(
        name="agent_builder_intent",
        schema={
            "type": "object",
            "properties": {"request_type": {"type": "string"}},
        },
    )

    assert response_format == {
        "type": "json_schema",
        "json_schema": {
            "name": "agent_builder_intent",
            "schema": {
                "type": "object",
                "properties": {"request_type": {"type": "string"}},
                "required": ["request_type"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def test_openai_legacy_model_does_not_offer_json_schema_format():
    client = OpenAIClient(
        model_id="gpt-3.5-turbo",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    assert client.build_json_schema_response_format(
        name="agent_builder_intent",
        schema={"type": "object", "properties": {}},
    ) is None


def test_openai_responses_json_format_does_not_duplicate_json_instruction(monkeypatch):
    """기존 input에 json 지시가 있으면 보강 user block을 중복 추가하지 않는다."""
    requested = {}

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "error": None,
                "output_text": "{}",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, _url, **kwargs):
            requested["payload"] = kwargs["json"]
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )

    client = OpenAIClient(
        model_id="gpt-5-mini",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    client.invoke_sync(
        [{"role": "user", "content": "Return a json object."}],
        response_format={"type": "json_object"},
    )

    assert len(requested["payload"]["input"]) == 1
    assert requested["payload"]["input"][0]["content"][0]["text"] == (
        "Return a json object."
    )


def test_openai_responses_keeps_explicit_reasoning_effort_for_json_output():
    """명시한 reasoning 수준은 JSON mode의 안전 기본값으로 덮어쓰지 않는다."""
    client = OpenAIClient(
        model_id="gpt-5-mini",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )
    messages = [{"role": "user", "content": "Return a json object."}]

    payload = client._build_responses_request_payload(
        {
            "model": "gpt-5-mini",
            "messages": messages,
            "response_format": {"type": "json_object"},
            "reasoning": {"effort": "high"},
        },
        messages,
    )

    assert payload["reasoning"] == {"effort": "high"}


def test_openai_responses_unknown_model_omits_unverified_reasoning_effort():
    """알 수 없는 모델에는 provider가 거부할 수 있는 reasoning 값을 추정하지 않는다."""
    client = OpenAIClient(
        model_id="gpt-5-mini",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )
    messages = [{"role": "user", "content": "사내 온보딩 절차를 요약해 주세요."}]

    payload = client._build_responses_request_payload(
        {
            "model": "gpt-5-mini",
            "messages": messages,
            "max_completion_tokens": 900,
        },
        messages,
    )

    assert payload["max_output_tokens"] == 900
    assert payload["reasoning"] == {"effort": "minimal"}


def test_openai_responses_unknown_model_omits_unconfirmed_reasoning_effort():
    client = OpenAIClient(
        model_id="gpt-5.6-luna",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )
    messages = [{"role": "user", "content": "요약해 주세요."}]

    payload = client._build_responses_request_payload(
        {
            "model": "gpt-5.6-luna",
            "messages": messages,
            "max_completion_tokens": 900,
        },
        messages,
    )

    assert payload["max_output_tokens"] == 900
    assert "reasoning" not in payload


def test_openai_invoke_sync_responses_error_body_is_wrapped(monkeypatch):
    """GPT-5.x sync Responses 오류는 legacy completions로 fallback하지 않고 그대로 실패한다."""
    requested_urls = []

    class MockResponse:
        status_code = 400
        text = '{"error":{"message":"bad request","type":"invalid_request_error"}}'

        def json(self):
            return {
                "error": {
                    "message": "bad request",
                    "type": "invalid_request_error",
                }
            }

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **_kwargs):
            requested_urls.append(url)
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )

    client = OpenAIClient(
        model_id="gpt-5.4-mini",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    with pytest.raises(ProviderInvocationError) as captured:
        client.invoke_sync([{"role": "user", "content": "hi"}])

    assert requested_urls == ["https://api.openai.com/v1/responses"]
    assert captured.value.status_code == 400
    assert "bad request" not in str(captured.value)


def test_openai_invoke_sync_responses_malformed_json_is_parse_error(monkeypatch):
    """GPT-5.x sync Responses 성공 status라도 JSON 파싱 실패면 명시적 오류를 낸다."""

    class MockResponse:
        status_code = 200
        text = "not-json"

        def json(self):
            raise ValueError("bad json")

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *_args, **_kwargs):
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )

    client = OpenAIClient(
        model_id="gpt-5.4-mini",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    with pytest.raises(ValueError, match="OpenAI 응답을 JSON으로 파싱할 수 없습니다"):
        client.invoke_sync([{"role": "user", "content": "hi"}])


def test_openai_invoke_sync_responses_empty_output_is_error(monkeypatch):
    """Responses 호출이 200이어도 visible output text가 없으면 성공으로 변환하지 않는다."""

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "error": None,
                "status": "completed",
                "output": [
                    {
                        "type": "reasoning",
                        "summary": [],
                    }
                ],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 30,
                    "provider_detail": "must-not-cross-boundary",
                },
            }

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *_args, **_kwargs):
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )

    client = OpenAIClient(
        model_id="gpt-5",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    with pytest.raises(
        LLMResponseValidationError,
        match="사용할 수 있는 텍스트가 없습니다",
    ) as exc_info:
        client.invoke_sync([{"role": "user", "content": "Return a json object."}])

    assert exc_info.value.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 30,
    }
    assert "provider_detail" not in exc_info.value.usage


def test_openai_invoke_sync_responses_incomplete_status_is_error(monkeypatch):
    """Responses status가 incomplete면 부분 text가 있어도 성공으로 취급하지 않는다."""

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "error": None,
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output_text": '{"partial": true}',
                "usage": {"input_tokens": 12, "output_tokens": 30},
            }

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *_args, **_kwargs):
            return MockResponse()

    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )

    client = OpenAIClient(
        model_id="gpt-5",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    with pytest.raises(
        LLMResponseValidationError,
        match="응답이 완료되지 않았습니다",
    ) as exc_info:
        client.invoke_sync([{"role": "user", "content": "Return a json object."}])

    assert exc_info.value.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 30,
    }


def test_openai_responses_invalid_content_preserves_safe_usage():
    client = OpenAIClient(
        model_id="gpt-5",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    with pytest.raises(LLMResponseValidationError) as exc_info:
        client._convert_responses_response(
            {
                "status": "completed",
                "output": [{"content": {"type": "output_text"}}],
                "usage": {"input_tokens": 7, "output_tokens": 2},
            }
        )

    assert exc_info.value.usage == {
        "prompt_tokens": 7,
        "completion_tokens": 2,
    }


def test_openai_billable_response_validation_error_skips_legacy_fallback(
    monkeypatch,
):
    client = OpenAIClient(
        model_id="text-davinci-example",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    def fail_with_usage(**_kwargs):
        raise LLMResponseValidationError(
            "provider response invalid",
            usage={"input_tokens": 12, "output_tokens": 3},
        )

    class NoLegacyClient:
        def post(self, *_args, **_kwargs):
            raise AssertionError("billable validation errors must not call completions")

    monkeypatch.setattr(client, "_invoke_responses_endpoint_sync", fail_with_usage)

    with pytest.raises(LLMResponseValidationError) as exc_info:
        client._invoke_non_chat_model_sync(
            NoLegacyClient(),
            payload={"model": "text-davinci-example"},
            messages=[{"role": "user", "content": "hello"}],
            timeout_seconds=60,
        )

    assert exc_info.value.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
    }


def test_openai_legacy_model_falls_back_only_when_responses_endpoint_is_missing():
    client = OpenAIClient(
        model_id="text-davinci-example",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )
    requested_urls: list[str] = []

    class LegacyClient:
        def post(self, url, **_kwargs):
            requested_urls.append(url)
            if url.endswith("/responses"):
                return httpx.Response(
                    404,
                    text="<html>endpoint not found</html>",
                    headers={"content-type": "text/html"},
                )
            return httpx.Response(
                200,
                json={
                    "choices": [{"text": "legacy response", "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3},
                },
            )

    result = client._invoke_non_chat_model_sync(
        LegacyClient(),
        payload={"model": "text-davinci-example"},
        messages=[{"role": "user", "content": "hello"}],
        timeout_seconds=60,
    )

    assert requested_urls == [
        "https://api.openai.com/v1/responses",
        "https://api.openai.com/v1/completions",
    ]
    assert result["choices"][0]["message"]["content"] == "legacy response"


@pytest.mark.asyncio
async def test_openai_async_legacy_model_falls_back_when_responses_endpoint_is_missing():
    client = OpenAIClient(
        model_id="text-davinci-example",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )
    requested_urls: list[str] = []

    class LegacyAsyncClient:
        async def post(self, url, **_kwargs):
            requested_urls.append(url)
            if url.endswith("/responses"):
                return httpx.Response(
                    404,
                    text="endpoint not found",
                    headers={"content-type": "text/plain"},
                )
            return httpx.Response(
                200,
                json={
                    "choices": [{"text": "legacy response", "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3},
                },
            )

    result = await client._handle_not_chat_model(
        LegacyAsyncClient(),
        payload={"model": "text-davinci-example"},
        messages=[{"role": "user", "content": "hello"}],
        error_text="not a chat model; use completions",
    )

    assert requested_urls == [
        "https://api.openai.com/v1/responses",
        "https://api.openai.com/v1/completions",
    ]
    assert result["choices"][0]["message"]["content"] == "legacy response"


def test_openai_legacy_model_does_not_fallback_on_structured_provider_error():
    client = OpenAIClient(
        model_id="text-davinci-example",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )
    requested_urls: list[str] = []

    class StructuredErrorClient:
        def post(self, url, **_kwargs):
            requested_urls.append(url)
            return httpx.Response(
                404,
                json={
                    "error": {
                        "message": "redacted by the client boundary",
                        "code": "model_not_found",
                    }
                },
            )

    with pytest.raises(ProviderInvocationError) as captured:
        client._invoke_non_chat_model_sync(
            StructuredErrorClient(),
            payload={"model": "text-davinci-example"},
            messages=[{"role": "user", "content": "hello"}],
            timeout_seconds=60,
        )

    assert requested_urls == ["https://api.openai.com/v1/responses"]
    assert captured.value.provider_error_code == "model_not_found"
    assert "redacted by the client boundary" not in str(captured.value)


def test_openai_invoke_sync_chat_model_uses_sync_http_client(monkeypatch):
    """Gevent worker의 chat model 호출은 공유 asyncio loop를 만들지 않는다."""
    requests = []

    def fail_async_wrapper(_coro_factory):
        raise AssertionError("sync chat invocation must not create an asyncio loop")

    class MockResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "chat"}}]}

    class MockClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return MockResponse()

    monkeypatch.setattr(
        BaseLLMClient,
        "_run_coroutine_sync",
        staticmethod(fail_async_wrapper),
    )
    monkeypatch.setattr(
        "apps.shared.services.llm_client.openai_client.httpx.Client",
        MockClient,
    )

    client = OpenAIClient(
        model_id="gpt-4o",
        credentials={"apiKey": "sk-test", "baseUrl": "https://api.openai.com/v1"},
    )

    assert client.invoke_sync([{"role": "user", "content": "hi"}]) == {
        "choices": [{"message": {"content": "chat"}}]
    }
    assert requests[0][0].endswith("/chat/completions")
    assert requests[0][1]["json"]["model"] == "gpt-4o"
