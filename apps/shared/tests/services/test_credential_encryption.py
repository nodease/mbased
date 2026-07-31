import json

import pytest
from apps.shared.services.credential_encryption import (
    CredentialEncryptionError,
    CredentialEncryptionService,
    EncryptedSecretEnvelope,
    require_mail_credential_keyring_ready,
)
from cryptography.fernet import Fernet


def test_mail_credential_encryption_round_trip_and_version():
    service = CredentialEncryptionService(
        {"v1": Fernet.generate_key().decode("utf-8")}, "v1"
    )
    source = "synthetic-mail-secret"

    envelope = service.encrypt(source)

    assert envelope.ciphertext != source
    assert envelope.key_version == "v1"
    assert service.decrypt(envelope) == source


def test_mail_credential_decryption_rejects_unknown_key_version_safely():
    service = CredentialEncryptionService(
        {"v1": Fernet.generate_key().decode("utf-8")}, "v1"
    )
    envelope = EncryptedSecretEnvelope(
        ciphertext="synthetic-ciphertext",
        key_version="missing",
    )

    with pytest.raises(CredentialEncryptionError) as exc:
        service.decrypt(envelope)

    assert "synthetic-ciphertext" not in str(exc.value)


def test_environment_keyring_encrypts_with_new_key_and_decrypts_old_key(monkeypatch):
    old_key = Fernet.generate_key().decode("utf-8")
    new_key = Fernet.generate_key().decode("utf-8")
    old_service = CredentialEncryptionService({"v1": old_key}, "v1")
    old_envelope = old_service.encrypt("synthetic-old-secret")
    monkeypatch.setenv(
        "MAIL_CREDENTIAL_ENCRYPTION_KEYS",
        json.dumps({"v1": old_key, "v2": new_key}),
    )
    monkeypatch.setenv("MAIL_CREDENTIAL_ACTIVE_KEY_VERSION", "v2")

    rotated_service = CredentialEncryptionService.from_environment()
    new_envelope = rotated_service.encrypt("synthetic-new-secret")

    assert rotated_service.decrypt(old_envelope) == "synthetic-old-secret"
    assert new_envelope.key_version == "v2"
    assert rotated_service.decrypt(new_envelope) == "synthetic-new-secret"


def test_mail_keyring_readiness_rejects_unknown_active_version(monkeypatch):
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("MAIL_CREDENTIAL_ENCRYPTION_KEYS", json.dumps({"v1": key}))
    monkeypatch.setenv("MAIL_CREDENTIAL_ACTIVE_KEY_VERSION", "v2")

    with pytest.raises(CredentialEncryptionError, match="unavailable"):
        require_mail_credential_keyring_ready()
