import threading

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import auth as auth_endpoint
from apps.gateway.application.csrf.token import (
    CSRF_ANON_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CsrfTokenService,
    CsrfValidationReason,
)
from apps.gateway.services.auth_service import AuthService
from apps.shared.db.session import get_db


def _client() -> TestClient:
    app = FastAPI()
    app.state.credentialed_cors_origins = ("https://client.example",)
    app.include_router(auth_endpoint.router, prefix="/auth")
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app, base_url="http://localhost")


def _bootstrap_headers() -> dict[str, str]:
    return {
        "X-CSRF-Bootstrap": "1",
        "Sec-Fetch-Site": "same-origin",
    }


def test_anonymous_csrf_bootstrap_sets_host_only_http_only_cookies(monkeypatch):
    monkeypatch.setattr(
        auth_endpoint,
        "csrf_token_service",
        lambda: CsrfTokenService.from_root_secret("csrf-endpoint-test-secret"),
    )

    with _client() as client:
        response = client.get(
            "/auth/csrf",
            headers={
                **_bootstrap_headers(),
                "Origin": "https://client.example",
                "Sec-Fetch-Site": "same-site",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["token"], str)
    assert payload["token"] == response.cookies[CSRF_COOKIE_NAME]
    assert "anonymous" not in response.text.lower()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    cookies = response.headers.get_list("set-cookie")
    assert any(
        cookie.startswith(f"{CSRF_COOKIE_NAME}=")
        and "HttpOnly" in cookie
        and "Path=/api/v1" in cookie
        and "Domain=" not in cookie
        for cookie in cookies
    )
    assert any(
        cookie.startswith(f"{CSRF_ANON_COOKIE_NAME}=")
        and "HttpOnly" in cookie
        and "Domain=" not in cookie
        for cookie in cookies
    )


def test_authenticated_csrf_bootstrap_validates_cookie_and_clears_anon_seed(
    monkeypatch,
):
    monkeypatch.setattr(AuthService, "get_user_from_token", lambda db, token: object())
    monkeypatch.setattr(
        auth_endpoint,
        "csrf_token_service",
        lambda: CsrfTokenService.from_root_secret("csrf-endpoint-test-secret"),
    )

    with _client() as client:
        client.cookies.set("auth_token", "valid-auth-token")
        client.cookies.set(CSRF_ANON_COOKIE_NAME, "stale-anonymous-seed")
        response = client.get(
            "/auth/csrf",
            headers={
                **_bootstrap_headers(),
                "X-Organization-Id": "organization-a",
            },
        )

    assert response.status_code == 200
    assert response.cookies[CSRF_COOKIE_NAME] == response.json()["token"]
    assert any(
        cookie.startswith(f"{CSRF_ANON_COOKIE_NAME}=") and "Max-Age=0" in cookie
        for cookie in response.headers.get_list("set-cookie")
    )


def test_authenticated_bootstrap_reuses_same_session_and_scope_cookie(monkeypatch):
    monkeypatch.setattr(AuthService, "get_user_from_token", lambda db, token: object())
    monkeypatch.setattr(
        auth_endpoint,
        "csrf_token_service",
        lambda: CsrfTokenService.from_root_secret("csrf-endpoint-test-secret"),
    )

    with _client() as client:
        client.cookies.set("auth_token", "valid-auth-token")
        first = client.get(
            "/auth/csrf",
            headers={
                **_bootstrap_headers(),
                "X-Organization-Id": "organization-a",
            },
        )
        first_token = first.json()["token"]

        # TestClient의 축약 route와 production cookie path가 다르므로
        # 두 번째 탭이 공유 cookie를 보내는 상태를 명시적으로 구성한다.
        client.cookies.clear()
        client.cookies.set("auth_token", "valid-auth-token")
        client.cookies.set(CSRF_COOKIE_NAME, first_token)
        second = client.get(
            "/auth/csrf",
            headers={
                **_bootstrap_headers(),
                "X-Organization-Id": "organization-a",
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["token"] == first_token
    assert second.cookies[CSRF_COOKIE_NAME] == first_token


def test_invalid_auth_cookie_cannot_fall_back_to_anonymous_bootstrap(monkeypatch):
    def reject_invalid_cookie(_db, _token):
        raise HTTPException(status_code=401, detail="invalid")

    monkeypatch.setattr(AuthService, "get_user_from_token", reject_invalid_cookie)

    with _client() as client:
        client.cookies.set("auth_token", "invalid-auth-token")
        response = client.get("/auth/csrf", headers=_bootstrap_headers())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid"
    cookies = response.headers.get_list("set-cookie")
    assert any(cookie.startswith("auth_token=") for cookie in cookies)
    assert any(cookie.startswith(f"{CSRF_COOKIE_NAME}=") for cookie in cookies)
    assert any(cookie.startswith(f"{CSRF_ANON_COOKIE_NAME}=") for cookie in cookies)


def test_inactive_auth_cookie_is_cleared_before_anonymous_recovery(monkeypatch):
    def reject_inactive_cookie(_db, _token):
        raise HTTPException(status_code=403, detail="inactive")

    monkeypatch.setattr(AuthService, "get_user_from_token", reject_inactive_cookie)

    with _client() as client:
        client.cookies.set("auth_token", "inactive-auth-token")
        response = client.get("/auth/csrf", headers=_bootstrap_headers())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid"
    cookies = response.headers.get_list("set-cookie")
    assert any(
        cookie.startswith("auth_token=") and "Max-Age=0" in cookie
        for cookie in cookies
    )
    assert any(
        cookie.startswith(f"{CSRF_COOKIE_NAME}=") and "Max-Age=0" in cookie
        for cookie in cookies
    )
    assert any(
        cookie.startswith(f"{CSRF_ANON_COOKIE_NAME}=") and "Max-Age=0" in cookie
        for cookie in cookies
    )


def test_untrusted_bootstrap_requests_cannot_rotate_csrf_cookies(monkeypatch):
    monkeypatch.setattr(
        auth_endpoint,
        "csrf_token_service",
        lambda: CsrfTokenService.from_root_secret("csrf-endpoint-test-secret"),
    )

    with _client() as client:
        missing_proof = client.get(
            "/auth/csrf",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        untrusted_origin = client.get(
            "/auth/csrf",
            headers={
                "X-CSRF-Bootstrap": "1",
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "same-site",
            },
        )

    for response in (missing_proof, untrusted_origin):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "auth.csrf_validation_failed"
        assert response.headers.get_list("set-cookie") == []


def test_invalid_organization_scope_uses_fixed_csrf_denial(monkeypatch):
    recorded_reasons: list[str] = []

    def record_denial(reason, *, request_id):
        recorded_reasons.append(reason.value)

    monkeypatch.setattr(
        auth_endpoint,
        "csrf_token_service",
        lambda: CsrfTokenService.from_root_secret("csrf-endpoint-test-secret"),
    )
    monkeypatch.setattr(
        auth_endpoint,
        "record_csrf_bootstrap_denial",
        record_denial,
    )

    with _client() as client:
        response = client.get(
            "/auth/csrf",
            headers={
                **_bootstrap_headers(),
                "X-Organization-Id": "o" * 129,
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.csrf_validation_failed"
    assert response.json()["error"]["message"] == "CSRF validation failed."
    assert response.headers.get_list("set-cookie") == []
    assert recorded_reasons == ["organization_scope_invalid"]


def test_bootstrap_denial_audit_runs_outside_request_execution_thread(monkeypatch):
    request_threads: list[int] = []
    audit_threads: list[int] = []

    def reject_bootstrap(_headers, *, allowed_origins):
        request_threads.append(threading.get_ident())
        return CsrfValidationReason.FETCH_METADATA_INVALID

    def record_denial(_reason, *, request_id):
        audit_threads.append(threading.get_ident())

    monkeypatch.setattr(
        auth_endpoint,
        "validate_csrf_bootstrap_request",
        reject_bootstrap,
    )
    monkeypatch.setattr(
        auth_endpoint,
        "record_csrf_bootstrap_denial",
        record_denial,
    )

    with _client() as client:
        response = client.get("/auth/csrf")

    assert response.status_code == 403
    assert request_threads
    assert audit_threads
    assert request_threads[0] != audit_threads[0]


def test_logout_clears_auth_and_csrf_cookie_families():
    with _client() as client:
        response = client.post("/auth/logout")

    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert any(cookie.startswith("auth_token=") for cookie in cookies)
    assert any(cookie.startswith(f"{CSRF_COOKIE_NAME}=") for cookie in cookies)
    assert any(cookie.startswith(f"{CSRF_ANON_COOKIE_NAME}=") for cookie in cookies)
