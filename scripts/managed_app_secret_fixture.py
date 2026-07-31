from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    app_auth_secret_verifier,
    app_auth_secret_verifier_state_is_valid,
    generate_app_auth_secret,
)


class _AppSecretState(Protocol):
    auth_secret: str | None
    auth_secret_verifier: str | None
    auth_secret_verifier_version: int | None
    auth_secret_generation: int
    auth_secret_previous_verifier: str | None
    auth_secret_previous_verifier_version: int | None
    auth_secret_previous_valid_until: datetime | None
    auth_secret_rotated_at: datetime | None


def configure_managed_app_secret_fixture(app: _AppSecretState) -> None:
    """Configure verifier-only state for direct-runtime verification fixtures."""

    generation = max(int(app.auth_secret_generation or 0), 0)
    managed_state_valid = (
        generation > 0
        and app_auth_secret_verifier_state_is_valid(
            app.auth_secret_verifier,
            app.auth_secret_verifier_version,
        )
        and isinstance(app.auth_secret_rotated_at, datetime)
        and app.auth_secret_rotated_at.tzinfo is not None
    )
    if managed_state_valid:
        app.auth_secret = None
        return

    candidate = (
        app.auth_secret
        if isinstance(app.auth_secret, str) and app.auth_secret
        else generate_app_auth_secret()
    )
    app.auth_secret = None
    app.auth_secret_verifier = app_auth_secret_verifier(candidate)
    app.auth_secret_verifier_version = APP_AUTH_SECRET_VERIFIER_VERSION
    app.auth_secret_generation = generation + 1
    app.auth_secret_previous_verifier = None
    app.auth_secret_previous_verifier_version = None
    app.auth_secret_previous_valid_until = None
    app.auth_secret_rotated_at = datetime.now(timezone.utc)


__all__ = ["configure_managed_app_secret_fixture"]
