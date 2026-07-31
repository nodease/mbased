import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from apps.shared.services.credential_encryption import CredentialEncryptionService
from apps.shared.services.llm_credential_config import (
    LLMCredentialConfigError,
    LLMCredentialConfigService,
    load_llm_credential_config,
    materialize_llm_client_credentials,
    require_llm_credential_keyring_ready,
)
from apps.shared.services.llm_credential_rotation import (
    LLMCredentialRotationError,
    LLMCredentialRotationService,
)
from cryptography.fernet import Fernet


def _config_service(keys=None, active_version="v1"):
    keys = keys or {"v1": Fernet.generate_key().decode("utf-8")}
    return LLMCredentialConfigService(
        CredentialEncryptionService(
            keys,
            active_version,
            subject_label="LLM credential",
        )
    )


def test_llm_credential_config_round_trip_uses_versioned_ciphertext():
    service = _config_service()
    source = {"apiKey": "synthetic-provider-secret", "baseUrl": "https://api.test"}

    envelope = service.protect(source)
    credential = SimpleNamespace(
        encrypted_config=envelope.ciphertext,
        encryption_key_version=envelope.key_version,
        encryption_algorithm=envelope.algorithm,
    )

    assert envelope.ciphertext != json.dumps(source)
    assert envelope.key_version == "v1"
    assert service.load(credential) == source


def test_llm_credential_config_reads_only_explicit_legacy_rows_without_keyring(
    monkeypatch,
):
    monkeypatch.delenv("LLM_CREDENTIAL_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    credential = SimpleNamespace(
        encrypted_config=json.dumps(
            {"apiKey": "synthetic-legacy-secret", "baseUrl": None}
        ),
        encryption_key_version=None,
        encryption_algorithm=None,
    )

    assert load_llm_credential_config(credential)["apiKey"] == (
        "synthetic-legacy-secret"
    )


def test_llm_client_credentials_use_current_provider_endpoint_authority():
    credential = SimpleNamespace(
        encrypted_config=json.dumps(
            {
                "apiKey": "synthetic-provider-secret",
                "baseUrl": "https://stale-provider.example/v1",
            }
        ),
        encryption_key_version=None,
        encryption_algorithm=None,
    )
    provider = SimpleNamespace(base_url="https://current-provider.example/v1")

    materialized = materialize_llm_client_credentials(credential, provider)

    assert materialized == {
        "apiKey": "synthetic-provider-secret",
        "baseUrl": "https://current-provider.example/v1",
    }


@pytest.mark.parametrize("base_url", [None, "", " https://provider.example/v1"])
def test_llm_client_credentials_reject_invalid_provider_endpoint(base_url):
    credential = SimpleNamespace(
        encrypted_config=json.dumps(
            {"apiKey": "synthetic-provider-secret", "baseUrl": "https://stale"}
        ),
        encryption_key_version=None,
        encryption_algorithm=None,
    )

    with pytest.raises(LLMCredentialConfigError) as captured:
        materialize_llm_client_credentials(
            credential,
            SimpleNamespace(base_url=base_url),
        )

    assert "synthetic-provider-secret" not in str(captured.value)
    assert "provider.example" not in str(captured.value)


@pytest.mark.parametrize(
    ("key_version", "algorithm"),
    [("v1", None), (None, "fernet-v1"), ("", "fernet-v1")],
)
def test_llm_credential_config_rejects_partial_encryption_metadata(
    key_version,
    algorithm,
):
    credential = SimpleNamespace(
        encrypted_config=json.dumps({"apiKey": "synthetic-secret"}),
        encryption_key_version=key_version,
        encryption_algorithm=algorithm,
    )

    with pytest.raises(LLMCredentialConfigError, match="metadata is invalid"):
        load_llm_credential_config(credential)


