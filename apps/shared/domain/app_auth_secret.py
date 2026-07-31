from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

APP_AUTH_SECRET_VERIFIER_VERSION = 1
APP_AUTH_SECRET_MIN_BYTES = 1
APP_AUTH_SECRET_MAX_BYTES = 512
APP_AUTH_SECRET_TOKEN_BYTES = 32
APP_AUTH_SECRET_PREFIX = "nodease_app_"

_VERIFIER_DOMAIN = b"nodease.app-auth-secret.v1\x00"


class AppAuthSecretCandidateInvalid(ValueError):
    pass


def generate_app_auth_secret() -> str:
    """Generate a redaction-recognizable bearer token with 256 bits of entropy."""

    return APP_AUTH_SECRET_PREFIX + secrets.token_urlsafe(APP_AUTH_SECRET_TOKEN_BYTES)


def app_auth_secret_verifier(
    candidate: str | bytes,
    *,
    verifier_version: int = APP_AUTH_SECRET_VERIFIER_VERSION,
) -> str:
    raw_candidate = _candidate_bytes(candidate)
    if verifier_version != APP_AUTH_SECRET_VERIFIER_VERSION:
        raise AppAuthSecretCandidateInvalid("app.auth_secret_state_invalid")
    return hashlib.sha256(_VERIFIER_DOMAIN + raw_candidate).hexdigest()


def verify_app_auth_secret(
    candidate: str | bytes,
    *,
    current_verifier: str | None,
    current_verifier_version: int | None,
    previous_verifier: str | None = None,
    previous_verifier_version: int | None = None,
    previous_valid_until: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """Verify a candidate without exposing which stored verifier matched."""

    if not app_auth_secret_verifier_state_is_valid(
        current_verifier,
        current_verifier_version,
    ):
        return False

    try:
        candidate_verifier = app_auth_secret_verifier(
            candidate,
            verifier_version=_validated_verifier_version(current_verifier_version),
        )
    except AppAuthSecretCandidateInvalid:
        return False

    if secrets.compare_digest(
        candidate_verifier,
        current_verifier,
    ):
        return True

    if not _previous_state_is_valid(
        previous_verifier=previous_verifier,
        previous_verifier_version=previous_verifier_version,
        previous_valid_until=previous_valid_until,
        now=now,
    ):
        return False

    try:
        previous_candidate_verifier = app_auth_secret_verifier(
            candidate,
            verifier_version=_validated_verifier_version(previous_verifier_version),
        )
    except AppAuthSecretCandidateInvalid:
        return False
    return secrets.compare_digest(previous_candidate_verifier, previous_verifier)


def verify_legacy_app_auth_secret(
    candidate: str | bytes,
    legacy_secret: object,
) -> bool:
    """Verify a pre-cutover raw secret without making it current authority."""

    if not isinstance(legacy_secret, str):
        return False
    try:
        candidate_verifier = app_auth_secret_verifier(candidate)
        legacy_verifier = app_auth_secret_verifier(legacy_secret)
    except AppAuthSecretCandidateInvalid:
        return False
    return secrets.compare_digest(candidate_verifier, legacy_verifier)


def app_auth_secret_verifier_state_is_valid(
    verifier: str | None,
    verifier_version: int | None,
) -> bool:
    return verifier_version == APP_AUTH_SECRET_VERIFIER_VERSION and _valid_verifier(
        verifier
    )


def app_auth_secret_previous_is_active(
    *,
    previous_verifier: str | None,
    previous_verifier_version: int | None,
    previous_valid_until: datetime | None,
    now: datetime | None = None,
) -> bool:
    return _previous_state_is_valid(
        previous_verifier=previous_verifier,
        previous_verifier_version=previous_verifier_version,
        previous_valid_until=previous_valid_until,
        now=now,
    )


def _candidate_bytes(candidate: str | bytes) -> bytes:
    if isinstance(candidate, str):
        try:
            raw_candidate = candidate.encode("ascii", errors="strict")
        except UnicodeEncodeError as exc:
            raise AppAuthSecretCandidateInvalid(
                "app.auth_secret_candidate_invalid"
            ) from exc
    elif isinstance(candidate, bytes):
        raw_candidate = candidate
        try:
            raw_candidate.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise AppAuthSecretCandidateInvalid(
                "app.auth_secret_candidate_invalid"
            ) from exc
    else:
        raise AppAuthSecretCandidateInvalid("app.auth_secret_candidate_invalid")

    if not APP_AUTH_SECRET_MIN_BYTES <= len(raw_candidate) <= APP_AUTH_SECRET_MAX_BYTES:
        raise AppAuthSecretCandidateInvalid("app.auth_secret_candidate_invalid")
    return raw_candidate


def _validated_verifier_version(value: int | None) -> int:
    if value != APP_AUTH_SECRET_VERIFIER_VERSION:
        raise AppAuthSecretCandidateInvalid("app.auth_secret_state_invalid")
    return value


def _valid_verifier(value: str | None) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _previous_state_is_valid(
    *,
    previous_verifier: str | None,
    previous_verifier_version: int | None,
    previous_valid_until: datetime | None,
    now: datetime | None,
) -> bool:
    if not _valid_verifier(previous_verifier):
        return False
    if previous_verifier_version != APP_AUTH_SECRET_VERIFIER_VERSION:
        return False
    if previous_valid_until is None or previous_valid_until.tzinfo is None:
        return False
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        return False
    return current_time.astimezone(timezone.utc) < previous_valid_until.astimezone(
        timezone.utc
    )


__all__ = [
    "APP_AUTH_SECRET_MAX_BYTES",
    "APP_AUTH_SECRET_MIN_BYTES",
    "APP_AUTH_SECRET_PREFIX",
    "APP_AUTH_SECRET_VERIFIER_VERSION",
    "AppAuthSecretCandidateInvalid",
    "app_auth_secret_previous_is_active",
    "app_auth_secret_verifier",
    "app_auth_secret_verifier_state_is_valid",
    "generate_app_auth_secret",
    "verify_legacy_app_auth_secret",
    "verify_app_auth_secret",
]
