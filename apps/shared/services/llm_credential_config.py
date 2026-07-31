from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from apps.shared.services.credential_encryption import (
    DEFAULT_KEY_VERSION,
    FERNET_ENCRYPTION_ALGORITHM,
    CredentialEncryptionError,
    CredentialEncryptionService,
    EncryptedSecretEnvelope,
)

LLM_CREDENTIAL_KEYRING_ENV = "LLM_CREDENTIAL_ENCRYPTION_KEYS"
LLM_CREDENTIAL_ACTIVE_KEY_VERSION_ENV = "LLM_CREDENTIAL_ACTIVE_KEY_VERSION"
LLM_CREDENTIAL_ALGORITHM = FERNET_ENCRYPTION_ALGORITHM
LLM_CREDENTIAL_KEY_VERSION_MAX_LENGTH = 64


class LLMCredentialConfigError(ValueError):
    """Safe LLM credential config failure without secret-bearing details."""


class LLMCredentialConfigService:
    """Single encryption and validation boundary for stored LLM config."""

    def __init__(self, encryption: CredentialEncryptionService):
        active_key_version = encryption.active_key_version
        if (
            not isinstance(active_key_version, str)
            or not active_key_version.strip()
            or len(active_key_version) > LLM_CREDENTIAL_KEY_VERSION_MAX_LENGTH
        ):
            raise LLMCredentialConfigError(
                "LLM credential encryption keyring is invalid."
            )
        self._encryption = encryption

    @classmethod
    def from_environment(cls) -> "LLMCredentialConfigService":
        raw_keyring = os.getenv(LLM_CREDENTIAL_KEYRING_ENV)
        configured_active_version = os.getenv(LLM_CREDENTIAL_ACTIVE_KEY_VERSION_ENV)
        if not raw_keyring and configured_active_version not in (
            None,
            DEFAULT_KEY_VERSION,
        ):
            raise LLMCredentialConfigError(
                "LLM credential encryption keyring is unavailable."
            )
        try:
            return cls(
                CredentialEncryptionService.from_environment_variables(
                    keyring_environment_variable=LLM_CREDENTIAL_KEYRING_ENV,
                    active_version_environment_variable=(
                        LLM_CREDENTIAL_ACTIVE_KEY_VERSION_ENV
                    ),
                    subject_label="LLM credential",
                    algorithm=LLM_CREDENTIAL_ALGORITHM,
                )
            )
        except CredentialEncryptionError as exc:
            raise LLMCredentialConfigError(
                "LLM credential encryption keyring is unavailable."
            ) from exc

    @property
    def active_key_version(self) -> str:
        return self._encryption.active_key_version

    @property
    def algorithm(self) -> str:
        return self._encryption.algorithm

    def protect(self, config: Mapping[str, Any]) -> EncryptedSecretEnvelope:
        normalized = self._validate_config(config)
        serialized = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            return self._encryption.encrypt(serialized)
        except CredentialEncryptionError as exc:
            raise LLMCredentialConfigError(
                "LLM credential configuration could not be protected."
            ) from exc

    def load(self, credential: Any) -> dict[str, Any]:
        ciphertext = getattr(credential, "encrypted_config", None)
        key_version = getattr(credential, "encryption_key_version", None)
        algorithm = getattr(credential, "encryption_algorithm", None)

        if not isinstance(ciphertext, str) or not ciphertext:
            raise LLMCredentialConfigError(
                "LLM credential configuration is unavailable."
            )

        if key_version is None and algorithm is None:
            serialized = ciphertext
        elif not key_version or not algorithm:
            raise LLMCredentialConfigError(
                "LLM credential encryption metadata is invalid."
            )
        else:
            try:
                serialized = self._encryption.decrypt(
                    EncryptedSecretEnvelope(
                        ciphertext=ciphertext,
                        key_version=str(key_version),
                        algorithm=str(algorithm),
                    )
                )
            except CredentialEncryptionError as exc:
                raise LLMCredentialConfigError(
                    "LLM credential configuration is unavailable."
                ) from exc

        return self._decode_serialized_config(serialized)

    @classmethod
    def load_legacy(cls, credential: Any) -> dict[str, Any]:
        serialized = getattr(credential, "encrypted_config", None)
        if not isinstance(serialized, str) or not serialized:
            raise LLMCredentialConfigError(
                "LLM credential configuration is unavailable."
            )
        return cls._decode_serialized_config(serialized)

    @classmethod
    def _decode_serialized_config(cls, serialized: str) -> dict[str, Any]:
        try:
            decoded = json.loads(serialized)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMCredentialConfigError(
                "LLM credential configuration is unavailable."
            ) from exc
        return cls._validate_config(decoded)

    @staticmethod
    def _validate_config(config: Any) -> dict[str, Any]:
        if not isinstance(config, Mapping):
            raise LLMCredentialConfigError("LLM credential configuration is invalid.")

        api_key = config.get("apiKey")
        base_url = config.get("baseUrl")
        if not isinstance(api_key, str) or not api_key:
            raise LLMCredentialConfigError("LLM credential configuration is invalid.")
        if base_url is not None and not isinstance(base_url, str):
            raise LLMCredentialConfigError("LLM credential configuration is invalid.")
        return {"apiKey": api_key, "baseUrl": base_url}


def get_llm_credential_config_service() -> LLMCredentialConfigService:
    return LLMCredentialConfigService.from_environment()


def protect_llm_credential_config(
    config: Mapping[str, Any],
) -> EncryptedSecretEnvelope:
    return get_llm_credential_config_service().protect(config)


def load_llm_credential_config(credential: Any) -> dict[str, Any]:
    key_version = getattr(credential, "encryption_key_version", None)
    algorithm = getattr(credential, "encryption_algorithm", None)
    if key_version is None and algorithm is None:
        return LLMCredentialConfigService.load_legacy(credential)
    if not key_version or not algorithm:
        raise LLMCredentialConfigError("LLM credential encryption metadata is invalid.")
    return get_llm_credential_config_service().load(credential)


def materialize_llm_client_credentials(
    credential: Any,
    provider: Any,
) -> dict[str, str]:
    """Combine secret material with the current server-owned provider endpoint."""

    config = load_llm_credential_config(credential)
    api_key = config.get("apiKey")
    base_url = getattr(provider, "base_url", None)
    if (
        not isinstance(api_key, str)
        or not api_key
        or not isinstance(base_url, str)
        or not base_url
        or base_url != base_url.strip()
    ):
        raise LLMCredentialConfigError("LLM provider configuration is invalid.")
    return {"apiKey": api_key, "baseUrl": base_url}


def require_llm_credential_keyring_ready() -> None:
    """Validate the LLM keyring without decrypting a credential."""
    get_llm_credential_config_service()
