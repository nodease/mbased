"""workflow 예산 관리 API(FR-051) route 등록 테스트.

TDD red phase: route가 아직 없으므로 404로 실패해야 한다.
route가 등록되어 있으면 미인증 요청은 404가 아니라 인증 실패 401로 끝난다
(test_admin_usage_api.py와 같은 패턴).
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import admin as admin_endpoint
from apps.gateway.main import app
from apps.gateway.services.app_lifecycle_lock import (
    AppPrimaryChangedDuringMutationError,
)
from apps.shared.db.models.workflow_budget import WorkflowBudget
from apps.shared.schemas.workflow_budget import WorkflowBudgetResponse
from apps.shared.services.provider_usage_cost_read_model import (
    ProviderUsageAggregate,
)

KST = ZoneInfo("Asia/Seoul")


class TestWorkflowBudgetRoutesRegistered(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides = {}

    def test_budget_list_route_is_registered(self):
        response = self.client.get("/api/v1/admin/workflow-budgets")

        self.assertEqual(response.status_code, 401)

    def test_budget_detail_route_is_registered(self):
        response = self.client.get(f"/api/v1/admin/workflow-budgets/{uuid4()}")

        self.assertEqual(response.status_code, 401)

    def test_budget_upsert_route_is_registered(self):
        response = self.client.put(
            f"/api/v1/admin/workflow-budgets/{uuid4()}",
            json={"monthly_budget_usd": 100.0, "is_enabled": True},
        )

        self.assertEqual(response.status_code, 401)


def test_budget_usage_serialization_passes_timezone_aware_kst_now(monkeypatch):
    captured = {}
    workflow_id = uuid4()
    now = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    budget = WorkflowBudget(
        id=uuid4(),
        organization_id=uuid4(),
        workflow_id=workflow_id,
        monthly_budget_usd=Decimal("100.00"),
        is_enabled=True,
        created_at=now,
        updated_at=now,
    )

    def fake_get_current_month_usage(db, *, workflow_id, now, organization_id=None):
        captured["workflow_id"] = workflow_id
        captured["now"] = now
        captured["organization_id"] = organization_id
        return ProviderUsageAggregate(
            total_cost=Decimal("1.00"),
            unresolved_provider_call_count=2,
        )

    monkeypatch.setattr(
        admin_endpoint.WorkflowBudgetService,
        "get_current_month_usage",
        staticmethod(fake_get_current_month_usage),
    )

    response = admin_endpoint._serialize_workflow_budget(
        db=object(),
        budget=budget,
        workflow_name="예산 워크플로우",
        include_usage=True,
    )

    assert response.current_month_cost == 1.0
    assert response.usage_data_complete is False
    assert response.unresolved_provider_call_count == 2
    assert captured["workflow_id"] == workflow_id
    assert captured["organization_id"] == budget.organization_id
    assert captured["now"].tzinfo is not None
    assert captured["now"].utcoffset() == KST.utcoffset(captured["now"])


def test_budget_upsert_rejects_unknown_fields_before_service(monkeypatch):
    client = TestClient(app)
    workflow_id = uuid4()
    organization_id = uuid4()
    user_id = uuid4()
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    service_called = False

    app.dependency_overrides[admin_endpoint.get_db] = lambda: object()
    app.dependency_overrides[admin_endpoint.get_current_user] = lambda: SimpleNamespace(
        id=user_id
    )

    def fake_upsert_budget(*args, **kwargs):
        nonlocal service_called
        service_called = True
        return SimpleNamespace()

    monkeypatch.setattr(
        admin_endpoint,
        "_resolve_managed_organization",
        lambda *args, **kwargs: organization_id,
    )
    monkeypatch.setattr(
        admin_endpoint,
        "_workflow_name_in_scope",
        lambda *args, **kwargs: "예산 워크플로우",
    )
    monkeypatch.setattr(
        admin_endpoint.WorkflowBudgetService,
        "upsert_budget",
        staticmethod(fake_upsert_budget),
    )
    monkeypatch.setattr(
        admin_endpoint,
        "_serialize_workflow_budget",
        lambda *args, **kwargs: WorkflowBudgetResponse(
            workflow_id=workflow_id,
            workflow_name="예산 워크플로우",
            monthly_budget_usd=100.0,
            is_enabled=True,
            created_at=now,
            updated_at=now,
        ),
    )

    try:
        response = client.put(
            f"/api/v1/admin/workflow-budgets/{workflow_id}",
            headers={"X-Organization-Id": str(organization_id)},
            json={
                "monthly_budget_usd": 100.0,
                "is_enabled": True,
                "is_enabledd": False,
            },
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 422
    assert service_called is False


def test_budget_upsert_rejects_primary_changed_while_waiting(monkeypatch):
    client = TestClient(app)
    workflow_id = uuid4()
    organization_id = uuid4()
    rollback_calls = []
    db = SimpleNamespace(rollback=lambda: rollback_calls.append(True))
    app.dependency_overrides[admin_endpoint.get_db] = lambda: db
    app.dependency_overrides[admin_endpoint.get_current_user] = lambda: SimpleNamespace(
        id=uuid4()
    )
    monkeypatch.setattr(
        admin_endpoint,
        "_resolve_managed_organization",
        lambda *args, **kwargs: organization_id,
    )
    monkeypatch.setattr(
        admin_endpoint,
        "_workflow_name_in_scope",
        lambda *args, **kwargs: "예산 워크플로우",
    )
    monkeypatch.setattr(
        admin_endpoint.WorkflowBudgetService,
        "upsert_budget",
        staticmethod(
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AppPrimaryChangedDuringMutationError()
            )
        ),
    )

    try:
        response = client.put(
            f"/api/v1/admin/workflow-budgets/{workflow_id}",
            headers={"X-Organization-Id": str(organization_id)},
            json={"monthly_budget_usd": 100.0, "is_enabled": True},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "workflow.primary_changed"
    assert rollback_calls == [True]
