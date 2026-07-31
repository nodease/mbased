from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from apps.shared.schemas.llm import LLMCredentialCreate
from apps.shared.services.llm_credential_config import LLMCredentialConfigError
from apps.workflow_engine.services import llm_service as service_module
from apps.workflow_engine.services.llm_service import LLMService


class Query:
    def __init__(self, value):
        self.value = value

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value


class Db:
    def __init__(self, value):
        self.value = value
        self.added = []
        self.committed = False

    def query(self, *args, **kwargs):
        return Query(self.value)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        now = datetime.now(timezone.utc)
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()
            if getattr(value, "created_at", None) is None:
                value.created_at = now
            if getattr(value, "updated_at", None) is None:
                value.updated_at = now

    def commit(self):
        self.committed = True

    def refresh(self, value):
        self.refreshed = value


def test_workflow_credential_registration_writes_encryption_envelope(monkeypatch):
    provider = SimpleNamespace(
        id=uuid.uuid4(),
        name="openai",
        base_url="https://api.example",
    )
    request = LLMCredentialCreate(
        provider_id=provider.id,
        credential_name="runtime",
        api_key="synthetic-key",
    )
    db = Db(provider)

    monkeypatch.setattr(
        service_module,
        "protect_llm_credential_config",
        lambda config: SimpleNamespace(
            ciphertext="synthetic-ciphertext",
            key_version="v2",
            algorithm="fernet-v1",
        ),
    )
    monkeypatch.setattr(LLMService, "_fetch_remote_models", lambda *args: [])
    monkeypatch.setattr(LLMService, "_sync_models_to_db", lambda *args: [])
    monkeypatch.setattr(
        service_module.LLMCredentialResponse,
        "model_validate",
        staticmethod(lambda credential: credential),
    )

    credential = LLMService.register_credential(db, uuid.uuid4(), request)

    assert credential.encrypted_config == "synthetic-ciphertext"
    assert credential.encryption_key_version == "v2"
    assert credential.encryption_algorithm == "fernet-v1"
    assert db.committed is True


def test_workflow_runtime_stops_before_provider_creation_on_decrypt_failure(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    model = SimpleNamespace(
        id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_id_for_api_call="gpt-test",
        name="GPT Test",
        is_active=True,
    )
    credential = SimpleNamespace(
        id=uuid.uuid4(),
        provider=SimpleNamespace(name="openai"),
    )
    db = Db(model)

    monkeypatch.setattr(
        LLMService,
        "_get_runtime_credential_for_user",
        staticmethod(lambda *args, **kwargs: credential),
    )
    monkeypatch.setattr(
        service_module,
        "materialize_llm_client_credentials",
        lambda *args: (_ for _ in ()).throw(
            LLMCredentialConfigError("safe config failure")
        ),
    )
    monkeypatch.setattr(
        service_module,
        "get_llm_client",
        lambda **kwargs: pytest.fail(
            "provider client must not be created for an invalid credential"
        ),
    )

    with pytest.raises(ValueError, match="Invalid credential config"):
        LLMService.get_runtime_client_for_user(
            db,
            user_id=uuid.uuid4(),
            model_id="gpt-test",
            organization_id=organization_id,
        )
