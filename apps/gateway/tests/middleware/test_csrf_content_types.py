import re

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.gateway.application.csrf.models import (
    CsrfContentKind,
    CsrfRoutePolicy,
    CsrfRoutePolicyKind,
    CsrfRoutePolicyRegistry,
)
from apps.gateway.application.csrf.token import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CsrfBindingKind,
    CsrfTokenService,
)
from apps.gateway.middleware.csrf import CsrfProtectionMiddleware


ORIGIN = "https://client.example"


def _policy(path: str, content_kind: CsrfContentKind) -> CsrfRoutePolicy:
    return CsrfRoutePolicy(
        method="POST",
        path_template=path,
        path_pattern=re.compile(f"^{re.escape(path)}$"),
        policy_kind=CsrfRoutePolicyKind.COOKIE_AUTHENTICATED,
        content_kind=content_kind,
    )


def _app_and_headers():
    app = FastAPI()
    effects = {"json": 0, "multipart": 0, "optional": 0}

    @app.post("/json")
    async def json_endpoint(request: Request):
        await request.body()
        effects["json"] += 1
        return {"ok": True}

    @app.post("/multipart")
    async def multipart_endpoint(request: Request):
        await request.body()
        effects["multipart"] += 1
        return {"ok": True}

    @app.post("/optional")
    async def optional_endpoint():
        effects["optional"] += 1
        return {"ok": True}

    service = CsrfTokenService.from_root_secret("content-type-middleware-test-secret")
    issued = service.issue(
        binding_kind=CsrfBindingKind.AUTHENTICATED,
        binding_secret="session-a",
        organization_scope=None,
    )
    registry = CsrfRoutePolicyRegistry(
        (
            _policy("/json", CsrfContentKind.JSON),
            _policy("/multipart", CsrfContentKind.MULTIPART),
            _policy("/optional", CsrfContentKind.BODY_OPTIONAL),
        )
    )
    app.add_middleware(
        CsrfProtectionMiddleware,
        registry=registry,
        token_service=service,
        allowed_origins=(ORIGIN,),
        enforcement_enabled=True,
        on_denied=lambda *_args: None,
    )
    headers = {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: issued.token,
    }
    return app, effects, headers, issued.token


def test_json_utf8_multipart_and_empty_body_contracts_are_accepted():
    app, effects, headers, token = _app_and_headers()

    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("auth_token", "session-a")
        client.cookies.set(CSRF_COOKIE_NAME, token)
        json_response = client.post(
            "/json",
            headers={**headers, "Content-Type": "application/json; charset=UTF-8"},
            content="{}",
        )
        multipart_response = client.post(
            "/multipart",
            headers=headers,
            files={"file": ("document.txt", b"content", "text/plain")},
        )
        optional_response = client.post(
            "/optional",
            headers={
                **headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": "0",
            },
            content=b"",
        )

    assert json_response.status_code == 200
    assert multipart_response.status_code == 200
    assert optional_response.status_code == 200
    assert effects == {"json": 1, "multipart": 1, "optional": 1}


def test_unexpected_json_parameters_and_malformed_multipart_fail_before_effects():
    app, effects, headers, token = _app_and_headers()

    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("auth_token", "session-a")
        client.cookies.set(CSRF_COOKIE_NAME, token)
        json_response = client.post(
            "/json",
            headers={
                **headers,
                "Content-Type": "application/json; profile=unexpected",
            },
            content="{}",
        )
        multipart_response = client.post(
            "/multipart",
            headers={
                **headers,
                "Content-Type": "multipart/form-data; boundary=bad boundary",
            },
            content=b"sentinel",
        )
        nonempty_optional_response = client.post(
            "/optional",
            headers={
                **headers,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content=b"sentinel",
        )

    assert json_response.status_code == 403
    assert multipart_response.status_code == 403
    assert nonempty_optional_response.status_code == 403
    assert effects == {"json": 0, "multipart": 0, "optional": 0}
