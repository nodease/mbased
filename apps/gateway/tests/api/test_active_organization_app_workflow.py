import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.sql.operators import eq
from sqlalchemy.sql.operators import is_ as is_operator

from apps.gateway.api.v1.endpoints import app as app_endpoint
from apps.gateway.api.v1.endpoints import workflow as workflow_endpoint
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.gateway.services import app_service
from apps.gateway.services import workflow_service
from apps.gateway.services.app_service import AppService
from apps.gateway.services.workflow_service import WorkflowService
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    canonical_graph_hash,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MEMBER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.team import (
    TeamMembership,
    TeamWorkflowPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import WorkflowRun
from apps.shared.db.session import get_db
from apps.shared.schemas.workflow import WorkflowNodeSecretWriteRequest


def test_store_workflow_node_secret_uses_active_organization_header(monkeypatch):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    workflow = SimpleNamespace(id=workflow_id, organization_id=organization_id)
    captured = {}

    monkeypatch.setattr(
        workflow_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, user_id: organization_id,
    )
    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda db, current_user, requested_workflow_id, action: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "store_node_secret",
        lambda db, **kwargs: captured.update(kwargs)
        or {"secret_reference": f"workflow-node-secret://{uuid.uuid4()}", "configured": True},
    )

    result = workflow_endpoint.store_workflow_node_secret(
        workflow_id=str(workflow_id),
        payload=WorkflowNodeSecretWriteRequest(
            node_id="slack-1",
            node_type="slackPostNode",
            parameter_key="bot_token",
            secret_value="synthetic-input-value",
        ),
        request=object(),
        x_organization_id=str(organization_id),
        db=object(),
        current_user=user,
    )

    assert result["configured"] is True
    assert captured["workflow_id"] == str(workflow_id)
    assert captured["active_organization_id"] == organization_id
    assert captured["user_id"] == user.id


def test_sync_draft_rejects_workflow_outside_active_organization(monkeypatch):
    active_organization_id = uuid.uuid4()
    workflow_organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=workflow_organization_id,
    )
    captured = {"saved": False}

    monkeypatch.setattr(
        workflow_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, user_id: active_organization_id,
    )
    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda db, current_user, requested_workflow_id, action: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "save_draft",
        lambda *args, **kwargs: captured.update(saved=True),
    )

    with pytest.raises(HTTPException) as exc_info:
        workflow_endpoint.sync_draft_workflow(
            workflow_id=str(workflow_id),
            request=object(),
            payload=object(),
            x_organization_id=str(active_organization_id),
            db=object(),
            current_user=user,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Workflow not found"
    assert captured["saved"] is False


def test_create_app_uses_active_organization_header(monkeypatch):
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    payload = object()
    captured = {}

    monkeypatch.setattr(
        app_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, user_id: organization_id,
    )
    # 이 테스트의 관심사는 조직 header 전파다. App 생성 권한 판정(ADR-0016)은
    # 전용 테스트에서 검증하므로 여기서는 허용으로 고정한다.
    monkeypatch.setattr(
        app_endpoint.shared_permissions,
        "has_app_creation_permission",
        lambda db, user_id, org_id: True,
    )
    monkeypatch.setattr(
        app_endpoint.AppService,
        "create_app",
        lambda db, request, user_id, organization_id=None: captured.update(
            {
                "request": request,
                "user_id": user_id,
                "organization_id": organization_id,
            }
        )
        or "created",
    )

    result = app_endpoint.create_app(
        request=object(),
        payload=payload,
        x_organization_id=str(organization_id),
        db=object(),
        current_user=user,
    )

    assert result == "created"
    assert captured == {
        "request": payload,
        "user_id": user.id,
        "organization_id": organization_id,
    }


def test_clone_app_uses_active_organization_header(monkeypatch):
    organization_id = uuid.uuid4()
    source_app_id = str(uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())
    captured = {}

    monkeypatch.setattr(
        app_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, user_id: organization_id,
    )
    monkeypatch.setattr(
        app_endpoint,
        "_get_app_or_404",
        lambda db, app_id: SimpleNamespace(id=app_id),
    )
    monkeypatch.setattr(
        app_endpoint.AppService,
        "access_denial_status",
        lambda db, app, user_id, action: None,
    )
    monkeypatch.setattr(
        app_endpoint.AppService,
        "clone_app",
        lambda db, user_id, source_app_id, organization_id=None: captured.update(
            {
                "user_id": user_id,
                "source_app_id": source_app_id,
                "organization_id": organization_id,
            }
        )
        or "cloned",
    )

    result = app_endpoint.clone_app(
        app_id=source_app_id,
        request=object(),
        x_organization_id=str(organization_id),
        db=object(),
        current_user=user,
    )

    assert result == "cloned"
    assert captured == {
        "user_id": user.id,
        "source_app_id": source_app_id,
        "organization_id": organization_id,
    }


def test_list_apps_uses_active_organization_header(monkeypatch):
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    captured = {}

    monkeypatch.setattr(
        app_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, user_id: organization_id,
    )
    monkeypatch.setattr(
        app_endpoint.AppService,
        "get_user_apps",
        lambda db, user_id, organization_id=None: captured.update(
            {
                "user_id": user_id,
                "organization_id": organization_id,
            }
        )
        or ["app"],
    )

    result = app_endpoint.list_apps(
        request=object(),
        x_organization_id=str(organization_id),
        db=object(),
        current_user=user,
    )

    assert result == ["app"]
    assert captured == {
        "user_id": user.id,
        "organization_id": organization_id,
    }


def test_list_app_operations_uses_active_organization_header(monkeypatch):
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    captured = {}

    monkeypatch.setattr(
        app_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, user_id: organization_id,
    )
    monkeypatch.setattr(
        app_endpoint.AppService,
        "list_app_operations",
        lambda db, **kwargs: captured.update(kwargs) or ["operation"],
    )

    result = app_endpoint.list_app_operations(
        request=object(),
        q="문의",
        permission="builder",
        capability="write",
        deployment_state="active",
        run_state="success",
        limit=20,
        offset=5,
        x_organization_id=str(organization_id),
        db=object(),
        current_user=user,
    )

    assert result == ["operation"]
    assert captured == {
        "user_id": user.id,
        "organization_id": organization_id,
        "q": "문의",
        "permission": "builder",
        "capability": "write",
        "deployment_state": "active",
        "run_state": "success",
        "limit": 20,
        "offset": 5,
    }


def test_get_app_operations_cost_summary_uses_active_organization_header(monkeypatch):
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    captured = {}

    monkeypatch.setattr(
        app_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, user_id: organization_id,
    )
    monkeypatch.setattr(
        app_endpoint.AppService,
        "get_app_operations_cost_summary",
        lambda db, **kwargs: captured.update(kwargs) or {"active_workflow_count": 0},
    )

    result = app_endpoint.get_app_operations_cost_summary(
        request=object(),
        x_organization_id=str(organization_id),
        db=object(),
        current_user=user,
    )

    assert result == {"active_workflow_count": 0}
    assert captured == {
        "user_id": user.id,
        "organization_id": organization_id,
    }


def test_create_workflow_uses_active_organization_header(monkeypatch):
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    payload = object()
    captured = {}

    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        created_at=SimpleNamespace(isoformat=lambda: "created"),
        updated_at=SimpleNamespace(isoformat=lambda: "updated"),
    )
    monkeypatch.setattr(
        workflow_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, user_id: organization_id,
    )
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "create_workflow",
        lambda db, request, user_id, organization_id=None: captured.update(
            {
                "request": request,
                "user_id": user_id,
                "organization_id": organization_id,
            }
        )
        or workflow,
    )

    result = workflow_endpoint.create_workflow(
        request=object(),
        payload=payload,
        x_organization_id=str(organization_id),
        db=object(),
        current_user=user,
    )

    assert result["id"] == str(workflow.id)
    assert captured == {
        "request": payload,
        "user_id": user.id,
        "organization_id": organization_id,
    }


