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
from apps.memory.domain.conversation import (
    MAX_MEMORY_CONTENT_BYTES,
    ProtectedContent,
)


_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_DEFAULT_VERIFIER_KEY_VERSION = "memory-public-hmac-v1"
_DEFAULT_REPLAY_KEY_VERSION = "memory-public-replay-v1"
_DEFAULT_CONTENT_KEY_VERSION = "memory-content-v1"
_CONTENT_FORMAT_VERSION = "memory-content-fernet-v1"
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
            raise RuntimeError(
                "MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEY is invalid"
            ) from exc

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


class FernetMemoryContentCipher:
    """Encrypt bounded Memory projections with versioned keys and keyed digests."""

    def __init__(
        self,
        fernet_key: bytes | Mapping[str, bytes],
        *,
        digest_hmac_key: bytes,
        key_version: str = _DEFAULT_CONTENT_KEY_VERSION,
        primary_key_version: str | None = None,
    ) -> None:
        primary = primary_key_version or key_version
        if isinstance(fernet_key, Mapping):
            raw_keyring = _validated_fernet_keyring(fernet_key, primary)
        else:
            raw_keyring = _validated_fernet_keyring(
                {key_version: fernet_key},
                key_version,
            )
        if not isinstance(digest_hmac_key, bytes) or len(digest_hmac_key) < 32:
            raise ValueError("memory content digest HMAC key is invalid")
        _require_distinct_keyring_materials(
            (*raw_keyring.values(), digest_hmac_key),
            "memory content",
        )
        self._key_materials = raw_keyring
        self._fernets = OrderedDict(
            (version, Fernet(value)) for version, value in raw_keyring.items()
        )
        self._key_version = next(iter(self._fernets))
        self._digest_hmac_key = digest_hmac_key

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str],
    ) -> "FernetMemoryContentCipher":
        raw_keyring = environ.get("MEMORY_CONTENT_ENCRYPTION_KEYS", "").strip()
        primary = environ.get(
            "MEMORY_CONTENT_ENCRYPTION_PRIMARY_VERSION",
            "",
        ).strip()
        raw_digest_key = environ.get("MEMORY_CONTENT_DIGEST_HMAC_KEY", "")
        if not raw_keyring or not primary or not raw_digest_key:
            raise RuntimeError("memory content protection keys are required")
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
                primary_key_version=primary,
                digest_hmac_key=raw_digest_key.encode("utf-8"),
            )
        except (
            json.JSONDecodeError,
            UnicodeEncodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise RuntimeError("invalid memory content protection keyring") from exc

    def protect(self, value: str, *, associated_data: str) -> ProtectedContent:
        if not isinstance(value, str) or not isinstance(associated_data, str):
            raise ValueError("memory content is invalid")
        raw = value.encode("utf-8")
        if (
            not 1 <= len(raw) <= MAX_MEMORY_CONTENT_BYTES
            or not 1 <= len(associated_data.encode("utf-8")) <= 512
        ):
            raise ValueError("memory content is invalid")
        aad_digest = hashlib.sha256(associated_data.encode("utf-8")).hexdigest()
        content_digest = self._digest(associated_data=associated_data, raw=raw)
        payload = json.dumps(
            {
                "aad": aad_digest,
                "digest": content_digest,
                "value": value,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return ProtectedContent(
            ciphertext=self._fernets[self._key_version].encrypt(payload),
            key_version=self._key_version,
            format_version=_CONTENT_FORMAT_VERSION,
            content_digest=content_digest,
            plaintext_byte_length=len(raw),
        )

    def reveal(
        self,
        protected: ProtectedContent,
        *,
        associated_data: str,
    ) -> str | None:
        if (
            protected.format_version != _CONTENT_FORMAT_VERSION
            or not isinstance(associated_data, str)
            or len(associated_data.encode("utf-8")) > 512
        ):
            return None
        fernet = self._fernets.get(protected.key_version)
        if fernet is None:
            return None
        try:
            payload = json.loads(fernet.decrypt(protected.ciphertext).decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        value = payload.get("value")
        if not isinstance(value, str):
            return None
        raw = value.encode("utf-8")
        expected_aad = hashlib.sha256(associated_data.encode("utf-8")).hexdigest()
        expected_digest = self._digest(associated_data=associated_data, raw=raw)
        stored_aad = payload.get("aad")
        stored_digest = payload.get("digest")
        if (
            not isinstance(stored_aad, str)
            or not isinstance(stored_digest, str)
            or not hmac.compare_digest(stored_aad, expected_aad)
            or not hmac.compare_digest(stored_digest, expected_digest)
            or not hmac.compare_digest(protected.content_digest, expected_digest)
            or protected.plaintext_byte_length != len(raw)
            or not 1 <= len(raw) <= MAX_MEMORY_CONTENT_BYTES
        ):
            return None
        return value

    def configuration_key_materials(self) -> tuple[bytes, ...]:
        return (*self._key_materials.values(), self._digest_hmac_key)

    def _digest(self, *, associated_data: str, raw: bytes) -> str:
        payload = (
            b"memory-content-digest-v1\x00"
            + associated_data.encode("utf-8")
            + b"\x00"
            + raw
        )
        return hmac.new(self._digest_hmac_key, payload, hashlib.sha256).hexdigest()


class HmacMemoryRuntimeFingerprinter:
    """Domain-separated request identity; raw mapped content is never stored."""

    def __init__(
        self,
        keyring: Mapping[str, bytes],
        *,
        primary_key_version: str,
    ) -> None:
        self._keys = _validated_hmac_keyring(keyring, primary_key_version)
        self._primary = next(iter(self._keys))

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str],
    ) -> "HmacMemoryRuntimeFingerprinter":
        raw_keyring = environ.get("MEMORY_RUNTIME_ADMISSION_HMAC_KEYS", "").strip()
        primary = environ.get(
            "MEMORY_RUNTIME_ADMISSION_HMAC_PRIMARY_VERSION",
            "",
        ).strip()
        if not raw_keyring or not primary:
            raise RuntimeError("memory runtime admission keyring is required")
        try:
            decoded = json.loads(raw_keyring)
            if not isinstance(decoded, dict):
                raise ValueError
            keys = {
                version: value.encode("utf-8")
                for version, value in decoded.items()
                if isinstance(version, str) and isinstance(value, str)
            }
            if len(keys) != len(decoded):
                raise ValueError
            return cls(keys, primary_key_version=primary)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError("invalid memory runtime admission keyring") from exc

    def fingerprint(
        self,
        *,
        key_version: str | None = None,
        **values,
    ) -> tuple[str, str]:
        selected_version = key_version or self._primary
        selected_key = self._keys.get(selected_version)
        if selected_key is None:
            raise ValueError("memory runtime fingerprint key is unavailable")
        input_text = values.get("input_text")
        if (
            not isinstance(input_text, str)
            or not 1 <= len(input_text.encode("utf-8")) <= MAX_MEMORY_CONTENT_BYTES
        ):
            raise ValueError("memory runtime request is invalid")
        canonical = {
            "organization_id": str(values.get("organization_id")),
            "deployment_id": str(values.get("deployment_id")),
            "deployment_version": values.get("deployment_version"),
            "grant_id": str(values.get("grant_id")),
            "session_id": str(values.get("session_id")),
            "expected_lifecycle_revision": values.get("expected_lifecycle_revision"),
            "mapping_version": values.get("mapping_version"),
            "memory_policy_version": values.get("memory_policy_version"),
            "memory_contract_version": values.get("memory_contract_version"),
            "storage_generation": values.get("storage_generation"),
            "input_variable": values.get("input_variable"),
            "input_text": input_text,
        }
        if (
            type(canonical["deployment_version"]) is not int
            or canonical["deployment_version"] < 1
            or type(canonical["expected_lifecycle_revision"]) is not int
            or canonical["expected_lifecycle_revision"] < 1
            or type(canonical["storage_generation"]) is not int
            or canonical["storage_generation"] < 1
        ):
            raise ValueError("memory runtime request is invalid")
        payload = b"memory-runtime-admission-v1\x00" + json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        digest = hmac.new(
            selected_key,
            payload,
            hashlib.sha256,
        ).hexdigest()
        return selected_version, digest

    def configuration_key_materials(self) -> tuple[bytes, ...]:
        return tuple(self._keys.values())


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


__all__ = [
    "FernetMemoryContentCipher",
    "FernetSecretReplayCipher",
    "HmacMemoryRuntimeFingerprinter",
    "HmacPublicSecretIssuer",
]
