from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from apps.gateway.api.deps import require_json_content_type
from apps.gateway.middleware.public_conversation_cors import (
    PublicConversationCorsBoundaryMiddleware,
)


class _EmptyConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://parent.example"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Added after CORS so it is the outer boundary in the production stack.
    app.add_middleware(PublicConversationCorsBoundaryMiddleware)

    @app.post("/api/v1/run-public/chat/conversations")
    def conversation_create():
        return {"ok": True}

    @app.post("/api/v1/run-public/chat/conversation/close")
    def conversation_close(
        _body: _EmptyConversationRequest,
        _content_type: None = Depends(require_json_content_type),
    ):
        return {"ok": True}

    @app.get("/api/v1/run-public/chat/conversation/explicit-error")
    def conversation_error():
        raise HTTPException(status_code=409, detail="safe conflict")

    @app.post("/api/v1/run-public/chat")
    def legacy_public_run():
        return {"ok": True}

    @app.post("/api/v1/run-public/chat/chat")
    def public_chat_run(
        _body: _EmptyConversationRequest,
        _content_type: None = Depends(require_json_content_type),
    ):
        return {"ok": True}

    return TestClient(app)


def test_public_conversation_preflight_is_not_a_cors_grant():
    response = _client().options(
        "/api/v1/run-public/chat/conversations",
        headers={
            "Origin": "https://parent.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 404
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_public_chat_run_preflight_is_not_a_cors_grant():
    response = _client().options(
        "/api/v1/run-public/chat/chat",
        headers={
            "Origin": "https://parent.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 404
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_public_chat_run_framework_failures_never_inherit_global_cors():
    client = _client()
    responses = {
        "wrong-content-type": client.post(
            "/api/v1/run-public/chat/chat",
            content="{}",
            headers={
                "Content-Type": "text/plain",
                "Origin": "https://parent.example",
            },
        ),
        "malformed-json": client.post(
            "/api/v1/run-public/chat/chat",
            content="{",
            headers={
                "Content-Type": "application/json",
                "Origin": "https://parent.example",
            },
        ),
        "unknown-field": client.post(
            "/api/v1/run-public/chat/chat",
            json={"unexpected": True},
            headers={"Origin": "https://parent.example"},
        ),
    }

    assert {name: response.status_code for name, response in responses.items()} == {
        "wrong-content-type": 415,
        "malformed-json": 422,
        "unknown-field": 422,
    }
    for name, response in responses.items():
        assert response.headers["cache-control"] == "no-store", name
        assert response.headers["referrer-policy"] == "no-referrer", name
        assert "access-control-allow-origin" not in response.headers, name
        assert "access-control-allow-credentials" not in response.headers, name
        assert "origin" not in response.headers.get("vary", "").lower(), name


def test_trailing_slash_create_preflight_is_not_a_cors_grant():
    response = _client().options(
        "/api/v1/run-public/chat/conversations/",
        headers={
            "Origin": "https://parent.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 404
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_public_conversation_response_never_inherits_global_cors_or_vary_origin():
    response = _client().post(
        "/api/v1/run-public/chat/conversations",
        headers={"Origin": "https://parent.example"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers
    assert "origin" not in response.headers.get("vary", "").lower()

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_public_security_headers_cover_pre_endpoint_and_router_failures():
    client = _client()
    responses = {
        "explicit-error": client.get(
            "/api/v1/run-public/chat/conversation/explicit-error"
        ),
        "wrong-content-type": client.post(
            "/api/v1/run-public/chat/conversation/close",
            content="{}",
            headers={"Content-Type": "text/plain"},
        ),
        "malformed-json": client.post(
            "/api/v1/run-public/chat/conversation/close",
            content="{",
            headers={"Content-Type": "application/json"},
        ),
        "unknown-field": client.post(
            "/api/v1/run-public/chat/conversation/close",
            json={"unexpected": True},
        ),
        "not-found": client.get("/api/v1/run-public/chat/conversation/not-found"),
        "redirect-alias": client.post(
            "/api/v1/run-public/chat/conversations/",
            follow_redirects=False,
        ),
        "method-not-allowed": client.put("/api/v1/run-public/chat/conversations"),
    }

    assert {name: response.status_code for name, response in responses.items()} == {
        "explicit-error": 409,
        "wrong-content-type": 415,
        "malformed-json": 422,
        "unknown-field": 422,
        "not-found": 404,
        "redirect-alias": 307,
        "method-not-allowed": 405,
    }
    for name, response in responses.items():
        assert response.headers["cache-control"] == "no-store", name
        assert response.headers["referrer-policy"] == "no-referrer", name
        assert "access-control-allow-origin" not in response.headers, name
        assert "access-control-allow-credentials" not in response.headers, name
        assert "origin" not in response.headers.get("vary", "").lower(), name


def test_legacy_public_run_route_is_not_changed_by_target_cors_boundary():
    response = _client().post(
        "/api/v1/run-public/chat",
        headers={"Origin": "https://parent.example"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://parent.example"
