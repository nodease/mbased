from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from apps.gateway.application.authentication.errors import (
    InactiveAccount,
    InvalidCredentials,
    LoginRateLimited,
    LoginTemporarilyUnavailable,
    LimiterBackendError,
    PasswordLoginInternalError,
)
from apps.gateway.application.authentication.models import (
    LoginAdmission,
    LoginLimitDimension,
    PasswordLoginCommand,
    PasswordLoginResult,
    PasswordLoginSession,
    PasswordLoginUser,
)
from apps.gateway.application.authentication.password_login import PasswordLogin


@dataclass
class _Limiter:
    admission: LoginAdmission = LoginAdmission(allowed=True)
    admission_error: Exception | None = None
    reset_error: Exception | None = None

    def __post_init__(self):
        self.calls: list[tuple] = []

    def admit(self, *, account_identity: str, network_identity: str):
        self.calls.append(("admit", account_identity, network_identity))
        if self.admission_error:
            raise self.admission_error
        return self.admission

    def reset_after_success(self, *, account_identity: str, network_identity: str):
        self.calls.append(("reset", account_identity, network_identity))
        if self.reset_error:
            raise self.reset_error


class _Authenticator:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result or _result()
        self.error = error
        self.calls: list[tuple] = []

    def authenticate(self, *, account_identity: str, password: str):
        self.calls.append((account_identity, password))
        if self.error:
            raise self.error
        return self.result


class _Audit:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def record_success(self, **kwargs):
        self.calls.append(("success", kwargs))
        if self.error:
            raise self.error

    def record_failure(self, **kwargs):
        self.calls.append(("failure", kwargs))
        if self.error:
            raise self.error


class _Observability:
    def __init__(self):
        self.calls: list[dict] = []

    def record(self, **kwargs):
        self.calls.append(kwargs)


def _result() -> PasswordLoginResult:
    return PasswordLoginResult(
        user=PasswordLoginUser(
            id="opaque-user-id",
            email="member@example.com",
            name="Member",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        session=PasswordLoginSession(
            token="synthetic-token",
            expires_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
    )


def _command() -> PasswordLoginCommand:
    return PasswordLoginCommand(
        account="  Member@Example.COM  ",
        password="synthetic-password",
        source_network="192.0.2.0/24",
        request_id="request-1",
    )


def _use_case(*, limiter=None, authenticator=None, audit=None, observability=None):
    return PasswordLogin(
        limiter=limiter or _Limiter(),
        authenticator=authenticator or _Authenticator(),
        audit=audit or _Audit(),
        observability=observability or _Observability(),
        policy_version="v1",
    )


def test_admission_runs_before_credential_verification_and_success_resets_scoped_state():
    limiter = _Limiter()
    authenticator = _Authenticator()
    audit = _Audit()
    observability = _Observability()

    result = _use_case(
        limiter=limiter,
        authenticator=authenticator,
        audit=audit,
        observability=observability,
    ).execute(_command())

    assert result == authenticator.result
    assert limiter.calls == [
        ("admit", "member@example.com", "192.0.2.0/24"),
        ("reset", "member@example.com", "192.0.2.0/24"),
    ]
    assert authenticator.calls == [
        ("  Member@Example.COM  ", "synthetic-password")
    ]
    assert audit.calls == [
        (
            "success",
            {
                "request_id": "request-1",
                "policy_version": "v1",
                "actor_id": "opaque-user-id",
                "actor_name": "Member",
            },
        )
    ]
    assert observability.calls == [
        {"outcome": "succeeded", "policy_version": "v1", "operation": "login"}
    ]


def test_blocked_admission_never_calls_authenticator_or_consumes_other_work():
    limiter = _Limiter(
        admission=LoginAdmission(
            allowed=False,
            retry_after_seconds=17,
            limited_dimensions=(
                LoginLimitDimension.ACCOUNT,
                LoginLimitDimension.ACCOUNT_NETWORK,
            ),
        )
    )
    authenticator = _Authenticator()
    audit = _Audit()

    with pytest.raises(LoginRateLimited) as exc_info:
        _use_case(limiter=limiter, authenticator=authenticator, audit=audit).execute(
            _command()
        )

    assert exc_info.value.retry_after_seconds == 17
    assert exc_info.value.limited_dimensions == (
        LoginLimitDimension.ACCOUNT,
        LoginLimitDimension.ACCOUNT_NETWORK,
    )
    assert authenticator.calls == []
    assert limiter.calls == [("admit", "member@example.com", "192.0.2.0/24")]
    assert audit.calls[0][1]["reason_code"] == "auth.login.rate_limited"
    assert "account_identity" not in audit.calls[0][1]


def test_limiter_failure_fails_closed_before_authentication():
    limiter = _Limiter(admission_error=LimiterBackendError(operation="admission"))
    authenticator = _Authenticator()
    audit = _Audit()

    with pytest.raises(LoginTemporarilyUnavailable):
        _use_case(limiter=limiter, authenticator=authenticator, audit=audit).execute(
            _command()
        )

    assert authenticator.calls == []
    assert audit.calls[0][1]["reason_code"] == "auth.login.limiter_unavailable"


@pytest.mark.parametrize(
    ("error", "reason_code"),
    [
        (InvalidCredentials(), "auth.login.invalid_credentials"),
        (InactiveAccount(), "auth.login.inactive"),
    ],
)
def test_credential_failure_is_audited_without_reset(error, reason_code):
    limiter = _Limiter()
    authenticator = _Authenticator(error=error)
    audit = _Audit()

    with pytest.raises(type(error)):
        _use_case(limiter=limiter, authenticator=authenticator, audit=audit).execute(
            _command()
        )

    assert limiter.calls == [("admit", "member@example.com", "192.0.2.0/24")]
    assert audit.calls[0][1]["reason_code"] == reason_code


def test_reset_failure_does_not_turn_committed_login_into_failure():
    limiter = _Limiter(reset_error=LimiterBackendError(operation="reset"))
    observability = _Observability()

    result = _use_case(limiter=limiter, observability=observability).execute(_command())

    assert result == _result()
    assert observability.calls == [
        {"outcome": "reset_failed", "policy_version": "v1", "operation": "reset"},
        {"outcome": "succeeded", "policy_version": "v1", "operation": "login"},
    ]


def test_audit_failure_does_not_change_authentication_result():
    result = _use_case(audit=_Audit(error=RuntimeError("audit unavailable"))).execute(
        _command()
    )

    assert result == _result()


def test_unexpected_authenticator_error_is_replaced_with_safe_typed_error():
    raw_error = RuntimeError("raw-account@example.com")
    authenticator = _Authenticator(error=raw_error)
    audit = _Audit()

    with pytest.raises(PasswordLoginInternalError) as exc_info:
        _use_case(authenticator=authenticator, audit=audit).execute(_command())

    assert exc_info.value.__suppress_context__ is True
    assert "raw-account@example.com" not in str(exc_info.value)
    assert audit.calls[0][1]["reason_code"] == "auth.login.internal_error"
