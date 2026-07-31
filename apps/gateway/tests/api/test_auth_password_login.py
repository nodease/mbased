from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import auth as auth_endpoint
from apps.gateway.application.authentication.errors import (
    InactiveAccount,
    InvalidCredentials,
    LoginRateLimited,
    LoginTemporarilyUnavailable,
    PasswordLoginInternalError,
)
from apps.gateway.application.authentication.models import (
    LoginLimitDimension,
    PasswordLoginResult,
    PasswordLoginSession,
    PasswordLoginUser,
)
from apps.gateway.main import audit_permission_denied
from apps.shared.db.session import get_db


class _UseCase:
    def __init__(self, *, result=None, error: Exception | None = None):
        self.result = result or _result()
        self.error = error
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        if self.error:
            raise self.error
        return self.result


class _NetworkResolver:
    def __init__(self, result="198.51.100.0/24"):
        self.result = result
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return self.result


def _result():
    return PasswordLoginResult(
        user=PasswordLoginUser(
            id="user-id",
            email="member@example.com",
            name="Member",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        session=PasswordLoginSession(
            token="synthetic-token",
            expires_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
    )


@pytest.fixture
def login_client(monkeypatch):
    use_case = _UseCase()
    resolver = _NetworkResolver()
    global_audits = []

    monkeypatch.setattr(auth_endpoint, "build_password_login", lambda db: use_case)
    monkeypatch.setattr(auth_endpoint, "login_network_resolver", lambda: resolver)

    app = FastAPI()
    app.include_router(auth_endpoint.router, prefix="/auth")
    app.add_exception_handler(HTTPException, audit_permission_denied)
    app.dependency_overrides[get_db] = lambda: object()

    from apps.gateway import main

    monkeypatch.setattr(main, "record_audit", lambda **event: global_audits.append(event))
    return TestClient(app, base_url="http://localhost:8000"), use_case, resolver, global_audits


def test_login_success_preserves_response_and_cookie_contract(login_client):
    client, use_case, resolver, _ = login_client

    response = client.post(
        "/auth/login",
        json={"email": "Member@Example.com", "password": "synthetic-password"},
        headers={"X-Request-ID": "request-1"},
    )

    assert response.status_code == 200
    assert response.json()["session"]["token"] == "synthetic-token"
    assert response.json()["user"]["email"] == "member@example.com"
    assert response.cookies["auth_token"] == "synthetic-token"
    assert use_case.commands[0].account == "Member@example.com"
    assert use_case.commands[0].source_network == "198.51.100.0/24"
    assert use_case.commands[0].request_id == "request-1"
    assert len(resolver.requests) == 1


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (InvalidCredentials(), 401, "이메일 또는 비밀번호가 올바르지 않습니다"),
        (InactiveAccount(), 403, "비활성화된 계정입니다"),
    ],
)
def test_specialized_login_failure_suppresses_duplicate_global_audit(
    login_client,
    error,
    status,
    detail,
):
    client, use_case, _, global_audits = login_client
    use_case.error = error

    response = client.post(
        "/auth/login",
        json={"email": "member@example.com", "password": "wrong-password"},
    )

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert global_audits == []


def test_rate_limited_login_returns_generic_bounded_retry(login_client):
    client, use_case, _, _ = login_client
    use_case.error = LoginRateLimited(
        retry_after_seconds=23,
        limited_dimensions=(LoginLimitDimension.ACCOUNT_NETWORK,),
    )

    response = client.post(
        "/auth/login",
        json={"email": "member@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "23"
    assert response.json() == {
        "detail": "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요."
    }
    assert "account_network" not in response.text


def test_limiter_unavailable_returns_generic_retry_without_cookie(login_client):
    client, use_case, _, _ = login_client
    use_case.error = LoginTemporarilyUnavailable()

    response = client.post(
        "/auth/login",
        json={"email": "member@example.com", "password": "synthetic-password"},
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert response.json() == {"detail": "로그인을 일시적으로 사용할 수 없습니다."}
    assert "auth_token" not in response.cookies


def test_unexpected_login_backend_failure_returns_fixed_safe_error(login_client):
    client, use_case, _, _ = login_client
    use_case.error = PasswordLoginInternalError()

    response = client.post(
        "/auth/login",
        json={"email": "member@example.com", "password": "synthetic-password"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "로그인을 처리할 수 없습니다."}
    assert "member@example.com" not in response.text


def test_invalid_request_does_not_build_or_execute_login(login_client, monkeypatch):
    client, use_case, _, _ = login_client
    calls = []
    monkeypatch.setattr(auth_endpoint, "build_password_login", lambda db: calls.append(db))

    response = client.post(
        "/auth/login",
        json={"email": "not-an-email", "password": "synthetic-password"},
    )

    assert response.status_code == 422
    assert calls == []
    assert use_case.commands == []
