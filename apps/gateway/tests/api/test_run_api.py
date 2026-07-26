from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import run as run_endpoint
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
    monkeypatch.setattr(
        run_endpoint,
        "_public_conversation_runtime_required",
        lambda _slug: False,
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
        "auth_token": None,
        "require_auth": False,
        "trigger_mode": "app",
        "runtime_policy": injected_policy,
    }
