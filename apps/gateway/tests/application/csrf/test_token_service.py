from datetime import datetime, timedelta, timezone

import pytest

from apps.gateway.application.csrf.token import (
    CsrfBindingKind,
    CsrfTokenService,
    CsrfValidationReason,
)


@pytest.fixture
def service() -> CsrfTokenService:
    return CsrfTokenService.from_root_secret(
        "test-session-secret-with-sufficient-entropy",
        ttl_seconds=600,
        nonce_factory=lambda size: b"n" * size,
    )


def test_signed_token_contains_no_binding_or_organization_plaintext(
    service: CsrfTokenService,
):
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)

    issued = service.issue(
        binding_kind=CsrfBindingKind.AUTHENTICATED,
        binding_secret="raw-auth-cookie-sentinel",
        organization_scope="raw-organization-sentinel",
        now=now,
    )

    assert issued.token.count(".") == 3
    assert "raw-auth-cookie-sentinel" not in issued.token
    assert "raw-organization-sentinel" not in issued.token
    assert issued.expires_at == now + timedelta(seconds=600)


def test_valid_signed_double_submit_token_is_accepted(
    service: CsrfTokenService,
):
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    issued = service.issue(
        binding_kind=CsrfBindingKind.AUTHENTICATED,
        binding_secret="session-a",
        organization_scope="organization-a",
        now=now,
    )

    reason = service.validate(
        header_token=issued.token,
        cookie_token=issued.token,
        binding_kind=CsrfBindingKind.AUTHENTICATED,
        binding_secret="session-a",
        organization_scope="organization-a",
        now=now + timedelta(seconds=1),
    )

    assert reason is None


@pytest.mark.parametrize(
    ("header_token", "cookie_token", "expected"),
    [
        (None, "cookie-token", CsrfValidationReason.TOKEN_MISSING),
        ("header-token", None, CsrfValidationReason.TOKEN_MISSING),
        ("header-token", "cookie-token", CsrfValidationReason.TOKEN_MISMATCH),
        ("not-a-token", "not-a-token", CsrfValidationReason.TOKEN_INVALID),
    ],
)
def test_missing_mismatched_and_malformed_tokens_are_rejected(
    service: CsrfTokenService,
    header_token: str | None,
    cookie_token: str | None,
    expected: CsrfValidationReason,
):
    reason = service.validate(
        header_token=header_token,
        cookie_token=cookie_token,
        binding_kind=CsrfBindingKind.PRE_AUTH,
        binding_secret="anonymous-seed",
        organization_scope=None,
        now=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )

    assert reason is expected


@pytest.mark.parametrize(
    ("binding_kind", "binding_secret", "organization_scope"),
    [
        (CsrfBindingKind.AUTHENTICATED, "session-b", "organization-a"),
        (CsrfBindingKind.AUTHENTICATED, "session-a", "organization-b"),
        (CsrfBindingKind.PRE_AUTH, "session-a", "organization-a"),
    ],
)
def test_cross_session_scope_and_binding_kind_replay_are_rejected(
    service: CsrfTokenService,
    binding_kind: CsrfBindingKind,
    binding_secret: str,
    organization_scope: str | None,
):
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    issued = service.issue(
        binding_kind=CsrfBindingKind.AUTHENTICATED,
        binding_secret="session-a",
        organization_scope="organization-a",
        now=now,
    )

    reason = service.validate(
        header_token=issued.token,
        cookie_token=issued.token,
        binding_kind=binding_kind,
        binding_secret=binding_secret,
        organization_scope=organization_scope,
        now=now + timedelta(seconds=1),
    )

    assert reason is CsrfValidationReason.TOKEN_INVALID


def test_expired_and_implausibly_future_tokens_are_rejected(
    service: CsrfTokenService,
):
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    issued = service.issue(
        binding_kind=CsrfBindingKind.PRE_AUTH,
        binding_secret="anonymous-seed",
        organization_scope=None,
        now=now,
    )

    expired = service.validate(
        header_token=issued.token,
        cookie_token=issued.token,
        binding_kind=CsrfBindingKind.PRE_AUTH,
        binding_secret="anonymous-seed",
        organization_scope=None,
        now=now + timedelta(seconds=601),
    )
    future = service.validate(
        header_token=issued.token,
        cookie_token=issued.token,
        binding_kind=CsrfBindingKind.PRE_AUTH,
        binding_secret="anonymous-seed",
        organization_scope=None,
        now=now - timedelta(seconds=31),
    )

    assert expired is CsrfValidationReason.TOKEN_EXPIRED
    assert future is CsrfValidationReason.TOKEN_INVALID


def test_noncanonical_or_invalid_base64_token_segments_are_rejected(
    service: CsrfTokenService,
):
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    issued = service.issue(
        binding_kind=CsrfBindingKind.PRE_AUTH,
        binding_secret="anonymous-seed",
        organization_scope=None,
        now=now,
    )
    version, expiry, nonce, mac = issued.token.split(".")

    for malformed in (
        f"{version}.0{expiry}.{nonce}.{mac}",
        f"{version}.{expiry}.{nonce}=.{mac}",
        f"{version}.{expiry}.{nonce}.{mac}$",
    ):
        reason = service.validate(
            header_token=malformed,
            cookie_token=malformed,
            binding_kind=CsrfBindingKind.PRE_AUTH,
            binding_secret="anonymous-seed",
            organization_scope=None,
            now=now,
        )
        assert reason is CsrfValidationReason.TOKEN_INVALID
