from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from apps.gateway.api.deps import (
    get_deployment_runtime_policy,
)
from apps.gateway.api.v1.endpoints import deployment as deployment_endpoint
from apps.gateway.auth.dependencies import get_current_user
from apps.shared.db.models.app import App
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.session import get_db
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
)


class _Query:
    def __init__(self, result):
        self._result = result

    def filter(self, *expressions):
        return self

    def first(self):
        return self._result


class _Db:
    def __init__(self, rows_by_model):
        self._rows_by_model = rows_by_model

    def query(self, model, *columns):
        return _Query(self._rows_by_model.get(model))


@pytest.fixture
def authenticated_run_client(monkeypatch):
    workflow_id = uuid4()
    deployment = WorkflowDeployment(
        id=uuid4(),
        app_id=uuid4(),
        version=1,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid4(),
    )
    app_model = App(
        id=deployment.app_id,
        workflow_id=workflow_id,
        active_deployment_id=deployment.id,
        created_by=uuid4(),
    )
    db = _Db({WorkflowDeployment: deployment, App: app_model})
    current_user = SimpleNamespace(id=uuid4())
    dispatched = []

    monkeypatch.setattr(
        deployment_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: None,
    )

    async def run_authenticated_deployment(**kwargs):
        dispatched.append(kwargs)
        return {"status": "success", "results": {"answer": "ok"}}

    monkeypatch.setattr(
        deployment_endpoint.DeploymentService,
        "run_authenticated_deployment",
        run_authenticated_deployment,
    )

    app = FastAPI()
    app.include_router(
        deployment_endpoint.router,
        prefix="/api/v1/deployments",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://client.example"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_deployment_runtime_policy] = (
        lambda: DEFAULT_DEPLOYMENT_RUNTIME_POLICY
    )
    client = TestClient(app)
    path = f"/api/v1/deployments/{deployment.id}/run"
    return client, path, dispatched


def test_unlisted_origin_preflight_is_rejected_before_dispatch(
    authenticated_run_client,
):
    client, path, dispatched = authenticated_run_client

    response = client.options(
        path,
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    assert dispatched == []


def test_listed_origin_json_preflight_is_allowed(authenticated_run_client):
    client, path, dispatched = authenticated_run_client

    response = client.options(
        path,
        headers={
            "Origin": "https://client.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "https://client.example"
    )
    assert response.headers["access-control-allow-credentials"] == "true"
    assert dispatched == []


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        (None, b'{"inputs": {}}'),
        ("text/plain", b'{"inputs": {}}'),
        ("application/x-www-form-urlencoded", b"inputs=%7B%7D"),
        (
            "multipart/form-data; boundary=boundary",
            b"--boundary\r\nContent-Disposition: form-data; name=inputs\r\n\r\n{}"
            b"\r\n--boundary--\r\n",
        ),
    ],
)
def test_browser_simple_content_types_are_rejected_before_dispatch(
    authenticated_run_client,
    content_type,
    body,
):
    client, path, dispatched = authenticated_run_client
    headers = {"Origin": "https://attacker.example"}
    if content_type is not None:
        headers["Content-Type"] = content_type

    response = client.post(path, content=body, headers=headers)

    assert response.status_code == 415
    assert response.json() == {"detail": "Content-Type must be application/json"}
    assert dispatched == []


def test_json_request_reaches_authenticated_service(authenticated_run_client):
    client, path, dispatched = authenticated_run_client

    response = client.post(
        path,
        json={"inputs": {"question": "내부 문서는?"}},
        headers={"Origin": "https://client.example"},
    )

    assert response.status_code == 200
    assert response.json()["results"] == {"answer": "ok"}
    assert len(dispatched) == 1
