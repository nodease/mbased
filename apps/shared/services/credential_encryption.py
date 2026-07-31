from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping

from cryptography.fernet import Fernet, InvalidToken

FERNET_ENCRYPTION_ALGORITHM = "fernet-v1"
MAIL_CREDENTIAL_ALGORITHM = FERNET_ENCRYPTION_ALGORITHM
DEFAULT_KEY_VERSION = "v1"


class CredentialEncryptionError(ValueError):
    """Safe credential encryption failure without secret-bearing details."""


@dataclass(frozen=True)
class EncryptedSecretEnvelope:
    ciphertext: str
    key_version: str
    algorithm: str = MAIL_CREDENTIAL_ALGORITHM


class CredentialEncryptionService:
    """Versioned Fernet keyring for application-managed credential secrets."""

    def __init__(
        self,
        keys: Mapping[str, str],
        active_key_version: str,
        *,
        subject_label: str = "Mail credential",
        algorithm: str = FERNET_ENCRYPTION_ALGORITHM,
    ):
        self._subject_label = subject_label
        self._algorithm = algorithm
        normalized = {str(version): str(key) for version, key in keys.items() if key}
        if not normalized or active_key_version not in normalized:
            raise CredentialEncryptionError(
                f"{self._subject_label} encryption key is unavailable."
            )
        try:
            self._ciphers = {
                version: Fernet(key.encode("utf-8"))
                for version, key in normalized.items()
            }
        except (TypeError, ValueError) as exc:
            raise CredentialEncryptionError(
                f"{self._subject_label} encryption key is invalid."
            ) from exc
        self._active_key_version = active_key_version

    @classmethod
    def from_environment(cls) -> "CredentialEncryptionService":
        return cls.from_environment_variables(
            keyring_environment_variable="MAIL_CREDENTIAL_ENCRYPTION_KEYS",
            active_version_environment_variable="MAIL_CREDENTIAL_ACTIVE_KEY_VERSION",
            subject_label="Mail credential",
        )

    @classmethod
    def from_environment_variables(
        cls,
        *,
        keyring_environment_variable: str,
        active_version_environment_variable: str,
        subject_label: str,
        fallback_key_environment_variable: str = "ENCRYPTION_KEY",
        algorithm: str = FERNET_ENCRYPTION_ALGORITHM,
    ) -> "CredentialEncryptionService":
        raw_keyring = os.getenv(keyring_environment_variable)
        active_version = os.getenv(
            active_version_environment_variable, DEFAULT_KEY_VERSION
        )
        if raw_keyring:
            try:
                parsed = json.loads(raw_keyring)
            except json.JSONDecodeError as exc:
                raise CredentialEncryptionError(
                    f"{subject_label} encryption keyring is invalid."
                ) from exc
            if not isinstance(parsed, dict):
                raise CredentialEncryptionError(
                    f"{subject_label} encryption keyring is invalid."
                )
            return cls(
                parsed,
                active_version,
                subject_label=subject_label,
                algorithm=algorithm,
            )

        legacy_key = os.getenv(fallback_key_environment_variable)
        if legacy_key:
            return cls(
                {DEFAULT_KEY_VERSION: legacy_key},
                DEFAULT_KEY_VERSION,
                subject_label=subject_label,
                algorithm=algorithm,
            )
        raise CredentialEncryptionError(
            f"{subject_label} encryption key is unavailable."
        )

    @property
    def active_key_version(self) -> str:
        return self._active_key_version

    @property
    def algorithm(self) -> str:
        return self._algorithm

    def encrypt(self, secret: str) -> EncryptedSecretEnvelope:
        if not secret:
            raise CredentialEncryptionError(
                f"{self._subject_label} secret is required."
            )
        cipher = self._ciphers[self._active_key_version]
        return EncryptedSecretEnvelope(
            ciphertext=cipher.encrypt(secret.encode("utf-8")).decode("utf-8"),
            key_version=self._active_key_version,
            algorithm=self._algorithm,
        )

    def decrypt(self, envelope: EncryptedSecretEnvelope) -> str:
        if envelope.algorithm != self._algorithm:
            raise CredentialEncryptionError(
                f"{self._subject_label} encryption algorithm is unsupported."
            )
        cipher = self._ciphers.get(envelope.key_version)
        if cipher is None:
            raise CredentialEncryptionError(
                f"{self._subject_label} encryption key version is unavailable."
            )
        try:
            return cipher.decrypt(envelope.ciphertext.encode("utf-8")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
            raise CredentialEncryptionError(
                f"{self._subject_label} secret could not be decrypted."
            ) from exc


def get_credential_encryption_service() -> CredentialEncryptionService:
    return CredentialEncryptionService.from_environment()


def require_mail_credential_keyring_ready() -> None:
    """Validate Mail credential keyring configuration without handling a secret."""
    CredentialEncryptionService.from_environment()
