"""테스트 실행 endpoint(execute/stream)의 예산 차단 연결 계약 테스트.

TDD red phase: endpoint에 예산 차단이 아직 연결되지 않아 실패해야 한다.
test_workflow_execution_subject.py의 직접 호출 패턴을 따른다.

- execute/stream: exceeded workflow는 Celery dispatch 전에 429 (BGT-REQ-030~031)
- 권한 검사가 예산 판정보다 먼저다 — 권한 없는 요청은 429가 아니라 403
- compare(A/B)는 차단하지 않는다 (BGT-REQ-032) — guard 테스트 (red에서도 통과)
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.operators import eq
from starlette.requests import Request

from apps.gateway.api.v1.endpoints import workflow as workflow_endpoint
from apps.gateway.application.webhook_ingress import DEFAULT_WEBHOOK_INGRESS_POLICY
from apps.shared.audit.actions import AuditAction
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    app_auth_secret_verifier,
)
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.workflow_budget import WorkflowBudget
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
)


def _exceeded_db(workflow_id, organization_id):
    db = _Db(
        rows=[
            WorkflowBudget(
                id=uuid4(),
                organization_id=organization_id,
                workflow_id=workflow_id,
                monthly_budget_usd=Decimal("100.00"),
                is_enabled=True,
                created_by=uuid4(),
            )
        ]
    )
    db.usage_logs = [
        SimpleNamespace(
            workflow_id=workflow_id,
            organization_id=organization_id,
            prompt_tokens=0,
            completion_tokens=0,
            total_cost=Decimal("150.000000"),
            created_at=datetime.now(timezone.utc),
            runtime_surface=None,
            status="success",
        )
    ]
    return db


def _request(path, *, body=None, headers=None):
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in (headers or {}).items()
        ],
        "query_string": b"",
    }
    if body is None:
        return Request(scope)

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _valid_graph():
    return {
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            }
        ],
        "edges": [],
    }


def _patch_common(monkeypatch, workflow):
    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "get_draft",
        lambda *args, **kwargs: _valid_graph(),
    )
    monkeypatch.setattr(workflow_endpoint, "celery_app", _DispatchGuard())


def test_execute_blocks_exceeded_budget_before_celery_dispatch(monkeypatch):
    workflow_id = uuid4()
    organization_id = uuid4()
    current_user = SimpleNamespace(id=uuid4())
    workflow = SimpleNamespace(
        id=str(workflow_id),
        app_id=uuid4(),
        organization_id=organization_id,
        graph=_valid_graph(),
    )
    db = _exceeded_db(workflow_id, organization_id)
    _patch_common(monkeypatch, workflow)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            workflow_endpoint.execute_workflow(
                str(workflow_id),
                _request(f"/api/v1/workflows/{workflow_id}/execute"),
                user_input={},
                db=db,
                current_user=current_user,
            )
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "budget.exceeded"
    audits = db.added_of(AuditLog)
    assert len(audits) == 1
    assert audits[0].action == AuditAction.POLICY_BLOCK
    assert audits[0].actor_id == current_user.id
    assert audits[0].audit_metadata["trigger_mode"] == "test"


def test_stream_blocks_exceeded_budget_before_celery_dispatch(monkeypatch):
    workflow_id = uuid4()
    organization_id = uuid4()
    current_user = SimpleNamespace(id=uuid4())
    workflow = SimpleNamespace(
        id=str(workflow_id), app_id=uuid4(), organization_id=organization_id
    )
    db = _exceeded_db(workflow_id, organization_id)
    _patch_common(monkeypatch, workflow)
    monkeypatch.setattr(
        workflow_endpoint, "validate_execution_graph", lambda graph: None
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            workflow_endpoint.stream_workflow(
                str(workflow_id),
                _request(f"/api/v1/workflows/{workflow_id}/stream", body=b"{}"),
                db=db,
                current_user=current_user,
            )
        )

    # SSE 스트림이 시작되기 전에 차단된다.
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "budget.exceeded"
    assert [audit.action for audit in db.added_of(AuditLog)] == [
        AuditAction.POLICY_BLOCK
    ]


def test_stream_rejects_active_organization_mismatch_before_dispatch(monkeypatch):
    workflow_id = uuid4()
    workflow_organization_id = uuid4()
    active_organization_id = uuid4()
    current_user = SimpleNamespace(id=uuid4())
    workflow = SimpleNamespace(
        id=str(workflow_id),
        app_id=uuid4(),
        organization_id=workflow_organization_id,
    )
    db = _Db()
    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint,
        "resolve_active_organization_id",
        lambda _db, _request, raw, _user_id: active_organization_id,
    )
    monkeypatch.setattr(workflow_endpoint, "celery_app", _DispatchGuard())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            workflow_endpoint.stream_workflow(
                str(workflow_id),
                _request(
                    f"/api/v1/workflows/{workflow_id}/stream",
                    body=b"{}",
                    headers={"X-Organization-Id": str(active_organization_id)},
                ),
                x_organization_id=str(active_organization_id),
                db=db,
                current_user=current_user,
            )
        )

    assert exc_info.value.status_code == 404
    assert db.added == []


def test_execute_rejects_active_organization_mismatch_before_dispatch(monkeypatch):
    workflow_id = uuid4()
    workflow_organization_id = uuid4()
    active_organization_id = uuid4()
    current_user = SimpleNamespace(id=uuid4())
    workflow = SimpleNamespace(
        id=str(workflow_id),
        app_id=uuid4(),
        organization_id=workflow_organization_id,
    )
    db = _Db()
    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint,
        "resolve_active_organization_id",
        lambda _db, _request, raw, _user_id: active_organization_id,
    )
    monkeypatch.setattr(workflow_endpoint, "celery_app", _DispatchGuard())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            workflow_endpoint.execute_workflow(
                str(workflow_id),
                _request(
                    f"/api/v1/workflows/{workflow_id}/execute",
                    headers={"X-Organization-Id": str(active_organization_id)},
                ),
                user_input={},
                x_organization_id=str(active_organization_id),
                db=db,
                current_user=current_user,
            )
        )

    assert exc_info.value.status_code == 404
    assert db.added == []


def test_execute_allows_matching_active_organization_before_dispatch(monkeypatch):
    workflow_id = uuid4()
    organization_id = uuid4()
    current_user = SimpleNamespace(id=uuid4())
    workflow = SimpleNamespace(
        id=str(workflow_id),
        app_id=uuid4(),
        organization_id=organization_id,
        graph=_valid_graph(),
    )
    db = _Db()
    celery = _DispatchRecorder()
    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint,
        "resolve_active_organization_id",
        lambda _db, _request, raw, _user_id: organization_id,
    )
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "get_draft",
        lambda *args, **kwargs: _valid_graph(),
    )
    monkeypatch.setattr(workflow_endpoint, "celery_app", celery)

    asyncio.run(
        workflow_endpoint.execute_workflow(
            str(workflow_id),
            _request(
                f"/api/v1/workflows/{workflow_id}/execute",
                headers={"X-Organization-Id": str(organization_id)},
            ),
            user_input={},
            x_organization_id=str(organization_id),
            db=db,
            current_user=current_user,
        )
    )

    assert len(celery.calls) == 1
    execution_context = celery.calls[0]["args"][2]
    assert execution_context["organization_id"] == str(organization_id)


def test_execute_draft_not_found_preserves_404(monkeypatch):
    workflow_id = uuid4()
    organization_id = uuid4()
    current_user = SimpleNamespace(id=uuid4())
    workflow = SimpleNamespace(
        id=str(workflow_id),
        app_id=uuid4(),
        organization_id=organization_id,
        graph=_valid_graph(),
    )
    db = _Db()
    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "get_draft",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(workflow_endpoint, "celery_app", _DispatchGuard())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            workflow_endpoint.execute_workflow(
                str(workflow_id),
                _request(f"/api/v1/workflows/{workflow_id}/execute"),
                user_input={},
                db=db,
                current_user=current_user,
            )
        )

    assert exc_info.value.status_code == 404
    assert "draft not found" in exc_info.value.detail
    assert db.added == []


def test_stream_request_id_prefers_middleware_state_over_header():
    request = _request(
        "/api/v1/workflows/workflow-1/stream",
        body=b"{}",
        headers={"X-Request-Id": "header-request-id"},
    )
    request.state.request_id = "state-request-id"

    assert workflow_endpoint._request_id_from_request(request) == "state-request-id"


def test_execute_permission_check_precedes_budget_block(monkeypatch):
    # 권한 없는 요청은 예산 상태와 무관하게 403이다 (api_spec Permissions).
    workflow_id = uuid4()
    db = _exceeded_db(workflow_id, uuid4())

    def _deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="permission.denied")

    monkeypatch.setattr(workflow_endpoint, "ensure_workflow_permission", _deny)
    monkeypatch.setattr(workflow_endpoint, "celery_app", _DispatchGuard())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            workflow_endpoint.execute_workflow(
                str(workflow_id),
                _request(f"/api/v1/workflows/{workflow_id}/execute"),
                user_input={},
                db=db,
                current_user=SimpleNamespace(id=uuid4()),
            )
        )

    assert exc_info.value.status_code == 403
    assert db.added_of(AuditLog) == []


def test_compare_is_not_blocked_by_exceeded_budget(monkeypatch):
    # 초과 workflow의 A/B 비교는 비용 최적화 복구 경로라 차단하지 않는다
    # (BGT-REQ-032). guard 테스트: red에서도 통과하며 green에서 유지돼야 한다.
    workflow_id = uuid4()
    organization_id = uuid4()
    workflow = SimpleNamespace(
        id=str(workflow_id), app_id=uuid4(), organization_id=organization_id
    )
    db = _exceeded_db(workflow_id, organization_id)
    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: workflow,
    )
    # draft 없음 → 404. 만약 compare에 예산 차단이 잘못 연결되면 429가 나와
    # 이 테스트가 실패한다.
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "get_draft",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        workflow_endpoint.compare_workflow_variants(
            str(workflow_id),
            request_body=SimpleNamespace(),
            request=_request(f"/api/v1/workflows/{workflow_id}/compare"),
            db=db,
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert exc_info.value.status_code == 404
    assert db.added_of(AuditLog) == []


def test_webhook_blocks_exceeded_budget_before_background_dispatch():
    # webhook 경로도 서버 차단 대상이다 (BGT-REQ-030, api_spec 실행 차단 표).
    from fastapi import BackgroundTasks

    from apps.gateway.api.v1.endpoints import webhook as webhook_endpoint
    from apps.shared.db.models.app import App
    from apps.shared.db.models.workflow_deployment import (
        DeploymentType,
        WorkflowDeployment,
    )

    workflow_id = uuid4()
    organization_id = uuid4()
    deployment_id = uuid4()
    app_row = App(
        id=uuid4(),
        name="예산 초과 웹훅 앱",
        url_slug=f"hook-{uuid4().hex[:8]}",
        auth_secret=None,
        auth_secret_verifier=app_auth_secret_verifier("hook-secret"),
        auth_secret_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
        auth_secret_generation=1,
        workflow_id=workflow_id,
        organization_id=organization_id,
        active_deployment_id=deployment_id,
        created_by=uuid4(),
    )
    deployment_row = WorkflowDeployment(
        id=deployment_id,
        app_id=app_row.id,
        version=1,
        type=DeploymentType.WEBHOOK,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid4(),
    )
    db = _exceeded_db(workflow_id, organization_id)
    db.rows.extend([app_row, deployment_row])
    background_tasks = BackgroundTasks()
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/hooks/{app_row.url_slug}",
        "headers": [
            (b"x-webhook-secret", b"hook-secret"),
            (b"content-type", b"application/json"),
        ],
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            webhook_endpoint.receive_webhook(
                app_row.url_slug,
                Request(scope, receive),
                background_tasks,
                runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
                ingress_policy=DEFAULT_WEBHOOK_INGRESS_POLICY,
                db=db,
            )
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "budget.exceeded"
    assert background_tasks.tasks == []  # dispatch 예약 자체가 없어야 한다
    audits = db.added_of(AuditLog)
    assert len(audits) == 1
    assert audits[0].audit_metadata["trigger_mode"] == "webhook"
    assert audits[0].actor_id is None


def test_webhook_rejects_non_webhook_deployment_before_budget_or_dispatch():
    from fastapi import BackgroundTasks

    from apps.gateway.api.v1.endpoints import webhook as webhook_endpoint
    from apps.shared.db.models.app import App
    from apps.shared.db.models.workflow_deployment import (
        DeploymentType,
        WorkflowDeployment,
    )

    workflow_id = uuid4()
    organization_id = uuid4()
    deployment_id = uuid4()
    app_row = App(
        id=uuid4(),
        name="비웹훅 배포 차단",
        url_slug=f"hook-{uuid4().hex[:8]}",
        auth_secret=None,
        auth_secret_verifier=app_auth_secret_verifier("hook-secret"),
        auth_secret_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
        auth_secret_generation=1,
        workflow_id=workflow_id,
        organization_id=organization_id,
        active_deployment_id=deployment_id,
        created_by=uuid4(),
    )
    deployment_row = WorkflowDeployment(
        id=deployment_id,
        app_id=app_row.id,
        version=1,
        type=DeploymentType.API,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid4(),
    )
    db = _exceeded_db(workflow_id, organization_id)
    db.rows.extend([app_row, deployment_row])
    background_tasks = BackgroundTasks()
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/hooks/{app_row.url_slug}",
        "headers": [
            (b"x-webhook-secret", b"hook-secret"),
            (b"content-type", b"application/json"),
        ],
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            webhook_endpoint.receive_webhook(
                app_row.url_slug,
                Request(scope, receive),
                background_tasks,
                runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
                ingress_policy=DEFAULT_WEBHOOK_INGRESS_POLICY,
                db=db,
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Active deployment not found"
    assert background_tasks.tasks == []
    assert db.added_of(AuditLog) == []


# --- fakes -------------------------------------------------------------------


class _Query:
    def __init__(self, items):
        self.items = list(items)
        self.filters = []

    def filter(self, *expressions):
        self.filters.extend(expressions)
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return [item for item in self.items if self._matches(item)]

    def first(self):
        return next(iter(self.all()), None)

    def _matches(self, item):
        return all(
            _matches_expression(item, expression) for expression in self.filters
        )


def _matches_expression(item, expression):
    if not hasattr(expression, "left"):
        return True
    column = str(expression.left).split(".")[-1]
    if not hasattr(item, column):
        return True
    if expression.operator is not eq:
        return True
    right = expression.right
    right_value = right.value if hasattr(right, "value") else right
    return getattr(item, column) == right_value


class _Db:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, model, *rest):
        return _Query([row for row in self.rows if isinstance(row, model)])

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, obj):
        pass

    def added_of(self, model):
        return [obj for obj in self.added if isinstance(obj, model)]


class _DispatchGuard:
    """차단됐어야 할 실행이 Celery로 넘어가면 즉시 실패시킨다 (SSE/폴링 hang 방지)."""

    backend = SimpleNamespace(TimeoutError=TimeoutError)

    def send_task(self, *args, **kwargs):
        raise AssertionError(
            "budget-blocked run must not be dispatched to Celery"
        )


class _SuccessfulTask:
    def get(self, timeout=None):
        return {"status": "success", "result": {}}


class _DispatchRecorder:
    backend = SimpleNamespace(TimeoutError=TimeoutError)

    def __init__(self):
        self.calls = []

    def send_task(self, name, args=None, kwargs=None, **options):
        self.calls.append({"name": name, "args": args or [], "kwargs": kwargs or {}})
        return _SuccessfulTask()
