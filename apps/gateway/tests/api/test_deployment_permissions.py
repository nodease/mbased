import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.sql.operators import eq

from apps.gateway.api.deps import get_deployment_runtime_policy
from apps.gateway.api.v1.endpoints import deployment as deployment_endpoint
from apps.shared.db.models.app import App
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    SURFACE_PUBLIC_INFO,
)
from apps.shared.schemas.deployment import (
    AuthenticatedDeploymentRunRequest,
    DeploymentPreflightRequest,
    DeploymentPreflightResponse,
)


class FakeQuery:
    def __init__(self, result):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.result


class FakeDb:
    def __init__(self, app):
        self.app = app

    def query(self, *args, **kwargs):
        return FakeQuery(self.app)


class FakeModelDb:
    def __init__(self, rows_by_model):
        self.rows_by_model = rows_by_model

    def query(self, model, *args, **kwargs):
        return FakeQuery(self.rows_by_model.get(model))


class FilteringQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.expressions = []

    def filter(self, *expressions):
        self.expressions.extend(expressions)
        return self

    def first(self):
        return next(
            (
                row
                for row in self.rows
                if all(
                    self._matches(row, expression) for expression in self.expressions
                )
            ),
            None,
        )

    @staticmethod
    def _matches(row, expression):
        left = getattr(expression, "left", None)
        if left is None or expression.operator is not eq:
            return True
        column = getattr(left, "key", None)
        if not column or not hasattr(row, column):
            return False
        right = expression.right
        expected = right.value if hasattr(right, "value") else right
        return getattr(row, column) == expected


class FilteringModelDb:
    def __init__(self, rows):
        self.rows = list(rows)

    def query(self, model, *args, **kwargs):
        return FilteringQuery(row for row in self.rows if isinstance(row, model))


