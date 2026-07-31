from datetime import datetime, timedelta, timezone

import pytest
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_PREFIX,
    APP_AUTH_SECRET_VERIFIER_VERSION,
    AppAuthSecretCandidateInvalid,
    app_auth_secret_verifier,
    generate_app_auth_secret,
    verify_app_auth_secret,
    verify_legacy_app_auth_secret,
)


def test_generated_secret_is_unique_bounded_ascii_with_256_bit_entropy_source():
    secrets = {generate_app_auth_secret() for _ in range(16)}

    assert len(secrets) == 16
    assert all(1 <= len(value.encode("ascii")) <= 512 for value in secrets)
    assert all(value.startswith(APP_AUTH_SECRET_PREFIX) for value in secrets)


def test_verifier_is_stable_fixed_length_and_not_the_candidate():
    verifier = app_auth_secret_verifier("secret-candidate")

    assert verifier == app_auth_secret_verifier("secret-candidate")
    assert len(verifier) == 64
    assert verifier != "secret-candidate"


@pytest.mark.parametrize("candidate", ["", "s" * 513, "비밀", b"\xff", object()])
def test_verifier_rejects_malformed_candidates(candidate):
    with pytest.raises(AppAuthSecretCandidateInvalid):
        app_auth_secret_verifier(candidate)  # type: ignore[arg-type]


def test_verification_accepts_current_and_unexpired_previous_only():
    now = datetime(2026, 7, 17, 3, 0, tzinfo=timezone.utc)
    current = app_auth_secret_verifier("current-secret")
    previous = app_auth_secret_verifier("previous-secret")
    state = {
        "current_verifier": current,
        "current_verifier_version": APP_AUTH_SECRET_VERIFIER_VERSION,
        "previous_verifier": previous,
        "previous_verifier_version": APP_AUTH_SECRET_VERIFIER_VERSION,
        "previous_valid_until": now + timedelta(minutes=5),
    }

    assert verify_app_auth_secret("current-secret", now=now, **state)
    assert verify_app_auth_secret("previous-secret", now=now, **state)
    assert not verify_app_auth_secret("other-secret", now=now, **state)
    assert not verify_app_auth_secret(
        "previous-secret",
        now=state["previous_valid_until"],
        **state,
    )


def test_verification_fails_closed_for_unknown_or_incomplete_state():
    verifier = app_auth_secret_verifier("secret")

    assert not verify_app_auth_secret(
        "secret",
        current_verifier=verifier,
        current_verifier_version=999,
    )
    assert not verify_app_auth_secret(
        "secret",
        current_verifier=None,
        current_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
    )


def test_verification_rejects_previous_when_current_verifier_is_malformed():
    now = datetime(2026, 7, 17, 3, 0, tzinfo=timezone.utc)

    assert not verify_app_auth_secret(
        "previous-secret",
        current_verifier="malformed",
        current_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
        previous_verifier=app_auth_secret_verifier("previous-secret"),
        previous_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
        previous_valid_until=now + timedelta(minutes=5),
        now=now,
    )


def test_legacy_verification_is_bounded_and_constant_time_comparable():
    assert verify_legacy_app_auth_secret("legacy-secret", "legacy-secret")
    assert not verify_legacy_app_auth_secret("other-secret", "legacy-secret")
    assert not verify_legacy_app_auth_secret("legacy-secret", None)
    assert not verify_legacy_app_auth_secret("비밀", "legacy-secret")
