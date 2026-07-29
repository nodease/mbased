from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


CSRF_COOKIE_NAME = "csrf_token"
CSRF_ANON_COOKIE_NAME = "csrf_anon_seed"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_ORGANIZATION_HEADER_NAME = "X-Organization-Id"
CSRF_TOKEN_VERSION = "v1"
CSRF_TOKEN_TTL_SECONDS = 600
_CLOCK_SKEW_SECONDS = 30
_NONCE_BYTES = 32
_MAX_TOKEN_LENGTH = 256
_ACCOUNT_SCOPE = b"account"


class CsrfBindingKind(str, Enum):
    AUTHENTICATED = "authenticated"
    PRE_AUTH = "pre_auth"


class CsrfValidationReason(str, Enum):
    TOKEN_MISSING = "token_missing"
    TOKEN_MISMATCH = "token_mismatch"
    TOKEN_INVALID = "token_invalid"
    TOKEN_EXPIRED = "token_expired"
    ORGANIZATION_SCOPE_INVALID = "organization_scope_invalid"
    ORIGIN_INVALID = "origin_invalid"
    FETCH_METADATA_INVALID = "fetch_metadata_invalid"
    CONTENT_TYPE_INVALID = "content_type_invalid"


@dataclass(frozen=True)
class IssuedCsrfToken:
    token: str
    expires_at: datetime


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        f"{value}{padding}".encode("ascii"),
        altchars=b"-_",
        validate=True,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CsrfTokenService:
    def __init__(
        self,
        *,
        signing_key: bytes,
        binding_key: bytes,
        scope_key: bytes,
        ttl_seconds: int = CSRF_TOKEN_TTL_SECONDS,
        nonce_factory: Callable[[int], bytes] = secrets.token_bytes,
    ):
        if len(signing_key) < 32 or len(binding_key) < 32 or len(scope_key) < 32:
            raise ValueError("CSRF derived keys must be at least 32 bytes")
        if not 60 <= ttl_seconds <= 3600:
            raise ValueError("CSRF token TTL must be between 60 and 3600 seconds")
        self._signing_key = signing_key
        self._binding_key = binding_key
        self._scope_key = scope_key
        self._ttl_seconds = ttl_seconds
        self._nonce_factory = nonce_factory

    @classmethod
    def from_root_secret(
        cls,
        root_secret: str,
        *,
        ttl_seconds: int = CSRF_TOKEN_TTL_SECONDS,
        nonce_factory: Callable[[int], bytes] = secrets.token_bytes,
    ) -> "CsrfTokenService":
        if not root_secret or not root_secret.strip():
            raise ValueError("CSRF root secret is not configured")
        root_key = root_secret.encode("utf-8")

        def derive(context: bytes) -> bytes:
            return hmac.new(root_key, context, hashlib.sha256).digest()

        return cls(
            signing_key=derive(b"nodease.csrf.signing.v1"),
            binding_key=derive(b"nodease.csrf.binding.v1"),
            scope_key=derive(b"nodease.csrf.scope.v1"),
            ttl_seconds=ttl_seconds,
            nonce_factory=nonce_factory,
        )

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def issue(
        self,
        *,
        binding_kind: CsrfBindingKind,
        binding_secret: str,
        organization_scope: str | None,
        now: datetime | None = None,
    ) -> IssuedCsrfToken:
        issued_at = self._normalize_now(now)
        expiry = int(
            (issued_at + timedelta(seconds=self._ttl_seconds)).timestamp()
        )
        expires_at = datetime.fromtimestamp(expiry, tz=timezone.utc)
        nonce = self._nonce_factory(_NONCE_BYTES)
        if len(nonce) != _NONCE_BYTES:
            raise ValueError("CSRF nonce factory returned an invalid length")
        mac = self._mac(
            expiry=expiry,
            nonce=nonce,
            binding_kind=binding_kind,
            binding_secret=binding_secret,
            organization_scope=organization_scope,
        )
        token = ".".join(
            (
                CSRF_TOKEN_VERSION,
                str(expiry),
                _encode(nonce),
                _encode(mac),
            )
        )
        return IssuedCsrfToken(token=token, expires_at=expires_at)

    def reuse_if_valid(
        self,
        *,
        token: str | None,
        binding_kind: CsrfBindingKind,
        binding_secret: str,
        organization_scope: str | None,
        now: datetime | None = None,
    ) -> IssuedCsrfToken | None:
        if (
            self.validate(
                header_token=token,
                cookie_token=token,
                binding_kind=binding_kind,
                binding_secret=binding_secret,
                organization_scope=organization_scope,
                now=now,
            )
            is not None
        ):
            return None

        # validate() already proved the canonical token shape and expiry.
        assert token is not None
        _, raw_expiry, _, _ = token.split(".")
        return IssuedCsrfToken(
            token=token,
            expires_at=datetime.fromtimestamp(int(raw_expiry), tz=timezone.utc),
        )

    def validate(
        self,
        *,
        header_token: str | None,
        cookie_token: str | None,
        binding_kind: CsrfBindingKind,
        binding_secret: str | None,
        organization_scope: str | None,
        now: datetime | None = None,
    ) -> CsrfValidationReason | None:
        if not header_token or not cookie_token:
            return CsrfValidationReason.TOKEN_MISSING
        if (
            len(header_token) > _MAX_TOKEN_LENGTH
            or len(cookie_token) > _MAX_TOKEN_LENGTH
            or not header_token.isascii()
            or not cookie_token.isascii()
        ):
            return CsrfValidationReason.TOKEN_INVALID
        if not hmac.compare_digest(header_token, cookie_token):
            return CsrfValidationReason.TOKEN_MISMATCH
        if not binding_secret:
            return CsrfValidationReason.TOKEN_INVALID
        scope_reason = self.validate_organization_scope(organization_scope)
        if scope_reason is not None:
            return scope_reason

        try:
            version, raw_expiry, raw_nonce, raw_mac = header_token.split(".")
            if version != CSRF_TOKEN_VERSION:
                return CsrfValidationReason.TOKEN_INVALID
            expiry = int(raw_expiry)
            nonce = _decode(raw_nonce)
            supplied_mac = _decode(raw_mac)
            if (
                raw_expiry != str(expiry)
                or _encode(nonce) != raw_nonce
                or _encode(supplied_mac) != raw_mac
                or len(nonce) != _NONCE_BYTES
                or len(supplied_mac) != hashlib.sha256().digest_size
            ):
                return CsrfValidationReason.TOKEN_INVALID
        except (binascii.Error, TypeError, ValueError, UnicodeError):
            return CsrfValidationReason.TOKEN_INVALID

        current_timestamp = int(self._normalize_now(now).timestamp())
        if expiry <= current_timestamp:
            return CsrfValidationReason.TOKEN_EXPIRED
        if expiry > current_timestamp + self._ttl_seconds + _CLOCK_SKEW_SECONDS:
            return CsrfValidationReason.TOKEN_INVALID

        try:
            expected_mac = self._mac(
                expiry=expiry,
                nonce=nonce,
                binding_kind=binding_kind,
                binding_secret=binding_secret,
                organization_scope=organization_scope,
            )
        except ValueError:
            return CsrfValidationReason.TOKEN_INVALID
        if not hmac.compare_digest(supplied_mac, expected_mac):
            return CsrfValidationReason.TOKEN_INVALID
        return None

    def _mac(
        self,
        *,
        expiry: int,
        nonce: bytes,
        binding_kind: CsrfBindingKind,
        binding_secret: str,
        organization_scope: str | None,
    ) -> bytes:
        if not binding_secret or len(binding_secret) > 8192:
            raise ValueError("Invalid CSRF binding")
        binding_digest = hmac.new(
            self._binding_key,
            binding_secret.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        scope = self._normalize_scope(organization_scope)
        scope_digest = hmac.new(self._scope_key, scope, hashlib.sha256).digest()
        message = b"\x00".join(
            (
                CSRF_TOKEN_VERSION.encode("ascii"),
                str(expiry).encode("ascii"),
                nonce,
                binding_kind.value.encode("ascii"),
                binding_digest,
                scope_digest,
            )
        )
        return hmac.new(self._signing_key, message, hashlib.sha256).digest()

    @staticmethod
    def validate_organization_scope(
        organization_scope: str | None,
    ) -> CsrfValidationReason | None:
        try:
            CsrfTokenService._normalize_scope(organization_scope)
        except ValueError:
            return CsrfValidationReason.ORGANIZATION_SCOPE_INVALID
        return None

    @staticmethod
    def _normalize_scope(organization_scope: str | None) -> bytes:
        if organization_scope is None:
            return _ACCOUNT_SCOPE
        if len(organization_scope) > 128 or any(
            ord(character) < 32 or ord(character) == 127
            for character in organization_scope
        ):
            raise ValueError("Invalid CSRF organization scope")
        normalized = organization_scope.strip()
        if not normalized:
            return _ACCOUNT_SCOPE
        return normalized.encode("utf-8")

    @staticmethod
    def _normalize_now(now: datetime | None) -> datetime:
        value = now or _utc_now()
        if value.tzinfo is None:
            raise ValueError("CSRF time must be timezone-aware")
        return value.astimezone(timezone.utc)
