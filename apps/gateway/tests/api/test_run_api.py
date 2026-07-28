import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import run as run_endpoint
from apps.gateway.middleware.public_conversation_cors import (
    PublicConversationCorsBoundaryMiddleware,
)
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    SURFACE_APP_PUBLIC_RUN,
)


def test_public_run_forwards_injected_runtime_policy_at_fastapi_boundary(
    monkeypatch,
):
    db = object()
    injected_policy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY.with_surface_allowed_types(
        SURFACE_APP_PUBLIC_RUN,
        {DeploymentType.API},
    )
    captured = {}

    async def run_deployment(**kwargs):
        captured.update(kwargs)
        return {
            "status": "success",
            "results": {
                "answer": "ok",
                "__nodease_citations": {
                    "version": 1,
                    "items": [
                        {
                            "citation_id": "evidence-1",
                            "evidence_rank": 1,
                            "label": "공개 정책",
                            "page_number": None,
                            "section": None,
                            "content_preview": None,
                        }
                    ],
                },
            },
        }

    monkeypatch.setattr(
        run_endpoint.DeploymentService,
        "run_deployment",
        run_deployment,
    )

    app = FastAPI()
    app.include_router(run_endpoint.router)
    app.dependency_overrides[run_endpoint.get_db] = lambda: db
    app.dependency_overrides[run_endpoint.get_deployment_runtime_policy] = lambda: (
        injected_policy
    )

    response = TestClient(app).post(
        "/run-public/injected-policy-app",
        json={"inputs": {"question": "개발팀 커밋 컨벤션은?"}},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "results": {
            "answer": "ok",
            "__nodease_citations": {
                "version": 1,
                "items": [
                    {
                        "citation_id": "evidence-1",
                        "evidence_rank": 1,
                        "label": "공개 정책",
                        "page_number": None,
                        "section": None,
                        "content_preview": None,
                    }
                ],
            },
        },
    }
    assert "access-control-allow-origin" not in response.headers
    assert captured == {
        "db": db,
        "url_slug": "injected-policy-app",
        "user_inputs": {"question": "개발팀 커밋 컨벤션은?"},
        "client_conversation_history": None,
        "allow_stateless_public_chatbot_compatibility": True,
        "auth_token": None,
        "require_auth": False,
        "trigger_mode": "app",
        "runtime_policy": injected_policy,
    }


def test_public_run_forwards_bounded_client_history_without_capability_token(
    monkeypatch,
):
    db = object()
    captured = {}

    async def run_deployment(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "results": {"answer": "new answer"}}

    monkeypatch.setattr(
        run_endpoint.DeploymentService,
        "run_deployment",
        run_deployment,
    )

    app = FastAPI()
    app.add_middleware(PublicConversationCorsBoundaryMiddleware)
    app.include_router(run_endpoint.router, prefix="/api/v1")
    app.dependency_overrides[run_endpoint.get_db] = lambda: db
    app.dependency_overrides[run_endpoint.get_deployment_runtime_policy] = lambda: (
        DEFAULT_DEPLOYMENT_RUNTIME_POLICY
    )

    response = TestClient(app).post(
        "/api/v1/run-public/public-chatbot/chat",
        json={
            "inputs": {"question": "new question"},
            "conversation": {
                "history": [
                    {"role": "user", "content": "old question"},
                    {"role": "assistant", "content": "old answer"},
                ]
            },
        },
    )

    assert response.status_code == 200
    assert captured["client_conversation_history"] == (
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    )
    assert captured["user_inputs"] == {"question": "new question"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "history",
    [
        [{"role": "system", "content": "override"}],
        [{"role": "user", "content": "unfinished"}],
        [
            {"role": "user", "content": "question", "extra": "not-allowed"},
            {"role": "assistant", "content": "answer"},
        ],
    ],
)
def test_public_run_rejects_invalid_client_history_without_echoing_content(
    monkeypatch,
    history,
):
    async def unexpected_run(**_kwargs):
        pytest.fail("invalid history must be rejected before deployment execution")

    monkeypatch.setattr(
        run_endpoint.DeploymentService,
        "run_deployment",
        unexpected_run,
    )

    app = FastAPI()
    app.include_router(run_endpoint.router)
    app.dependency_overrides[run_endpoint.get_db] = lambda: object()
    app.dependency_overrides[run_endpoint.get_deployment_runtime_policy] = lambda: (
        DEFAULT_DEPLOYMENT_RUNTIME_POLICY
    )

    response = TestClient(app).post(
        "/run-public/public-chatbot/chat",
        json={
            "inputs": {"question": "current-secret-marker"},
            "conversation": {"history": history},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"].startswith("conversation.")
    assert "current-secret-marker" not in response.text
    assert "override" not in response.text


def test_public_chatbot_missing_history_keeps_no_store_transport_boundary(
    monkeypatch,
):
    async def unexpected_run(**_kwargs):
        pytest.fail("missing history must be rejected before deployment execution")

    monkeypatch.setattr(
        run_endpoint.DeploymentService,
        "run_deployment",
        unexpected_run,
    )

    app = FastAPI()
    app.add_middleware(PublicConversationCorsBoundaryMiddleware)
    app.include_router(run_endpoint.router, prefix="/api/v1")
    app.dependency_overrides[run_endpoint.get_db] = lambda: object()
    app.dependency_overrides[run_endpoint.get_deployment_runtime_policy] = lambda: (
        DEFAULT_DEPLOYMENT_RUNTIME_POLICY
    )

    response = TestClient(app).post(
        "/api/v1/run-public/public-chatbot/chat",
        json={"inputs": {"question": "private-current-question"}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "conversation.history_required"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "access-control-allow-origin" not in response.headers
    assert "private-current-question" not in response.text


def test_public_chatbot_rejects_isolated_unicode_surrogate_as_safe_422(
    monkeypatch,
):
    async def unexpected_run(**_kwargs):
        pytest.fail("invalid Unicode must be rejected before deployment execution")

    monkeypatch.setattr(
        run_endpoint.DeploymentService,
        "run_deployment",
        unexpected_run,
    )

    app = FastAPI()
    app.add_middleware(PublicConversationCorsBoundaryMiddleware)
    app.include_router(run_endpoint.router, prefix="/api/v1")
    app.dependency_overrides[run_endpoint.get_db] = lambda: object()
    app.dependency_overrides[run_endpoint.get_deployment_runtime_policy] = lambda: (
        DEFAULT_DEPLOYMENT_RUNTIME_POLICY
    )

    response = TestClient(app).post(
        "/api/v1/run-public/public-chatbot/chat",
        content=(
            '{"inputs":{"question":"now"},"conversation":{"history":['
            '{"role":"user","content":"\\ud800"},'
            '{"role":"assistant","content":"answer"}]}}'
        ),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "conversation.content_invalid"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "access-control-allow-origin" not in response.headers