def test_encrypted_config_never_falls_back_to_plaintext_on_unknown_key_version():
    service = _config_service()
    credential = SimpleNamespace(
        encrypted_config=json.dumps({"apiKey": "must-not-be-read"}),
        encryption_key_version="missing",
        encryption_algorithm="fernet-v1",
    )

    with pytest.raises(LLMCredentialConfigError) as exc:
        service.load(credential)

    rendered = str(exc.value)
    assert "must-not-be-read" not in rendered
    assert credential.encrypted_config not in rendered


def test_llm_keyring_readiness_rejects_unknown_active_version(monkeypatch):
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv(
        "LLM_CREDENTIAL_ENCRYPTION_KEYS",
        json.dumps({"v1": key}),
    )
    monkeypatch.setenv("LLM_CREDENTIAL_ACTIVE_KEY_VERSION", "v2")

    with pytest.raises(LLMCredentialConfigError, match="keyring is unavailable"):
        require_llm_credential_keyring_ready()


def test_llm_keyring_readiness_rejects_active_version_exceeding_storage_limit(
    monkeypatch,
):
    key = Fernet.generate_key().decode("utf-8")
    long_version = "v" * 65
    monkeypatch.setenv(
        "LLM_CREDENTIAL_ENCRYPTION_KEYS",
        json.dumps({long_version: key}),
    )
    monkeypatch.setenv("LLM_CREDENTIAL_ACTIVE_KEY_VERSION", long_version)

    with pytest.raises(LLMCredentialConfigError, match="keyring is invalid") as exc:
        require_llm_credential_keyring_ready()

    assert long_version not in str(exc.value)


def test_llm_keyring_does_not_silently_fallback_when_only_v2_is_configured(
    monkeypatch,
):
    monkeypatch.delenv("LLM_CREDENTIAL_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setenv("LLM_CREDENTIAL_ACTIVE_KEY_VERSION", "v2")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))

    with pytest.raises(LLMCredentialConfigError, match="keyring is unavailable"):
        require_llm_credential_keyring_ready()


def test_rotation_reencrypts_old_version_and_uses_skip_locked():
    old_key = Fernet.generate_key().decode("utf-8")
    new_key = Fernet.generate_key().decode("utf-8")
    old_service = _config_service({"v1": old_key}, "v1")
    rotating_service = _config_service({"v1": old_key, "v2": new_key}, "v2")
    old_envelope = old_service.protect(
        {"apiKey": "synthetic-old-secret", "baseUrl": None}
    )
    credential = SimpleNamespace(
        encrypted_config=old_envelope.ciphertext,
        encryption_key_version=old_envelope.key_version,
        encryption_algorithm=old_envelope.algorithm,
    )
    db = MagicMock()
    query = db.query.return_value
    selected = query.filter.return_value.order_by.return_value.with_for_update.return_value.limit.return_value
    selected.all.return_value = [credential]

    processed = LLMCredentialRotationService(
        db,
        config_service=rotating_service,
    ).rotate_batch(batch_size=10)

    assert processed == 1
    assert credential.encryption_key_version == "v2"
    assert rotating_service.load(credential)["apiKey"] == "synthetic-old-secret"
    query.filter.return_value.order_by.return_value.with_for_update.assert_called_once_with(
        skip_locked=True
    )
    db.commit.assert_called_once_with()


def test_rotation_rolls_back_malformed_legacy_batch_without_secret_in_error():
    credential = SimpleNamespace(
        encrypted_config="synthetic-malformed-secret",
        encryption_key_version=None,
        encryption_algorithm=None,
    )
    db = MagicMock()
    selected = db.query.return_value.filter.return_value.order_by.return_value.with_for_update.return_value.limit.return_value
    selected.all.return_value = [credential]

    with pytest.raises(LLMCredentialRotationError) as exc:
        LLMCredentialRotationService(
            db,
            config_service=_config_service(),
        ).rotate_batch(batch_size=10)

    assert "synthetic-malformed-secret" not in str(exc.value)
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()
