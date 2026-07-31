import inspect
from types import SimpleNamespace

import pytest
from apps.shared.services import embedding_service as embedding_module
from apps.shared.services.embedding_service import EmbeddingService
from apps.shared.services.llm_credential_config import LLMCredentialConfigError


class CredentialQuery:
    def __init__(self, credential):
        self.credential = credential

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.credential


class CredentialDb:
    def __init__(self, credential):
        self.credential = credential

    def query(self, *args, **kwargs):
        return CredentialQuery(self.credential)


def test_get_client_stops_before_provider_creation_when_config_load_fails(monkeypatch):
    credential = SimpleNamespace(
        id="credential-id",
        provider=SimpleNamespace(name="openai", base_url="https://provider.example/v1"),
    )
    service = EmbeddingService(
        db=CredentialDb(credential),
        user_id=SimpleNamespace(),
    )

    monkeypatch.setattr(
        embedding_module,
        "materialize_llm_client_credentials",
        lambda *_args: (_ for _ in ()).throw(
            LLMCredentialConfigError("safe config failure")
        ),
    )
    monkeypatch.setattr(
        embedding_module,
        "get_llm_client",
        lambda **kwargs: pytest.fail(
            "provider client must not be created for an invalid credential"
        ),
    )

    with pytest.raises(ValueError, match="Failed to initialize OpenAI client"):
        service._get_client()


def test_get_client_does_not_expose_provider_initialization_error(monkeypatch):
    sensitive_detail = "client init failed with api_key=must-not-leak"
    credential = SimpleNamespace(
        id="credential-id",
        provider=SimpleNamespace(name="openai", base_url="https://provider.example/v1"),
    )
    service = EmbeddingService(
        db=CredentialDb(credential),
        user_id=SimpleNamespace(),
    )

    monkeypatch.setattr(
        embedding_module,
        "materialize_llm_client_credentials",
        lambda *_args: {
            "apiKey": "synthetic-key",
            "baseUrl": "https://provider.example/v1",
        },
    )
    monkeypatch.setattr(
        embedding_module,
        "get_llm_client",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError(sensitive_detail)),
    )

    with pytest.raises(ValueError) as exc_info:
        service._get_client()

    assert str(exc_info.value) == "Failed to initialize OpenAI client"
    assert sensitive_detail not in str(exc_info.value)


def test_embed_batch_does_not_log_provider_error_detail(caplog):
    sensitive_detail = "provider failed with api_key=must-not-leak"

    class FailingClient:
        def embed_batch_sync(self, _texts):
            raise RuntimeError(sensitive_detail)

    service = EmbeddingService(db=SimpleNamespace(), user_id=SimpleNamespace())
    service._client = FailingClient()

    with pytest.raises(RuntimeError, match="provider failed"):
        service.embed_batch(["hello"])

    assert "RuntimeError" in caplog.text
    assert sensitive_detail not in caplog.text


def test_embedding_service_has_no_direct_provider_sdk_client():
    source = inspect.getsource(embedding_module)

    assert "import openai" not in source
    assert "openai.OpenAI" not in source
