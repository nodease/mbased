import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from apps.gateway.api.v1.endpoints import auth as auth_endpoint
from apps.shared.db.session import get_db


class _FakeGoogleOAuth:
    def __init__(self):
        self.redirect_uri = None
        self.start_error = None
        self.token_error = None
        self.token_result = {
            "userinfo": {
                "email": "member@example.com",
                "name": "Member",
                "sub": "google-subject",
            }
        }
        self.userinfo_error = None
        self.userinfo_result = None

    async def authorize_redirect(self, request, redirect_uri):
        self.redirect_uri = str(redirect_uri)
        if self.start_error:
            raise self.start_error
        return RedirectResponse("https://accounts.example/authorize", status_code=302)

    async def authorize_access_token(self, request):
        if self.token_error:
            raise self.token_error
        return self.token_result

    async def userinfo(self, *, token):
        if self.userinfo_error:
            raise self.userinfo_error
        return self.userinfo_result


@pytest.fixture
def oauth_app(monkeypatch):
    fake_google = _FakeGoogleOAuth()
    audit_events = []
    user = SimpleNamespace(
        id=uuid4(),
        email="member@example.com",
        name="Member",
    )

    monkeypatch.setattr(
        auth_endpoint,
        "oauth",
        SimpleNamespace(google=fake_google),
    )
    monkeypatch.setattr(
        auth_endpoint,
        "record_audit",
        lambda **event: audit_events.append(event),
    )
    monkeypatch.setattr(
        auth_endpoint.AuthService,
        "get_or_create_social_user",
        staticmethod(lambda **kwargs: user),
    )
    monkeypatch.setattr(
        auth_endpoint.AuthService,
        "mark_login_success",
        staticmethod(lambda db, current_user: None),
    )
    monkeypatch.setattr(
        auth_endpoint.AuthService,
        "create_jwt_token",
        staticmethod(lambda user_id: "synthetic-test-token"),
    )

    app = FastAPI()
    app.include_router(auth_endpoint.router, prefix="/auth")
    app.add_middleware(
        SessionMiddleware,
        secret_key="synthetic-test-session-signing-key",
    )
    app.dependency_overrides[get_db] = lambda: object()

    return app, fake_google, audit_events


def _client(app: FastAPI, *, base_url: str = "http://localhost:8000"):
    return TestClient(app, base_url=base_url, follow_redirects=False)


def test_google_oauth_returns_to_signed_session_path_once(oauth_app):
    app, _, _ = oauth_app
    client = _client(app)
    return_path = "/modules/workflow-1/run?deploymentId=deployment-1#result"

    start = client.get("/auth/google/login", params={"next": return_path})
    callback = client.get("/auth/google/callback")
    replay = client.get("/auth/google/callback")

    assert start.status_code == 302
    assert start.headers["location"] == "https://accounts.example/authorize"
    assert callback.status_code == 302
    assert callback.headers["location"] == (
        "http://localhost:3000/modules/workflow-1/run"
        "?deploymentId=deployment-1#result"
    )
    assert replay.status_code == 302
    assert replay.headers["location"] == "http://localhost:3000/dashboard"


@pytest.mark.parametrize(
    "return_path",
    [
        "https://attacker.example/steal",
        "//attacker.example/steal",
        "/%25252f%25252fattacker.example/steal",
        "/safe/../steal",
    ],
)
def test_google_oauth_rejects_unsafe_return_path(oauth_app, return_path):
    app, _, _ = oauth_app
    client = _client(app)

    client.get("/auth/google/login", params={"next": return_path})
    callback = client.get("/auth/google/callback")

    assert callback.headers["location"] == "http://localhost:3000/dashboard"
    assert "attacker.example" not in callback.headers["location"]


def test_google_oauth_uses_https_callback_for_non_local_host(oauth_app):
    app, fake_google, _ = oauth_app
    client = _client(app, base_url="http://api.example")

    response = client.get("/auth/google/login")

    assert response.status_code == 302
    assert fake_google.redirect_uri == "https://api.example/auth/google/callback"


def test_google_oauth_does_not_treat_lookalike_host_as_local(oauth_app):
    app, fake_google, _ = oauth_app
    client = _client(app, base_url="http://localhost.attacker.example")

    response = client.get("/auth/google/login")

    assert response.status_code == 302
    assert fake_google.redirect_uri == (
        "https://localhost.attacker.example/auth/google/callback"
    )


def test_google_oauth_start_failure_is_fixed_and_forgets_return_path(
    oauth_app,
    caplog,
):
    app, fake_google, audit_events = oauth_app
    client = _client(app)
    raw_marker = "raw-provider-secret-marker"
    fake_google.start_error = RuntimeError(raw_marker)

    with caplog.at_level(logging.WARNING):
        failed = client.get(
            "/auth/google/login",
            params={"next": "/modules/workflow-1/run"},
        )

    fake_google.start_error = None
    callback = client.get("/auth/google/callback")

    assert failed.status_code == 503
    assert failed.text == "OAuth login is unavailable"
    assert raw_marker not in failed.text
    assert raw_marker not in caplog.text
    assert raw_marker not in repr(audit_events)
    assert callback.headers["location"] == "http://localhost:3000/dashboard"


def test_google_oauth_token_failure_is_fixed_and_consumes_return_path(
    oauth_app,
    caplog,
):
    app, fake_google, audit_events = oauth_app
    client = _client(app)
    raw_marker = "raw-provider-secret-marker"

    client.get(
        "/auth/google/login",
        params={"next": "/modules/workflow-1/run"},
    )
    fake_google.token_error = RuntimeError(raw_marker)
    with caplog.at_level(logging.WARNING):
        failed = client.get("/auth/google/callback")

    fake_google.token_error = None
    retry = client.get("/auth/google/callback")

    assert failed.status_code == 400
    assert failed.text == "OAuth authentication failed"
    assert raw_marker not in failed.text
    assert raw_marker not in caplog.text
    assert raw_marker not in repr(audit_events)
    assert retry.headers["location"] == "http://localhost:3000/dashboard"


@pytest.mark.parametrize(
    ("token_result", "userinfo_result"),
    [
        ([], None),
        ({"userinfo": []}, []),
        ({"userinfo": {}}, {"name": "Missing Email"}),
    ],
)
def test_google_oauth_rejects_malformed_identity_responses(
    oauth_app,
    token_result,
    userinfo_result,
):
    app, fake_google, _ = oauth_app
    client = _client(app)
    fake_google.token_result = token_result
    fake_google.userinfo_result = userinfo_result

    client.get("/auth/google/login")
    response = client.get("/auth/google/callback")

    assert response.status_code == 400
    assert response.text == "OAuth authentication failed"
    assert "Missing Email" not in response.text