def test_get_deployments_authorizes_app_workflow_when_app_and_workflow_supplied(
    monkeypatch,
):
    app_workflow_id = uuid.uuid4()
    supplied_workflow_id = uuid.uuid4()
    app = SimpleNamespace(id=uuid.uuid4(), workflow_id=app_workflow_id)
    user = SimpleNamespace(id=uuid.uuid4())
    checked_workflow_ids = []

    def deny(db, current_user, checked_workflow_id, action):
        checked_workflow_ids.append(checked_workflow_id)
        raise HTTPException(status_code=403, detail="Forbidden")

    def fail_list(*args, **kwargs):
        raise AssertionError("deployments should not be listed without app permission")

    monkeypatch.setattr(deployment_endpoint, "ensure_workflow_permission", deny)
    monkeypatch.setattr(
        deployment_endpoint.DeploymentService, "list_deployments", fail_list
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint.get_deployments(
            app_id=str(app.id),
            workflow_id=str(supplied_workflow_id),
            db=FakeDb(app),
            current_user=user,
        )

    assert exc_info.value.status_code == 403
    assert checked_workflow_ids == [app_workflow_id]


def test_get_deployments_rejects_app_workflow_mismatch_after_authorization(
    monkeypatch,
):
    app_workflow_id = uuid.uuid4()
    supplied_workflow_id = uuid.uuid4()
    app = SimpleNamespace(id=uuid.uuid4(), workflow_id=app_workflow_id)
    user = SimpleNamespace(id=uuid.uuid4())

    def allow(db, current_user, checked_workflow_id, action):
        assert checked_workflow_id == app_workflow_id
        assert action == "read"

    def fail_list(*args, **kwargs):
        raise AssertionError("mismatched ids should not reach the service")

    monkeypatch.setattr(deployment_endpoint, "ensure_workflow_permission", allow)
    monkeypatch.setattr(
        deployment_endpoint.DeploymentService, "list_deployments", fail_list
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint.get_deployments(
            app_id=str(app.id),
            workflow_id=str(supplied_workflow_id),
            db=FakeDb(app),
            current_user=user,
        )

    assert exc_info.value.status_code == 400


def test_get_deployments_accepts_equivalent_workflow_uuid_text(monkeypatch):
    app_workflow_id = uuid.uuid4()
    app = SimpleNamespace(id=uuid.uuid4(), workflow_id=app_workflow_id)
    user = SimpleNamespace(id=uuid.uuid4())

    def allow(db, current_user, checked_workflow_id, action):
        assert checked_workflow_id == app_workflow_id
        assert action == "read"

    def list_deployments(*args, **kwargs):
        assert kwargs["app_id"] == str(app.id)
        assert kwargs["workflow_id"] == str(app_workflow_id).upper()
        return ["deployment"]

    monkeypatch.setattr(deployment_endpoint, "ensure_workflow_permission", allow)
    monkeypatch.setattr(
        deployment_endpoint.DeploymentService, "list_deployments", list_deployments
    )

    result = deployment_endpoint.get_deployments(
        app_id=str(app.id),
        workflow_id=str(app_workflow_id).upper(),
        db=FakeDb(app),
        current_user=user,
    )

    assert result == ["deployment"]


def test_toggle_deployment_passes_strict_public_conversation_contract(
    monkeypatch,
):
    deployment_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4())
    app = SimpleNamespace(id=uuid.uuid4())
    deployment = SimpleNamespace(id=deployment_id)
    captured = {}
    scheduler = object()

    monkeypatch.setattr(
        deployment_endpoint,
        "_deployment_app_and_workflow_id",
        lambda *_args, **_kwargs: (deployment, app, workflow_id),
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "ensure_workflow_permission",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "_deployment_toggle_audit_action",
        lambda *_args, **_kwargs: "deployment.toggle",
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "_deployment_audit_actor",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(deployment_endpoint, "set_current_actor", lambda _actor: "t")
    monkeypatch.setattr(deployment_endpoint, "clear_current_actor", lambda _token: None)
    monkeypatch.setattr(
        deployment_endpoint,
        "_record_deployment_toggle_audit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.gateway.services.scheduler_service.get_scheduler_service",
        lambda: scheduler,
    )
    monkeypatch.setattr(
        deployment_endpoint.settings,
        "PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE",
        "strict",
    )

    def toggle(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return deployment

    monkeypatch.setattr(
        deployment_endpoint.DeploymentService,
        "toggle_deployment",
        Mock(side_effect=toggle),
    )

    result = deployment_endpoint.toggle_deployment(
        str(deployment_id),
        request=SimpleNamespace(),
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        db=object(),
        current_user=current_user,
    )

    assert result is deployment
    assert captured["args"][2] is scheduler
    assert captured["kwargs"]["require_public_chat_conversation_contract"] is True


def test_preview_deployment_preflight_authorizes_deploy_and_returns_result(monkeypatch):
    workflow_id = uuid.uuid4()
    app = SimpleNamespace(id=uuid.uuid4(), workflow_id=workflow_id)
    current_user = SimpleNamespace(id=uuid.uuid4())
    graph = {"nodes": [], "edges": []}
    checked = []
    captured = {}

    def allow(db, user, checked_workflow_id, action):
        checked.append((user.id, checked_workflow_id, action))

    def resolve_graph(db, checked_workflow_id, graph_snapshot):
        captured["resolve"] = (checked_workflow_id, graph_snapshot)
        return graph

    def preview(db, **kwargs):
        captured["preview"] = kwargs
        return DeploymentPreflightResponse(
            status="passed",
            audience="anonymous_public",
        )

    monkeypatch.setattr(deployment_endpoint, "ensure_workflow_permission", allow)
    monkeypatch.setattr(
        deployment_endpoint.DeploymentService,
        "_resolve_graph_snapshot",
        resolve_graph,
    )
    monkeypatch.setattr(
        deployment_endpoint.DeploymentService,
        "preview_knowledge_preflight",
        preview,
    )

    result = deployment_endpoint.preview_deployment_preflight(
        DeploymentPreflightRequest(
            app_id=app.id,
            type="chatbot",
            config={},
            is_active=True,
            graph_snapshot=graph,
            audience="anonymous_public",
        ),
        request=SimpleNamespace(
            state=SimpleNamespace(request_id="request-1"),
            headers={},
        ),
        db=FakeModelDb({App: app}),
        current_user=current_user,
    )

    assert result.status == "passed"
    assert result.normalized_browser_access_policy is not None
    assert result.normalized_browser_access_policy.model_dump() == {
        "contract_version": "deployment_browser_access.v1",
        "embedding": {"enabled": False, "parent_origins": []},
    }
    assert checked == [(current_user.id, workflow_id, "deploy")]
    assert captured["resolve"] == (workflow_id, graph)
    assert captured["preview"]["app"] == app
    assert captured["preview"]["deployment_type"].value == "chatbot"
    assert captured["preview"]["graph_snapshot"] == graph
    assert captured["preview"]["audience_hint"] == "anonymous_public"
    assert captured["preview"]["is_active"] is True


@pytest.mark.parametrize(
    ("code", "status_code", "message", "details"),
    [
        (
            "app.auth_secret_lifecycle_unavailable",
            503,
            "App authentication secret lifecycle is unavailable.",
            {},
        ),
        (
            "deployment.app_auth_secret_required",
            409,
            "Issue an App authentication secret before activation.",
            {"required_actions": ["issue_app_auth_secret"]},
        ),
    ],
)
def test_auth_secret_preflight_error_uses_standard_error_envelope(
    code,
    status_code,
    message,
    details,
):
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="request-1"),
        headers={},
    )
    error = deployment_endpoint.DeploymentAuthSecretPreflightError(
        code=code,
        message=message,
        details=details,
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint._raise_deployment_auth_secret_preflight_error(
            request,
            error,
        )

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == {
        "error": {
            "code": code,
            "message": message,
            "request_id": "request-1",
            "details": details,
        }
    }


def test_unknown_auth_secret_preflight_error_fails_closed():
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="request-1"),
        headers={},
    )
    error = deployment_endpoint.DeploymentAuthSecretPreflightError(
        code="deployment.unknown_auth_secret_error",
        message="Untrusted detail.",
        details={"raw": "must-not-leak"},
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint._raise_deployment_auth_secret_preflight_error(
            request,
            error,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {
        "error": {
            "code": "deployment.auth_secret_preflight_failed",
            "message": "App authentication secret preflight failed.",
            "request_id": "request-1",
            "details": {},
        }
    }


def test_authenticated_run_routes_are_registered_before_deployment_detail():
    paths = [getattr(route, "path", "") for route in deployment_endpoint.router.routes]

    assert paths.index("/{deployment_id}/run-info") < paths.index("/{deployment_id}")
    assert paths.index("/{deployment_id}/run") < paths.index("/{deployment_id}")


def test_public_deployment_info_rejects_workflow_node_deployment():
    app = App(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        url_slug="module-info",
        active_deployment_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
    )
    deployment = WorkflowDeployment(
        id=app.active_deployment_id,
        app_id=app.id,
        version=1,
        type=DeploymentType.WORKFLOW_NODE,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid.uuid4(),
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint.get_deployment_info_public(
            app.url_slug,
            SimpleNamespace(headers={}),
            DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            db=FakeModelDb({App: app, WorkflowDeployment: deployment}),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Active deployment not found"


def test_public_deployment_info_rejects_cross_app_active_deployment_pointer():
    app = App(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        url_slug="cross-app-info",
        active_deployment_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
    )
    deployment = WorkflowDeployment(
        id=app.active_deployment_id,
        app_id=uuid.uuid4(),
        version=1,
        type=DeploymentType.CHATBOT,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid.uuid4(),
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint.get_deployment_info_public(
            app.url_slug,
            SimpleNamespace(headers={}),
            DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            db=FilteringModelDb([app, deployment]),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Active deployment not found"


@pytest.mark.parametrize(
    "deployment_type",
    [
        DeploymentType.API,
        DeploymentType.MCP,
        DeploymentType.SCHEDULE,
        DeploymentType.WEBHOOK,
    ],
)
def test_public_deployment_info_rejects_non_public_metadata_types(deployment_type):
    app = App(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        url_slug=f"private-info-{deployment_type.value}",
        active_deployment_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
    )
    deployment = WorkflowDeployment(
        id=app.active_deployment_id,
        app_id=app.id,
        version=1,
        type=deployment_type,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid.uuid4(),
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint.get_deployment_info_public(
            app.url_slug,
            SimpleNamespace(headers={}),
            DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            db=FilteringModelDb([app, deployment]),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Active deployment not found"


def test_public_deployment_info_uses_injected_runtime_policy():
    app = App(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        name="Injected policy app",
        url_slug="injected-policy-info",
        active_deployment_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
    )
    deployment = WorkflowDeployment(
        id=app.active_deployment_id,
        app_id=app.id,
        version=1,
        type=DeploymentType.API,
        graph_snapshot={"nodes": [], "edges": []},
        input_schema={},
        output_schema={},
        is_active=True,
        created_by=uuid.uuid4(),
    )
    injected_policy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY.with_surface_allowed_types(
        SURFACE_PUBLIC_INFO,
        {DeploymentType.API},
    )

    result = deployment_endpoint.get_deployment_info_public(
        app.url_slug,
        SimpleNamespace(headers={}),
        injected_policy,
        db=FilteringModelDb([app, deployment]),
    )

    assert result.type == DeploymentType.API.value


def test_public_deployment_info_policy_is_replaceable_at_fastapi_composition_boundary():
    app = App(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        name="Injected HTTP policy app",
        url_slug="injected-http-policy-info",
        active_deployment_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
    )
    deployment = WorkflowDeployment(
        id=app.active_deployment_id,
        app_id=app.id,
        version=1,
        type=DeploymentType.API,
        graph_snapshot={"nodes": [], "edges": []},
        input_schema={},
        output_schema={},
        is_active=True,
        created_by=uuid.uuid4(),
    )
    injected_policy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY.with_surface_allowed_types(
        SURFACE_PUBLIC_INFO,
        {DeploymentType.API},
    )
    test_app = FastAPI()
    test_app.include_router(deployment_endpoint.router, prefix="/deployments")
    test_app.dependency_overrides[deployment_endpoint.get_db] = lambda: FilteringModelDb(
        [app, deployment]
    )

    test_app.dependency_overrides[get_deployment_runtime_policy] = lambda: injected_policy

    response = TestClient(test_app).get(
        f"/deployments/public/{app.url_slug}/info"
    )

    assert response.status_code == 200
    assert response.json()["type"] == DeploymentType.API.value
    assert "access-control-allow-origin" not in response.headers
    assert DEFAULT_DEPLOYMENT_RUNTIME_POLICY.allowed_types_by_surface[
        SURFACE_PUBLIC_INFO
    ] == {"webapp", "widget", "chatbot"}


def test_run_authenticated_deployment_authorizes_execute_and_forwards_inputs(
    monkeypatch,
):
    workflow_id = uuid.uuid4()
    deployment = WorkflowDeployment(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        version=1,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid.uuid4(),
    )
    app = App(
        id=deployment.app_id,
        workflow_id=workflow_id,
        active_deployment_id=deployment.id,
        created_by=uuid.uuid4(),
    )
    current_user = SimpleNamespace(id=uuid.uuid4())
    client_conversation_id = uuid.uuid4()
    checked = []
    captured = {}

    def allow(db, user, checked_workflow_id, action):
        checked.append((user.id, checked_workflow_id, action))

    async def run_service(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "results": {"answer": "ok"}}

    monkeypatch.setattr(deployment_endpoint, "ensure_workflow_permission", allow)
    monkeypatch.setattr(
        deployment_endpoint.DeploymentService,
        "run_authenticated_deployment",
        run_service,
    )

    result = asyncio.run(
        deployment_endpoint.run_authenticated_deployment(
            deployment_id=str(deployment.id),
            request=SimpleNamespace(headers={"x-request-id": "req-1"}),
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            request_body=AuthenticatedDeploymentRunRequest(
                inputs={"question": "개발팀 커밋 컨벤션은?"},
                conversation={"client_id": client_conversation_id},
            ),
            db=FakeModelDb({WorkflowDeployment: deployment, App: app}),
            current_user=current_user,
        )
    )

    assert result["status"] == "success"
    assert checked == [(current_user.id, workflow_id, "execute")]
    assert captured["deployment_id"] == str(deployment.id)
    assert captured["user_inputs"] == {"question": "개발팀 커밋 컨벤션은?"}
    assert captured["client_conversation_id"] == str(client_conversation_id)
    assert captured["current_user_id"] == current_user.id
    assert captured["request_id"] == "req-1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user_id", str(uuid.uuid4())),
        ("organization_id", str(uuid.uuid4())),
        (
            "execution_subject",
            {"type": "user", "id": str(uuid.uuid4())},
        ),
    ],
)
def test_authenticated_run_rejects_top_level_execution_context_override(
    monkeypatch,
    field,
    value,
):
    current_user = SimpleNamespace(id=uuid.uuid4())
    test_app = FastAPI()
    test_app.include_router(deployment_endpoint.router, prefix="/deployments")
    test_app.dependency_overrides[deployment_endpoint.get_db] = lambda: object()
    test_app.dependency_overrides[deployment_endpoint.get_current_user] = (
        lambda: current_user
    )
    test_app.dependency_overrides[get_deployment_runtime_policy] = (
        lambda: DEFAULT_DEPLOYMENT_RUNTIME_POLICY
    )

    async def fail_run_service(**_kwargs):
        raise AssertionError("invalid request must not reach deployment execution")

    monkeypatch.setattr(
        deployment_endpoint.DeploymentService,
        "run_authenticated_deployment",
        fail_run_service,
    )

    response = TestClient(test_app).post(
        f"/deployments/{uuid.uuid4()}/run",
        json={"inputs": {"question": "safe"}, field: value},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_run_authenticated_deployment_forwards_middleware_request_id(
    monkeypatch,
):
    workflow_id = uuid.uuid4()
    deployment = WorkflowDeployment(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        version=1,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid.uuid4(),
    )
    app = App(
        id=deployment.app_id,
        workflow_id=workflow_id,
        active_deployment_id=deployment.id,
        created_by=uuid.uuid4(),
    )
    current_user = SimpleNamespace(id=uuid.uuid4())
    captured = {}

    monkeypatch.setattr(
        deployment_endpoint,
        "ensure_workflow_permission",
        lambda db, user, checked_workflow_id, action: None,
    )

    async def run_service(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "results": {"answer": "ok"}}

    monkeypatch.setattr(
        deployment_endpoint.DeploymentService,
        "run_authenticated_deployment",
        run_service,
    )

    asyncio.run(
        deployment_endpoint.run_authenticated_deployment(
            deployment_id=str(deployment.id),
            request=SimpleNamespace(
                headers={},
                state=SimpleNamespace(request_id="middleware-req-1"),
            ),
            runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
            request_body=AuthenticatedDeploymentRunRequest(
                inputs={"question": "개발팀 커밋 컨벤션은?"}
            ),
            db=FakeModelDb({WorkflowDeployment: deployment, App: app}),
            current_user=current_user,
        )
    )

    assert captured["request_id"] == "middleware-req-1"


def test_get_authenticated_deployment_run_info_authorizes_execute(monkeypatch):
    workflow_id = uuid.uuid4()
    deployment = WorkflowDeployment(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        version=1,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid.uuid4(),
    )
    app = App(
        id=deployment.app_id,
        workflow_id=workflow_id,
        organization_id=uuid.uuid4(),
        active_deployment_id=deployment.id,
        created_by=uuid.uuid4(),
    )
    current_user = SimpleNamespace(id=uuid.uuid4())
    checked = []

    def allow(db, user, checked_workflow_id, action):
        checked.append((user.id, checked_workflow_id, action))

    monkeypatch.setattr(deployment_endpoint, "ensure_workflow_permission", allow)
    monkeypatch.setattr(
        deployment_endpoint.DeploymentService,
        "get_deployment_run_info",
        lambda db, deployment_id, **_kwargs: {
            "deployment_id": deployment_id,
            "name": "safe run info",
            "input_schema": {"variables": []},
        },
    )

    result = deployment_endpoint.get_authenticated_deployment_run_info(
        deployment_id=str(deployment.id),
        request=SimpleNamespace(headers={}),
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        db=FakeModelDb({WorkflowDeployment: deployment, App: app}),
        current_user=current_user,
    )

    assert result["name"] == "safe run info"
    assert checked == [(current_user.id, workflow_id, "execute")]


def test_run_authenticated_deployment_rejects_non_object_inputs(monkeypatch):
    workflow_id = uuid.uuid4()
    deployment = WorkflowDeployment(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        version=1,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid.uuid4(),
    )
    app = App(
        id=deployment.app_id,
        workflow_id=workflow_id,
        active_deployment_id=deployment.id,
        created_by=uuid.uuid4(),
    )

    monkeypatch.setattr(
        deployment_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            deployment_endpoint.run_authenticated_deployment(
                deployment_id=str(deployment.id),
                request=SimpleNamespace(headers={}),
                runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
                request_body=AuthenticatedDeploymentRunRequest(
                    inputs="not-an-object"
                ),
                db=FakeModelDb({WorkflowDeployment: deployment, App: app}),
                current_user=SimpleNamespace(id=uuid.uuid4()),
            )
        )

    assert exc_info.value.status_code == 400


def test_run_authenticated_deployment_masks_active_organization_mismatch(
    monkeypatch,
):
    workflow_id = uuid.uuid4()
    active_organization_id = uuid.uuid4()
    app_organization_id = uuid.uuid4()
    deployment = WorkflowDeployment(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        version=1,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid.uuid4(),
    )
    app = App(
        id=deployment.app_id,
        workflow_id=workflow_id,
        organization_id=app_organization_id,
        active_deployment_id=deployment.id,
        created_by=uuid.uuid4(),
    )

    monkeypatch.setattr(
        deployment_endpoint,
        "resolve_active_organization_id",
        lambda *args, **kwargs: active_organization_id,
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("permission check should not run after org mismatch")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            deployment_endpoint.run_authenticated_deployment(
                deployment_id=str(deployment.id),
                request=SimpleNamespace(headers={}),
                runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
                request_body=AuthenticatedDeploymentRunRequest(),
                x_organization_id=str(active_organization_id),
                db=FakeModelDb({WorkflowDeployment: deployment, App: app}),
                current_user=SimpleNamespace(id=uuid.uuid4()),
            )
        )

    assert exc_info.value.status_code == 404


def test_toggle_audit_action_marks_previous_activation():
    deployment_id = uuid.uuid4()
    deployment = SimpleNamespace(id=deployment_id, is_active=False)
    app = SimpleNamespace(active_deployment_id=uuid.uuid4())

    assert (
        deployment_endpoint._deployment_toggle_audit_action(deployment, app)
        == deployment_endpoint.AuditAction.DEPLOYMENT_ACTIVATE_PREVIOUS
    )


def test_toggle_audit_action_keeps_regular_toggle_for_deactivation():
    deployment_id = uuid.uuid4()
    deployment = SimpleNamespace(id=deployment_id, is_active=True)
    app = SimpleNamespace(active_deployment_id=deployment_id)

    assert (
        deployment_endpoint._deployment_toggle_audit_action(deployment, app)
        == deployment_endpoint.AuditAction.DEPLOYMENT_TOGGLE
    )
