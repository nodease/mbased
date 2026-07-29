import re

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.gateway.application.csrf.models import (
    CsrfContentKind,
    CsrfRoutePolicy,
    CsrfRoutePolicyKind,
    CsrfRoutePolicyRegistry,
)
from apps.gateway.application.csrf.token import (
    CSRF_ANON_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CsrfBindingKind,
    CsrfTokenService,
)
from apps.gateway.middleware.csrf import CsrfProtectionMiddleware


ORIGIN = "https://client.example"
NOW_HEADER = {"X-Request-ID": "csrf-request-id"}


def _policy(
    method: str,
    path: str,
    kind: CsrfRoutePolicyKind,
    *,
    content_kind: CsrfContentKind = CsrfContentKind.JSON,
) -> CsrfRoutePolicy:
    return CsrfRoutePolicy(
        method=method,
        path_template=path,
        path_pattern=re.compile(f"^{re.escape(path)}$"),
        policy_kind=kind,
        content_kind=content_kind,
    )


def _build_app(
    token_service: CsrfTokenService,
    denied: list[tuple[str, str, str, str]],
):
    app = FastAPI()
    effects = {"protected": 0, "public": 0, "pre_auth": 0}

    @app.post("/protected")
    async def protected(request: Request):
        await request.body()
        effects["protected"] += 1
        return {"ok": True}

    @app.post("/public")
    async def public():
        effects["public"] += 1
        return {"ok": True}

    @app.post("/pre-auth")
    async def pre_auth():
        effects["pre_auth"] += 1
        return {"ok": True}

    registry = CsrfRoutePolicyRegistry(
        (
            _policy(
                "POST",
                "/protected",
                CsrfRoutePolicyKind.COOKIE_AUTHENTICATED,
            ),
            _policy(
                "POST",
                "/public",
                CsrfRoutePolicyKind.PUBLIC_ANONYMOUS,
                content_kind=CsrfContentKind.UNRESTRICTED,
            ),
            _policy("POST", "/pre-auth", CsrfRoutePolicyKind.PRE_AUTH_SESSION),
        )
    )

    def on_denied(reason, policy, method, request_id):
        denied.append((reason.value, policy.policy_kind.value, method, request_id))

    app.add_middleware(
        CsrfProtectionMiddleware,
        registry=registry,
        token_service=token_service,
        allowed_origins=(ORIGIN,),
        enforcement_enabled=True,
        on_denied=on_denied,
    )
    return app, effects


@pytest.fixture
def token_service() -> CsrfTokenService:
    return CsrfTokenService.from_root_secret(
        "middleware-test-session-secret",
        ttl_seconds=600,
        nonce_factory=lambda size: b"m" * size,
    )


def _authenticated_headers(
    token_service: CsrfTokenService,
    *,
    session: str = "session-a",
    organization: str | None = "organization-a",
) -> tuple[dict[str, str], str]:
    issued = token_service.issue(
        binding_kind=CsrfBindingKind.AUTHENTICATED,
        binding_secret=session,
        organization_scope=organization,
    )
    headers = {
        **NOW_HEADER,
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-site",
        "Content-Type": "application/json",
        CSRF_HEADER_NAME: issued.token,
    }
    if organization is not None:
        headers["X-Organization-Id"] = organization
    return headers, issued.token


def test_valid_cookie_authenticated_request_reaches_endpoint(
    token_service: CsrfTokenService,
):
    denied: list[tuple[str, str, str, str]] = []
    app, effects = _build_app(token_service, denied)
    headers, token = _authenticated_headers(token_service)

    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("auth_token", "session-a")
        client.cookies.set(CSRF_COOKIE_NAME, token)
        response = client.post("/protected", headers=headers, json={"value": 1})

    assert response.status_code == 200
    assert effects["protected"] == 1
    assert denied == []


@pytest.mark.parametrize(
    ("header_changes", "cookie_token", "expected_reason"),
    [
        ({"Origin": None}, None, "origin_invalid"),
        ({"Origin": "null"}, None, "origin_invalid"),
        ({"Origin": "https://attacker.example"}, None, "origin_invalid"),
        ({"Sec-Fetch-Site": "cross-site"}, None, "fetch_metadata_invalid"),
        ({"Content-Type": "text/plain"}, None, "content_type_invalid"),
        ({CSRF_HEADER_NAME: None}, None, "token_missing"),
        ({CSRF_HEADER_NAME: "forged"}, "forged", "token_invalid"),
        ({"X-Organization-Id": "organization-b"}, None, "token_invalid"),
    ],
)
def test_invalid_browser_boundary_is_rejected_before_body_or_side_effect(
    token_service: CsrfTokenService,
    header_changes: dict[str, str | None],
    cookie_token: str | None,
    expected_reason: str,
):
    denied: list[tuple[str, str, str, str]] = []
    app, effects = _build_app(token_service, denied)
    headers, issued_token = _authenticated_headers(token_service)
    for name, value in header_changes.items():
        if value is None:
            headers.pop(name, None)
        else:
            headers[name] = value

    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("auth_token", "session-a")
        client.cookies.set(CSRF_COOKIE_NAME, cookie_token or issued_token)
        response = client.post(
            "/protected",
            headers=headers,
            content='{"sentinel":"request-body-must-not-be-read"}',
        )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "auth.csrf_validation_failed",
            "message": "CSRF validation failed.",
            "request_id": "csrf-request-id",
        }
    }
    assert effects["protected"] == 0
    assert denied == [
        (
            expected_reason,
            "cookie_authenticated",
            "POST",
            "csrf-request-id",
        )
    ]


def test_cookie_authenticated_route_without_auth_cookie_returns_401_before_effect(
    token_service: CsrfTokenService,
):
    denied: list[tuple[str, str, str, str]] = []
    app, effects = _build_app(token_service, denied)

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/protected",
            headers={**NOW_HEADER, "Origin": ORIGIN},
            json={"value": 1},
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "auth.required",
            "message": "Authentication is required.",
            "request_id": "csrf-request-id",
        }
    }
    assert effects["protected"] == 0
    assert denied == []


def test_pre_auth_token_uses_anonymous_seed_without_auth_cookie(
    token_service: CsrfTokenService,
):
    denied: list[tuple[str, str, str, str]] = []
    app, effects = _build_app(token_service, denied)
    issued = token_service.issue(
        binding_kind=CsrfBindingKind.PRE_AUTH,
        binding_secret="anonymous-seed",
        organization_scope=None,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set(CSRF_ANON_COOKIE_NAME, "anonymous-seed")
        client.cookies.set(CSRF_COOKIE_NAME, issued.token)
        response = client.post(
            "/pre-auth",
            headers={
                **NOW_HEADER,
                "Origin": ORIGIN,
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "application/json",
                CSRF_HEADER_NAME: issued.token,
            },
            json={"email": "user@example.com"},
        )

    assert response.status_code == 200
    assert effects["pre_auth"] == 1
    assert denied == []


def test_public_route_ignores_login_and_csrf_cookies(
    token_service: CsrfTokenService,
):
    denied: list[tuple[str, str, str, str]] = []
    app, effects = _build_app(token_service, denied)

    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("auth_token", "session-sentinel")
        client.cookies.set(CSRF_COOKIE_NAME, "csrf-sentinel")
        response = client.post(
            "/public",
            headers={"Origin": "https://external.example"},
            content="public payload",
        )

    assert response.status_code == 200
    assert effects["public"] == 1
    assert denied == []