class _FakeQuery:
    def __init__(self, value):
        self.value = value
        self.filters = []
        self.offset_value = 0
        self.limit_value = None

    def filter(self, *args, **kwargs):
        self.filters.extend(args)
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def options(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def offset(self, value):
        self.offset_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def first(self):
        return self.value

    def all(self):
        if self.limit_value is None:
            return self.value[self.offset_value :]
        return self.value[self.offset_value : self.offset_value + self.limit_value]


class _FakeDb:
    def __init__(self, value):
        self.value = value
        self.query_obj = _FakeQuery(value)
        self.rollback_count = 0

    def query(self, *args, **kwargs):
        return self.query_obj

    def rollback(self):
        self.rollback_count += 1


def test_get_user_apps_filters_by_active_organization_before_permission_filter(monkeypatch):
    organization_id = uuid.uuid4()
    app = SimpleNamespace(
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        active_deployment_id=None,
        is_market=False,
    )
    db = _FakeDb([app])

    monkeypatch.setattr(
        app_service, "has_organization_manager_permission", lambda *a: True
    )
    monkeypatch.setattr(AppService, "_populate_owner_name", lambda *a: None)
    monkeypatch.setattr(AppService, "_populate_deployment_status", lambda *a: None)

    apps = AppService.get_user_apps(db, uuid.uuid4(), organization_id=organization_id)

    assert apps == [app]
    assert any(
        str(getattr(expression, "left", "")) == "apps.organization_id"
        and expression.operator is eq
        and expression.right.value == organization_id
        for expression in db.query_obj.filters
    )


def test_get_user_apps_attaches_member_budget_status_from_grouped_lookup(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user_id = uuid.uuid4()
    app = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        workflow_id=workflow_id,
        created_by=user_id,
        active_deployment_id=None,
        is_market=False,
    )
    db = _FakeDb([app])
    captured = {}

    monkeypatch.setattr(
        app_service, "has_organization_manager_permission", lambda *a: True
    )
    monkeypatch.setattr(AppService, "_populate_owner_name", lambda *a: None)
    monkeypatch.setattr(AppService, "_populate_deployment_status", lambda *a: None)

    def budget_status_by_workflow_id(db_arg, workflow_ids, *args, **kwargs):
        captured["workflow_ids"] = list(workflow_ids)
        return {workflow_id: {"usage_ratio": 0.9, "status": "at_risk"}}

    monkeypatch.setattr(
        AppService,
        "_budget_status_by_workflow_id",
        budget_status_by_workflow_id,
        raising=False,
    )

    apps = AppService.get_user_apps(db, user_id, organization_id=organization_id)

    assert getattr(apps[0], "budget_status", None) == {
        "usage_ratio": pytest.approx(0.9),
        "status": "at_risk",
    }
    assert captured["workflow_ids"] == [workflow_id]


def test_get_user_apps_returns_null_budget_status_when_lookup_races(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user_id = uuid.uuid4()
    app = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        workflow_id=workflow_id,
        created_by=user_id,
        active_deployment_id=None,
        is_market=False,
    )
    db = _FakeDb([app])

    monkeypatch.setattr(
        app_service, "has_organization_manager_permission", lambda *a: True
    )
    monkeypatch.setattr(AppService, "_populate_owner_name", lambda *a: None)
    monkeypatch.setattr(AppService, "_populate_deployment_status", lambda *a: None)

    def budget_status_by_workflow_id(*args, **kwargs):
        raise RuntimeError("budget row changed while app list was being built")

    monkeypatch.setattr(
        AppService,
        "_budget_status_by_workflow_id",
        budget_status_by_workflow_id,
        raising=False,
    )

    apps = AppService.get_user_apps(db, user_id, organization_id=organization_id)

    assert getattr(apps[0], "budget_status", "missing") is None
    assert db.rollback_count == 1


def test_list_app_operations_returns_safe_summary(monkeypatch):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    deployment_id = uuid.uuid4()
    team_id = uuid.uuid4()
    deployment = SimpleNamespace(
        id=deployment_id,
        app_id=uuid.uuid4(),
        type=SimpleNamespace(value="webhook"),
        is_active=True,
    )
    app = SimpleNamespace(
        id=deployment.app_id,
        organization_id=organization_id,
        name="고객 문의 분류",
        description="문의 분류",
        icon={"type": "emoji", "content": "📨", "background_color": "#E0F2FE"},
        workflow_id=workflow_id,
        active_deployment=deployment,
        active_deployment_id=deployment_id,
        created_by=user_id,
        created_at=now,
        updated_at=now,
        url_slug="app-secret",
        auth_secret="sk-secret",
    )
    db = _FakeDb([app])

    monkeypatch.setattr(AppService, "can_read_app_operations", lambda *a: True)
    monkeypatch.setattr(
        app_service,
        "get_effective_workflow_auth_state",
        lambda *a, **kwargs: "builder",
    )
    monkeypatch.setattr(
        app_service,
        "get_workflow_permission_sources_by_workflow_ids",
        lambda *a, **kwargs: {
            workflow_id: [
                {
                    "type": "team",
                    "team_id": team_id,
                    "team_name": "운영팀",
                    "auth_state": "builder",
                }
            ]
        },
    )
    monkeypatch.setattr(
        AppService, "_owner_names_by_id", lambda *a: {user_id: "혜연"}
    )
    monkeypatch.setattr(AppService, "_latest_runs_by_workflow_id", lambda *a: {})
    monkeypatch.setattr(
        AppService,
        "_deployment_history_by_app_id",
        lambda *a: {app.id: deployment},
    )

    rows = AppService.list_app_operations(
        db,
        user_id=user_id,
        organization_id=organization_id,
    )

    assert len(rows) == 1
    row = rows[0].model_dump()
    assert row["app"]["name"] == "고객 문의 분류"
    assert row["app"]["owner_name"] == "혜연"
    assert "auth_secret" not in row["app"]
    assert "url_slug" not in row["app"]
    assert row["permission"]["auth_state"] == "builder"
    assert row["permission"]["can_write"] is True
    assert row["permission"]["can_deploy"] is False
    assert row["permission_sources"] == [
        {
            "type": "team",
            "auth_state": "builder",
            "team_id": team_id,
            "team_name": "운영팀",
            "user_id": None,
            "user_name": None,
        }
    ]
    assert row["deployment"]["state"] == "active"
    assert row["latest_run"]["state"] == "not_started"


def test_list_app_operations_attaches_member_budget_status_from_grouped_lookup(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    app = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="예산 운영 모듈",
        description="예산 상태 표시",
        icon={"type": "emoji", "content": "B", "background_color": "#E0F2FE"},
        workflow_id=workflow_id,
        active_deployment=None,
        active_deployment_id=None,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db = _FakeDb([app])
    captured = {}

    monkeypatch.setattr(AppService, "can_read_app_operations", lambda *a: True)
    monkeypatch.setattr(
        app_service,
        "get_effective_workflow_auth_state",
        lambda *a, **kwargs: "viewer",
    )
    monkeypatch.setattr(
        app_service,
        "get_workflow_permission_sources_by_workflow_ids",
        lambda *a, **kwargs: {},
    )
    monkeypatch.setattr(
        AppService, "_owner_names_by_id", lambda *a: {user_id: "혜연"}
    )
    monkeypatch.setattr(AppService, "_latest_runs_by_workflow_id", lambda *a: {})
    monkeypatch.setattr(AppService, "_deployment_history_by_app_id", lambda *a: {})

    def budget_status_by_workflow_id(db_arg, workflow_ids, *args, **kwargs):
        captured["workflow_ids"] = list(workflow_ids)
        return {workflow_id: {"usage_ratio": 1.000001, "status": "exceeded"}}

    monkeypatch.setattr(
        AppService,
        "_budget_status_by_workflow_id",
        budget_status_by_workflow_id,
        raising=False,
    )

    rows = AppService.list_app_operations(
        db,
        user_id=user_id,
        organization_id=organization_id,
    )

    row = rows[0].model_dump()
    assert row["app"]["budget_status"] == {
        "usage_ratio": pytest.approx(1.000001),
        "status": "exceeded",
    }
    assert "monthly_budget_usd" not in row["app"]["budget_status"]
    assert "current_month_cost" not in row["app"]["budget_status"]
    assert captured["workflow_ids"] == [workflow_id]


def test_list_app_operations_recovers_optional_projection_lookup_failures(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    app = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="예산 경합 모듈",
        description="예산 조회 경합",
        icon={"type": "emoji", "content": "B", "background_color": "#E0F2FE"},
        workflow_id=workflow_id,
        active_deployment=None,
        active_deployment_id=None,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db = _FakeDb([app])

    monkeypatch.setattr(AppService, "can_read_app_operations", lambda *a: True)
    monkeypatch.setattr(
        app_service,
        "get_effective_workflow_auth_state",
        lambda *a, **kwargs: "viewer",
    )

    def permission_sources_by_workflow_ids(*args, **kwargs):
        assert db.rollback_count == 2
        return {}

    monkeypatch.setattr(
        app_service,
        "get_workflow_permission_sources_by_workflow_ids",
        permission_sources_by_workflow_ids,
    )
    monkeypatch.setattr(
        AppService, "_owner_names_by_id", lambda *a: {user_id: "혜연"}
    )
    monkeypatch.setattr(AppService, "_latest_runs_by_workflow_id", lambda *a: {})
    monkeypatch.setattr(AppService, "_deployment_history_by_app_id", lambda *a: {})

    def budget_status_by_workflow_id(*args, **kwargs):
        raise RuntimeError(
            "budget row changed while operations list was being built"
        )

    monkeypatch.setattr(
        AppService,
        "_budget_status_by_workflow_id",
        budget_status_by_workflow_id,
        raising=False,
    )

    def operation_metrics_by_workflow_id(*args, **kwargs):
        raise RuntimeError(
            "usage row changed while operations list was being built"
        )

    monkeypatch.setattr(
        AppService,
        "_operation_metrics_by_workflow_id",
        operation_metrics_by_workflow_id,
    )

    rows = AppService.list_app_operations(
        db,
        user_id=user_id,
        organization_id=organization_id,
    )

    assert rows[0].model_dump()["app"].get("budget_status", "missing") is None
    assert db.rollback_count == 2


def test_list_app_operations_filters_by_capability(monkeypatch):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    app = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="실행 전용 모듈",
        description="operator 권한",
        icon={"type": "emoji", "content": "N", "background_color": "#E0F2FE"},
        workflow_id=workflow_id,
        active_deployment=None,
        active_deployment_id=None,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db = _FakeDb([app])

    monkeypatch.setattr(AppService, "can_read_app_operations", lambda *a: True)
    monkeypatch.setattr(
        app_service,
        "get_effective_workflow_auth_state",
        lambda *a, **kwargs: "operator",
    )
    monkeypatch.setattr(
        app_service,
        "get_workflow_permission_sources_by_workflow_ids",
        lambda *a, **kwargs: {},
    )
    monkeypatch.setattr(AppService, "_owner_names_by_id", lambda *a: {})
    monkeypatch.setattr(AppService, "_latest_runs_by_workflow_id", lambda *a: {})
    monkeypatch.setattr(AppService, "_deployment_history_by_app_id", lambda *a: {})

    executable_rows = AppService.list_app_operations(
        db,
        user_id=user_id,
        organization_id=organization_id,
        capability="execute",
    )
    writable_rows = AppService.list_app_operations(
        db,
        user_id=user_id,
        organization_id=organization_id,
        capability="write",
    )
    operator_writable_rows = AppService.list_app_operations(
        db,
        user_id=user_id,
        organization_id=organization_id,
        permission="operator",
        capability="write",
    )

    assert [row.app.name for row in executable_rows] == ["실행 전용 모듈"]
    assert writable_rows == []
    assert operator_writable_rows == []


def test_list_app_operations_stops_permission_scan_after_page_is_filled(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    apps = [
        SimpleNamespace(
            id=uuid.uuid4(),
            organization_id=organization_id,
            name=f"모듈 {index}",
            description=None,
            icon=None,
            workflow_id=uuid.uuid4(),
            active_deployment=None,
            active_deployment_id=None,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        for index in range(3)
    ]
    db = _FakeDb(apps)
    checked_app_ids = []

    def can_read(db, app, user_id):
        checked_app_ids.append(app.id)
        return True

    monkeypatch.setattr(AppService, "can_read_app_operations", can_read)
    monkeypatch.setattr(
        app_service,
        "get_effective_workflow_auth_state",
        lambda *a, **kwargs: "viewer",
    )
    monkeypatch.setattr(
        app_service,
        "get_workflow_permission_sources_by_workflow_ids",
        lambda *a, **kwargs: {},
    )
    monkeypatch.setattr(AppService, "_owner_names_by_id", lambda *a: {})
    monkeypatch.setattr(AppService, "_latest_runs_by_workflow_id", lambda *a: {})
    monkeypatch.setattr(AppService, "_deployment_history_by_app_id", lambda *a: {})

    rows = AppService.list_app_operations(
        db,
        user_id=user_id,
        organization_id=organization_id,
        limit=1,
    )

    assert [row.app.name for row in rows] == ["모듈 0"]
    assert checked_app_ids == [apps[0].id]


def test_operation_cost_summary_includes_all_readable_active_workflows(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    apps = [
        SimpleNamespace(
            id=uuid.uuid4(),
            organization_id=organization_id,
            name=f"모듈 {index}",
            description=None,
            icon=None,
            workflow_id=uuid.uuid4(),
            active_deployment=SimpleNamespace(is_active=True),
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        for index in range(101)
    ]
    db = _FakeDb(apps)
    captured = {}
    for operation_app in apps:
        operation_app.active_deployment.app_id = operation_app.id

    monkeypatch.setattr(
        app_service,
        "has_organization_manager_permission",
        lambda *args: True,
    )
    monkeypatch.setattr(
        AppService,
        "_operation_metrics_by_workflow_id",
        lambda _db, workflow_ids, **kwargs: captured.update(
            {"workflow_ids": workflow_ids, "now": kwargs["now"]}
        )
        or {
            workflow_id: {
                "projected_month_cost": 3,
                "projected_month_workflow_execution_cost": 2,
                "projected_month_agent_builder_cost": 1,
                "usage_data_complete": False,
                "unresolved_provider_call_count": 1,
            }
            for workflow_id in workflow_ids
        },
    )

    summary = AppService.get_app_operations_cost_summary(
        db,
        user_id=user_id,
        organization_id=organization_id,
        now=now,
    )

    assert len(captured["workflow_ids"]) == 101
    assert summary.active_workflow_count == 101
    assert summary.projected_month_cost == pytest.approx(303)
    assert summary.projected_month_workflow_execution_cost == pytest.approx(202)
    assert summary.projected_month_agent_builder_cost == pytest.approx(101)
    assert summary.usage_data_complete is False
    assert summary.unresolved_provider_call_count == 101


def test_operation_cost_summary_batches_non_manager_permission_sources(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    apps = [
        SimpleNamespace(
            id=uuid.uuid4(),
            organization_id=organization_id,
            name=f"모듈 {index}",
            description=None,
            icon=None,
            workflow_id=uuid.uuid4(),
            active_deployment=SimpleNamespace(is_active=True),
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        for index in range(101)
    ]
    db = _FakeDb(apps)
    captured = {}
    for operation_app in apps:
        operation_app.active_deployment.app_id = operation_app.id

    monkeypatch.setattr(
        app_service,
        "has_organization_manager_permission",
        lambda *args: False,
    )
    monkeypatch.setattr(
        app_service,
        "get_workflow_permission_sources_by_workflow_ids",
        lambda _db, _user_id, workflow_ids, _organization_id: captured.update(
            {"workflow_ids": workflow_ids}
        )
        or {
            workflow_id: [SimpleNamespace(auth_state="builder")]
            for workflow_id in workflow_ids
        },
    )
    monkeypatch.setattr(
        AppService,
        "_operation_metrics_by_workflow_id",
        lambda _db, workflow_ids, **kwargs: {
            workflow_id: {
                "projected_month_cost": 3,
                "projected_month_workflow_execution_cost": 2,
                "projected_month_agent_builder_cost": 1,
            }
            for workflow_id in workflow_ids
        },
    )

    summary = AppService.get_app_operations_cost_summary(
        db,
        user_id=user_id,
        organization_id=organization_id,
        now=now,
    )

    assert len(captured["workflow_ids"]) == 101
    assert summary.active_workflow_count == 101


def test_escape_like_pattern_treats_wildcards_as_literals():
    assert AppService._escape_like_pattern(r"50%_done\test") == r"50\%\_done\\test"


def test_list_app_operations_does_not_expose_market_app_without_workflow_read(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    app = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="공개 앱",
        description="마켓 공개 앱",
        icon={"type": "emoji", "content": "N", "background_color": "#E0F2FE"},
        workflow_id=workflow_id,
        active_deployment=None,
        active_deployment_id=None,
        created_by=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        is_market=True,
    )
    db = _FakeDb([app])

    monkeypatch.setattr(
        app_service,
        "has_organization_manager_permission",
        lambda *args: False,
    )
    monkeypatch.setattr(app_service, "has_workflow_permission", lambda *a, **k: False)
    def latest_runs_for_empty_candidates(db, workflow_ids):
        assert workflow_ids == []
        return {}

    def deployment_history_for_empty_candidates(db, app_ids):
        assert app_ids == []
        return {}

    monkeypatch.setattr(
        AppService,
        "_latest_runs_by_workflow_id",
        latest_runs_for_empty_candidates,
    )
    monkeypatch.setattr(
        AppService,
        "_deployment_history_by_app_id",
        deployment_history_for_empty_candidates,
    )

    rows = AppService.list_app_operations(
        db,
        user_id=user_id,
        organization_id=organization_id,
    )

    assert rows == []


def test_operation_latest_run_summary_does_not_expose_raw_error_message():
    run = SimpleNamespace(
        id=uuid.uuid4(),
        status=SimpleNamespace(value="failed"),
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        error_message="secret token and prompt text",
    )

    summary = AppService._operation_latest_run_summary(run)

    assert summary.state == "failed"
    assert summary.raw_status == "failed"
    assert summary.error_message == "execution_failed"


def test_latest_runs_query_does_not_load_full_workflow_run_entity():
    workflow_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    captured_queries = []

    class LatestRunQuery:
        def __init__(self, rows=None):
            self.rows = rows or []

        def filter(self, *args, **kwargs):
            return self

        def join(self, *args, **kwargs):
            return self

        def subquery(self):
            return SimpleNamespace(c=SimpleNamespace(id=object(), row_number=object()))

        def all(self):
            return self.rows

    class LatestRunDb:
        def query(self, *entities):
            captured_queries.append(entities)
            if len(captured_queries) == 2:
                return LatestRunQuery(
                    [
                        SimpleNamespace(
                            workflow_id=workflow_id,
                            id=run_id,
                            status=SimpleNamespace(value="success"),
                            started_at=now,
                            finished_at=now,
                            error_message=None,
                        )
                    ]
                )
            return LatestRunQuery()

    latest_runs = AppService._latest_runs_by_workflow_id(LatestRunDb(), [workflow_id])

    assert latest_runs[workflow_id].id == run_id
    assert not any(
        entity is WorkflowRun for query in captured_queries for entity in query
    )
    selected_names = {
        getattr(entity, "name", str(entity))
        for query in captured_queries
        for entity in query
    }
    assert "conversation_id" not in selected_names
    assert "chat_session_id" not in selected_names


def test_create_workflow_rejects_active_organization_mismatch(monkeypatch):
    app_organization_id = uuid.uuid4()
    active_organization_id = uuid.uuid4()
    app = SimpleNamespace(id=uuid.uuid4(), organization_id=app_organization_id)

    monkeypatch.setattr(
        workflow_service.AppService,
        "can_manage_app",
        lambda db, app, user_id: True,
    )

    with pytest.raises(HTTPException) as exc_info:
        WorkflowService.create_workflow(
            _FakeDb(app),
            SimpleNamespace(app_id=app.id),
            user_id=uuid.uuid4(),
            organization_id=active_organization_id,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize("manager_field", ["created_by", "managed_by"])
def test_owner_or_manager_without_membership_can_create_and_list_apps_and_workflows(
    monkeypatch, manager_field
):
    _patch_audit(monkeypatch)
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    organization_kwargs = {"created_by": uuid.uuid4(), "managed_by": None}
    organization_kwargs[manager_field] = user_id
    session = _RouteSession(
        organizations=[_route_organization(id=organization_id, **organization_kwargs)],
        users=[_route_user(user_id)],
    )
    _override_route_dependencies(session, user_id)

    try:
        create_response = TestClient(app).post(
            "/api/v1/apps",
            headers={"X-Organization-Id": str(organization_id)},
            json=_app_payload("Manager App"),
        )
        assert create_response.status_code == 200
        app_id = uuid.UUID(create_response.json()["id"])
        primary_workflow_id = uuid.UUID(create_response.json()["workflow_id"])
        assert _has_user_workflow_permission(
            session,
            user_id=user_id,
            workflow_id=primary_workflow_id,
            organization_id=organization_id,
            auth_state="manager",
        )

        list_response = TestClient(app).get(
            "/api/v1/apps",
            headers={"X-Organization-Id": str(organization_id)},
        )
        assert list_response.status_code == 200
        assert [item["id"] for item in list_response.json()] == [str(app_id)]

        workflow_response = TestClient(app).post(
            "/api/v1/workflows",
            headers={"X-Organization-Id": str(organization_id)},
            json={"app_id": str(app_id)},
        )
        assert workflow_response.status_code == 200
        new_workflow_id = uuid.UUID(workflow_response.json()["id"])
        assert _has_user_workflow_permission(
            session,
            user_id=user_id,
            workflow_id=new_workflow_id,
            organization_id=organization_id,
            auth_state="manager",
        )
    finally:
        app.dependency_overrides = {}


def test_active_member_can_manage_app_draft_after_creating_app(monkeypatch):
    _patch_audit(monkeypatch)
    # Knowledge reference authorization is covered by its focused service/API
    # tests; this route fake intentionally models only App/Workflow RBAC rows.
    monkeypatch.setattr(
        WorkflowService,
        "validate_knowledge_references",
        lambda *args, **kwargs: None,
    )
    # App 생성 권한 판정(ADR-0016)은 전용 테스트에서 검증한다. 이 테스트의
    # 관심사는 생성 후 draft manage 권한이므로 생성 능력은 허용으로 고정한다.
    monkeypatch.setattr(
        app_endpoint.shared_permissions,
        "has_app_creation_permission",
        lambda db, user_id, org_id: True,
    )
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    session = _RouteSession(
        organizations=[_route_organization(id=organization_id, created_by=uuid.uuid4())],
        organization_memberships=[
            _route_organization_membership(user_id, organization_id)
        ],
        memberships=[
            _route_membership(user_id=user_id, organization_id=organization_id)
        ],
        users=[_route_user(user_id)],
    )
    _override_route_dependencies(session, user_id)

    try:
        create_response = TestClient(app).post(
            "/api/v1/apps",
            headers={"X-Organization-Id": str(organization_id)},
            json=_app_payload("Member App"),
        )
        assert create_response.status_code == 200
        workflow_id = uuid.UUID(create_response.json()["workflow_id"])
        knowledge_base_id = str(uuid.uuid4())
        llm_node = {
            "id": "llm-1",
            "type": "llmNode",
            "position": {"x": 100, "y": 200},
            "data": {
                "title": "LLM",
                "provider": "openai",
                "model_id": "gpt-4o",
                "user_prompt": "정책을 요약해줘",
                "knowledgeBases": [{"id": knowledge_base_id, "name": "제품 정책"}],
            },
        }

        saved_workflow = next(
            workflow for workflow in session.workflows if workflow.id == workflow_id
        )

        draft_response = TestClient(app).post(
            f"/api/v1/workflows/{workflow_id}/draft",
            headers={"X-Organization-Id": str(organization_id)},
            json={
                "nodes": [llm_node],
                "edges": [],
                "expected_graph_hash": canonical_graph_hash(saved_workflow.graph),
                "expected_updated_at": saved_workflow.updated_at.isoformat(),
            },
        )
        assert draft_response.status_code == 200
        assert draft_response.json()["status"] == "success"
        assert draft_response.json()["workflow_id"] == str(workflow_id)
        assert draft_response.json()["graph_hash"] == canonical_graph_hash(
            saved_workflow.graph
        )
        assert draft_response.json()["updated_at"] == saved_workflow.updated_at.isoformat()

        assert saved_workflow.graph["nodes"][0]["data"]["knowledgeBases"] == [
            {"id": knowledge_base_id, "name": "제품 정책"}
        ]

        get_response = TestClient(app).get(f"/api/v1/workflows/{workflow_id}/draft")
        assert get_response.status_code == 200
        assert get_response.json()["workflow_id"] == str(workflow_id)
        assert get_response.json()["graph_hash"] == canonical_graph_hash(
            saved_workflow.graph
        )
        assert get_response.json()["updated_at"] == saved_workflow.updated_at.isoformat()
        assert get_response.json()["nodes"][0]["data"]["knowledgeBases"] == [
            {"id": knowledge_base_id, "name": "제품 정책"}
        ]
    finally:
        app.dependency_overrides = {}


def test_workflow_direct_permission_without_active_scope_returns_404(monkeypatch):
    _patch_audit(monkeypatch)
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    session = _RouteSession(
        organizations=[_route_organization(id=organization_id, created_by=uuid.uuid4())],
        apps=[
            _route_app(
                id=app_id,
                organization_id=organization_id,
                workflow_id=workflow_id,
                created_by=uuid.uuid4(),
            )
        ],
        workflows=[
            _route_workflow(
                id=workflow_id,
                app_id=app_id,
                organization_id=organization_id,
                created_by=uuid.uuid4(),
            )
        ],
        user_workflow_permissions=[
            _route_user_workflow_permission(
                organization_id=organization_id,
                workflow_id=workflow_id,
                user_id=user_id,
                auth_state="manager",
            )
        ],
    )
    _override_route_dependencies(session, user_id)

    try:
        response = TestClient(app).get(f"/api/v1/workflows/{workflow_id}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Workflow not found"}
    finally:
        app.dependency_overrides = {}


def _patch_audit(monkeypatch):
    monkeypatch.setattr("apps.gateway.utils.audit.record_audit", lambda **kwargs: None)
    monkeypatch.setattr("apps.gateway.main.record_audit", lambda **kwargs: None)
    monkeypatch.setattr(
        "apps.gateway.auth.permissions.record_audit",
        lambda **kwargs: None,
    )


def _override_route_dependencies(session, user_id):
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)


def _app_payload(name):
    return {
        "name": name,
        "description": "test app",
        "icon": {
            "type": "emoji",
            "content": "A",
            "background_color": "#FFFFFF",
        },
        "is_market": False,
    }


def _has_user_workflow_permission(
    session,
    *,
    user_id,
    workflow_id,
    organization_id,
    auth_state,
):
    return any(
        permission.user_id == user_id
        and permission.workflow_id == workflow_id
        and permission.grantee_organization_id == organization_id
        and permission.auth_state == auth_state
        for permission in session.user_workflow_permissions
    )


class _RouteSession:
    def __init__(
        self,
        *,
        organizations=None,
        memberships=None,
        apps=None,
        workflows=None,
        users=None,
        organization_memberships=None,
        team_workflow_permissions=None,
        user_workflow_permissions=None,
    ):
        self.organizations = list(organizations or [])
        self.memberships = list(memberships or [])
        self.apps = list(apps or [])
        self.workflows = list(workflows or [])
        self.users = list(users or [])
        self.organization_memberships = list(organization_memberships or [])
        self.team_workflow_permissions = list(team_workflow_permissions or [])
        self.user_workflow_permissions = list(user_workflow_permissions or [])
        self.added = []
        self.committed = False
        self.refreshed = []

    def query(self, *entities):
        return _RouteQuery(self, entities)

    def add(self, value):
        self.added.append(value)
        if isinstance(value, App):
            self.apps.append(value)
        elif isinstance(value, Workflow):
            self.workflows.append(value)
        elif isinstance(value, UserWorkflowPermission):
            self.user_workflow_permissions.append(value)

    def flush(self):
        now = datetime.now(timezone.utc)
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()
            if (
                hasattr(value, "created_at")
                and getattr(value, "created_at", None) is None
            ):
                value.created_at = now
            if (
                hasattr(value, "updated_at")
                and getattr(value, "updated_at", None) is None
            ):
                value.updated_at = now

    def commit(self):
        self.flush()
        self.committed = True

    def refresh(self, value):
        self.refreshed.append(value)


class _RouteQuery:
    def __init__(self, session, entities):
        self.session = session
        self.entities = entities
        self.model = _model_for_entity(entities[0])
        self.selected_attribute = _selected_attribute(entities[0])
        self.filter_expressions = []

    def join(self, *args, **kwargs):
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def filter(self, *expressions):
        self.filter_expressions.extend(expressions)
        return self

    def options(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        return self

    def first(self):
        values = self.all()
        return values[0] if values else None

    def all(self):
        values = [
            value
            for value in self._items()
            if all(
                _matches_expression(value, expression)
                for expression in self.filter_expressions
            )
        ]
        if self.selected_attribute:
            return [(getattr(value, self.selected_attribute),) for value in values]
        return values

    def _items(self):
        if self.model is Organization:
            return self.session.organizations
        if self.model is TeamMembership:
            return self.session.memberships
        if self.model is App:
            return self.session.apps
        if self.model is Workflow:
            return self.session.workflows
        if self.model is User:
            return self.session.users
        if self.model is OrganizationMembership:
            return self.session.organization_memberships
        if self.model is TeamWorkflowPermission:
            return self.session.team_workflow_permissions
        if self.model is UserWorkflowPermission:
            return self.session.user_workflow_permissions
        raise AssertionError(f"Unexpected query model: {self.model}")


def _model_for_entity(entity):
    return getattr(entity, "class_", entity)


def _selected_attribute(entity):
    if getattr(entity, "class_", None) is None:
        return None
    return getattr(entity, "key", None)


def _matches_expression(value, expression):
    if not hasattr(expression, "left"):
        return True
    left_value = _column_value(value, str(expression.left))

    if expression.operator is eq:
        right = expression.right
        right_value = (
            right.value
            if hasattr(right, "value")
            else _column_value(value, str(right))
        )
        return _same_value(left_value, right_value)
    if expression.operator is is_operator:
        if str(expression.right).lower() == "null":
            return left_value is None
        return left_value is (str(expression.right) == "true")
    raise AssertionError(f"Unexpected filter operator: {expression.operator}")


def _same_value(left, right):
    return left == right or (
        left is not None and right is not None and str(left) == str(right)
    )


def _column_value(value, column):
    missing = object()
    values = {
        "organization.id": getattr(value, "id", missing),
        "organization.is_active": getattr(value, "is_active", missing),
        "organization_memberships.user_id": getattr(value, "user_id", missing),
        "organization_memberships.organization_id": getattr(
            value, "organization_id", missing
        ),
        "organization_memberships.membership_state": getattr(
            value, "membership_state", missing
        ),
        "users.id": getattr(value, "id", missing),
        "users.deactivated_at": getattr(value, "deactivated_at", missing),
        "apps.id": getattr(value, "id", missing),
        "apps.organization_id": getattr(value, "organization_id", missing),
        "apps.name": getattr(value, "name", missing),
        "apps.url_slug": getattr(value, "url_slug", missing),
        "workflows.id": getattr(value, "id", missing),
        "workflows.organization_id": getattr(value, "organization_id", missing),
        "team_memberships.user_id": getattr(value, "user_id", missing),
        "team_memberships.grantee_organization_id": getattr(
            value, "grantee_organization_id", missing
        ),
        "teams.organization_id": getattr(value, "team_organization_id", missing),
        "teams.is_active": getattr(value, "team_is_active", missing),
        "team_workflow_permissions.workflow_id": getattr(value, "workflow_id", missing),
        "team_workflow_permissions.grantee_organization_id": getattr(
            value, "grantee_organization_id", missing
        ),
        "team_workflow_permissions.team_id": getattr(value, "team_id", missing),
        "user_workflow_permissions.user_id": getattr(value, "user_id", missing),
        "user_workflow_permissions.workflow_id": getattr(value, "workflow_id", missing),
        "user_workflow_permissions.grantee_organization_id": getattr(
            value, "grantee_organization_id", missing
        ),
    }
    if column not in values:
        raise AssertionError(f"Unexpected filter column: {column}")
    if values[column] is missing:
        raise AssertionError(f"Missing fixture attribute for filter column: {column}")
    return values[column]


def _route_organization(id, created_by, managed_by=None, is_active=True):
    now = datetime.now(timezone.utc)
    return Organization(
        id=id,
        name="Acme",
        options={},
        created_by=created_by,
        managed_by=managed_by,
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )


def _route_user(id, deactivated_at=None):
    now = datetime.now(timezone.utc)
    return User(
        id=id,
        email=f"{id.hex[:8]}@moduly.local",
        name="Route User",
        social_provider="local",
        deactivated_at=deactivated_at,
        created_at=now,
        updated_at=now,
    )


def _route_organization_membership(
    user_id,
    organization_id,
    organization_auth_state=ORGANIZATION_AUTH_MEMBER,
    membership_state=ORGANIZATION_MEMBERSHIP_ACTIVE,
):
    now = datetime.now(timezone.utc)
    return OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        membership_state=membership_state,
        organization_auth_state=organization_auth_state,
        invited_by=user_id,
        invited_at=now,
        accepted_at=now,
        created_at=now,
        updated_at=now,
        options={},
        flags=0,
    )


def _route_membership(user_id, organization_id, team_is_active=True):
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        grantee_organization_id=organization_id,
        team_organization_id=organization_id,
        team_is_active=team_is_active,
    )


def _route_app(id, organization_id, workflow_id, created_by):
    now = datetime.now(timezone.utc)
    return App(
        id=id,
        organization_id=organization_id,
        name="Existing App",
        description="existing",
        icon={
            "type": "emoji",
            "content": "E",
            "background_color": "#FFFFFF",
        },
        workflow_id=workflow_id,
        url_slug=f"app-{id.hex[:8]}",
        auth_secret=None,
        is_market=False,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )


def _route_workflow(id, app_id, organization_id, created_by):
    now = datetime.now(timezone.utc)
    return Workflow(
        id=id,
        organization_id=organization_id,
        app_id=app_id,
        created_by=created_by,
        graph={"nodes": [], "edges": []},
        features={},
        env_variables=[],
        runtime_variables=[],
        created_at=now,
        updated_at=now,
    )


def _route_user_workflow_permission(
    organization_id,
    workflow_id,
    user_id,
    auth_state,
):
    return UserWorkflowPermission(
        id=uuid.uuid4(),
        grantee_organization_id=organization_id,
        workflow_id=workflow_id,
        user_id=user_id,
        auth_state=auth_state,
        assigned_by=user_id,
        assigned_at=datetime.now(timezone.utc),
        options={},
        flags=0,
    )


def test_list_workflows_by_app_hides_outside_scope(monkeypatch):
    app = SimpleNamespace(id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        workflow_endpoint.AppService,
        "access_denial_status",
        lambda db, app_record, user_id, action: 404,
    )

    with pytest.raises(HTTPException) as exc_info:
        workflow_endpoint.list_workflows_by_app(
            str(app.id),
            db=_FakeDb(app),
            current_user=user,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "App not found"


def test_list_workflows_by_app_returns_403_inside_scope_without_app_read(monkeypatch):
    app = SimpleNamespace(id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        workflow_endpoint.AppService,
        "access_denial_status",
        lambda db, app_record, user_id, action: 403,
    )

    with pytest.raises(HTTPException) as exc_info:
        workflow_endpoint.list_workflows_by_app(
            str(app.id),
            db=_FakeDb(app),
            current_user=user,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"
