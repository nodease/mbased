from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections import OrderedDict
from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken

from apps.memory.application.public_lifecycle import (
    IssuedSecret,
    SecretCiphertext,
)


_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_DEFAULT_VERIFIER_KEY_VERSION = "memory-public-hmac-v1"
_DEFAULT_REPLAY_KEY_VERSION = "memory-public-replay-v1"
_MAX_KEYRING_SIZE = 2


class HmacPublicSecretIssuer:
    """Issue opaque public capabilities and derive non-reversible verifiers."""

    def __init__(
        self,
        verifier_key: bytes | Mapping[str, bytes],
        *,
        key_version: str = _DEFAULT_VERIFIER_KEY_VERSION,
        primary_key_version: str | None = None,
    ) -> None:
        if isinstance(verifier_key, Mapping):
            primary = primary_key_version or key_version
            self._verifier_keys = _validated_hmac_keyring(verifier_key, primary)
        else:
            self._verifier_keys = _validated_hmac_keyring(
                {key_version: verifier_key},
                key_version,
            )
        self._key_version = next(iter(self._verifier_keys))

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str],
    ) -> "HmacPublicSecretIssuer":
        raw_keyring = environ.get("MEMORY_PUBLIC_CAPABILITY_HMAC_KEYS", "").strip()
        if raw_keyring:
            primary_version = environ.get(
                "MEMORY_PUBLIC_CAPABILITY_HMAC_PRIMARY_VERSION", ""
            ).strip()
            try:
                decoded = json.loads(raw_keyring)
                if not isinstance(decoded, dict):
                    raise ValueError
                keyring = {
                    version: value.encode("utf-8")
                    for version, value in decoded.items()
                    if isinstance(version, str) and isinstance(value, str)
                }
                if len(keyring) != len(decoded):
                    raise ValueError
                return cls(
                    keyring,
                    primary_key_version=primary_version,
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise RuntimeError("invalid public capability keyring") from exc

        raw_key = environ.get("MEMORY_PUBLIC_CAPABILITY_HMAC_KEY", "")
        if not raw_key:
            raise RuntimeError("MEMORY_PUBLIC_CAPABILITY_HMAC_KEY is required")
        key_version = environ.get(
            "MEMORY_PUBLIC_CAPABILITY_HMAC_KEY_VERSION",
            _DEFAULT_VERIFIER_KEY_VERSION,
        )
        try:
            return cls(raw_key.encode("utf-8"), key_version=key_version)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from None

    def issue_access_grant(self) -> IssuedSecret:
        return self._issue(prefix="cag", purpose="access_grant")

    def access_grant_verifier(self, raw_value: str) -> tuple[str, str] | None:
        candidates = self.access_grant_verifiers(raw_value)
        return candidates[0] if candidates else None

    def access_grant_verifiers(self, raw_value: str) -> tuple[tuple[str, str], ...]:
        return self._verifiers_for(raw_value, prefix="cag", purpose="access_grant")

    def issue_purge_receipt(self) -> IssuedSecret:
        return self._issue(prefix="cpr", purpose="purge_receipt")

    def purge_receipt_verifier(self, raw_value: str) -> tuple[str, str] | None:
        candidates = self.purge_receipt_verifiers(raw_value)
        return candidates[0] if candidates else None

    def purge_receipt_verifiers(self, raw_value: str) -> tuple[tuple[str, str], ...]:
        return self._verifiers_for(raw_value, prefix="cpr", purpose="purge_receipt")

    def _issue(self, *, prefix: str, purpose: str) -> IssuedSecret:
        raw_value = f"{prefix}_v1_{secrets.token_urlsafe(32)}"
        primary_key = self._verifier_keys[self._key_version]
        return IssuedSecret(
            raw_value=raw_value,
            verifier_hash=self._digest(primary_key, purpose, raw_value),
            verifier_key_version=self._key_version,
        )

    def _verifiers_for(
        self,
        raw_value: str,
        *,
        prefix: str,
        purpose: str,
    ) -> tuple[tuple[str, str], ...]:
        expected_prefix = f"{prefix}_v1_"
        if (
            not isinstance(raw_value, str)
            or not raw_value.startswith(expected_prefix)
            or not _TOKEN_PATTERN.fullmatch(raw_value[len(expected_prefix) :])
        ):
            return ()
        return tuple(
            (version, self._digest(key, purpose, raw_value))
            for version, key in self._verifier_keys.items()
        )

    @staticmethod
    def _digest(verifier_key: bytes, purpose: str, raw_value: str) -> str:
        message = f"memory-public-capability-v1:{purpose}:{raw_value}".encode("utf-8")
        return hmac.new(verifier_key, message, hashlib.sha256).hexdigest()

    def configuration_key_materials(self) -> tuple[bytes, ...]:
        return tuple(self._verifier_keys.values())


class FernetSecretReplayCipher:
    """Encrypt short-lived replay values and bind them to an idempotency result."""

    def __init__(
        self,
        fernet_key: bytes | Mapping[str, bytes],
        *,
        key_version: str = _DEFAULT_REPLAY_KEY_VERSION,
        primary_key_version: str | None = None,
    ) -> None:
        if isinstance(fernet_key, Mapping):
            primary = primary_key_version or key_version
            raw_keyring = _validated_fernet_keyring(fernet_key, primary)
        else:
            raw_keyring = _validated_fernet_keyring(
                {key_version: fernet_key},
                key_version,
            )
        self._key_materials = raw_keyring
        self._fernets = OrderedDict(
            (version, Fernet(value)) for version, value in raw_keyring.items()
        )
        self._key_version = next(iter(self._fernets))

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str],
    ) -> "FernetSecretReplayCipher":
        raw_keyring = environ.get("MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEYS", "").strip()
        if raw_keyring:
            primary_version = environ.get(
                "MEMORY_PUBLIC_REPLAY_ENCRYPTION_PRIMARY_VERSION", ""
            ).strip()
            try:
                decoded = json.loads(raw_keyring)
                if not isinstance(decoded, dict):
                    raise ValueError
                keyring = {
                    version: value.encode("ascii")
                    for version, value in decoded.items()
                    if isinstance(version, str) and isinstance(value, str)
                }
                if len(keyring) != len(decoded):
                    raise ValueError
                return cls(
                    keyring,
                    primary_key_version=primary_version,
                )
            except (
                json.JSONDecodeError,
                UnicodeEncodeError,
                TypeError,
                ValueError,
            ) as exc:
                raise RuntimeError("invalid public replay encryption keyring") from exc

        raw_key = environ.get("MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEY", "")
        if not raw_key:
            raise RuntimeError("MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEY is required")
        key_version = environ.get(
            "MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEY_VERSION",
            _DEFAULT_REPLAY_KEY_VERSION,
        )
        try:
            return cls(raw_key.encode("ascii"), key_version=key_version)
        except (UnicodeEncodeError, ValueError) as exc:
            raise RuntimeError("MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEY is invalid") from exc

    def encrypt(
        self,
        raw_value: str,
        *,
        associated_data_digest: str,
    ) -> SecretCiphertext:
        if not isinstance(raw_value, str) or len(raw_value) > 1024:
            raise ValueError("secret replay value is invalid")
        payload = json.dumps(
            {"aad": associated_data_digest, "value": raw_value},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        primary = self._fernets[self._key_version]
        return SecretCiphertext(
            ciphertext=primary.encrypt(payload),
            key_version=self._key_version,
        )

    def decrypt(
        self,
        ciphertext: bytes,
        *,
        key_version: str,
        associated_data_digest: str,
    ) -> str | None:
        fernet = self._fernets.get(key_version)
        if fernet is None:
            return None
        try:
            payload = json.loads(fernet.decrypt(ciphertext).decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        stored_digest = payload.get("aad")
        raw_value = payload.get("value")
        if (
            not isinstance(stored_digest, str)
            or not isinstance(raw_value, str)
            or not hmac.compare_digest(stored_digest, associated_data_digest)
        ):
            return None
        return raw_value

    def configuration_key_materials(self) -> tuple[bytes, ...]:
        return tuple(self._key_materials.values())


def _validated_hmac_keyring(
    keyring: Mapping[str, bytes],
    primary_version: str,
) -> OrderedDict[str, bytes]:
    if (
        not 1 <= len(keyring) <= _MAX_KEYRING_SIZE
        or not _VERSION_PATTERN.fullmatch(primary_version)
        or primary_version not in keyring
    ):
        raise ValueError("invalid public capability keyring")
    parsed: dict[str, bytes] = {}
    for version, value in keyring.items():
        if (
            not isinstance(version, str)
            or not _VERSION_PATTERN.fullmatch(version)
            or not isinstance(value, bytes)
            or len(value) < 32
        ):
            raise ValueError("invalid public capability keyring")
        parsed[version] = value
    _require_distinct_keyring_materials(parsed.values(), "public capability")
    return _primary_first(parsed, primary_version)


def _validated_fernet_keyring(
    keyring: Mapping[str, bytes],
    primary_version: str,
) -> OrderedDict[str, bytes]:
    if (
        not 1 <= len(keyring) <= _MAX_KEYRING_SIZE
        or not _VERSION_PATTERN.fullmatch(primary_version)
        or primary_version not in keyring
    ):
        raise ValueError("invalid public replay encryption keyring")
    parsed: dict[str, bytes] = {}
    for version, value in keyring.items():
        if (
            not isinstance(version, str)
            or not _VERSION_PATTERN.fullmatch(version)
            or not isinstance(value, bytes)
        ):
            raise ValueError("invalid public replay encryption keyring")
        try:
            Fernet(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid public replay encryption keyring") from exc
        parsed[version] = value
    _require_distinct_keyring_materials(parsed.values(), "public replay encryption")
    return _primary_first(parsed, primary_version)


def _primary_first(
    keyring: Mapping[str, bytes],
    primary_version: str,
) -> OrderedDict[str, bytes]:
    ordered = OrderedDict(((primary_version, keyring[primary_version]),))
    ordered.update(
        (version, value)
        for version, value in keyring.items()
        if version != primary_version
    )
    return ordered


def _require_distinct_keyring_materials(
    values,
    label: str,
) -> None:
    materials = tuple(values)
    if any(
        hmac.compare_digest(left, right)
        for index, left in enumerate(materials)
        for right in materials[index + 1 :]
    ):
        raise ValueError(f"{label} keyring values must be distinct")


__all__ = ["FernetSecretReplayCipher", "HmacPublicSecretIssuer"]
