from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from apps.gateway.services.app_service import AppService
from apps.gateway.services.workflow_budget_service import WorkflowBudgetService
from apps.shared.db.models.workflow_budget import WorkflowBudget

KST = ZoneInfo("Asia/Seoul")


def test_app_budget_status_boundaries_and_null_conditions():
    organization_id = uuid4()
    workflow_ids = {
        "normal": uuid4(),
        "at_risk": uuid4(),
        "exact_budget": uuid4(),
        "exceeded": uuid4(),
        "null_cost": uuid4(),
        "disabled": uuid4(),
        "zero_budget": uuid4(),
        "no_budget": uuid4(),
    }
    db = _BudgetStatusDb(
        budgets=[
            _budget_row(organization_id, workflow_ids["normal"], Decimal("100.00")),
            _budget_row(organization_id, workflow_ids["at_risk"], Decimal("100.00")),
            _budget_row(
                organization_id, workflow_ids["exact_budget"], Decimal("100.00")
            ),
            _budget_row(organization_id, workflow_ids["exceeded"], Decimal("100.00")),
            _budget_row(organization_id, workflow_ids["null_cost"], Decimal("100.00")),
            _budget_row(
                organization_id,
                workflow_ids["disabled"],
                Decimal("100.00"),
                is_enabled=False,
            ),
            _budget_row(organization_id, workflow_ids["zero_budget"], Decimal("0")),
            _budget_row(uuid4(), uuid4(), Decimal("100.00")),
        ],
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_ids["normal"],
                total_cost=Decimal("79.99"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                workflow_ids["normal"],
                total_cost=Decimal("100.00"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
                runtime_surface="agent_builder_intent",
                status="pending",
            ),
            _usage_log(
                organization_id,
                workflow_ids["at_risk"],
                total_cost=Decimal("80.00"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                workflow_ids["exact_budget"],
                total_cost=Decimal("100.00"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                workflow_ids["exceeded"],
                total_cost=Decimal("100.000001"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                workflow_ids["null_cost"],
                total_cost=None,
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    statuses = AppService._budget_status_by_workflow_id(
        db,
        list(workflow_ids.values()),
        organization_id=organization_id,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=KST),
    )

    assert statuses[workflow_ids["normal"]] == {
        "usage_ratio": pytest.approx(0.7999),
        "status": "normal",
    }
    assert statuses[workflow_ids["at_risk"]] == {
        "usage_ratio": pytest.approx(0.8),
        "status": "at_risk",
    }
    assert statuses[workflow_ids["exact_budget"]] == {
        "usage_ratio": pytest.approx(1.0),
        "status": "at_risk",
    }
    assert statuses[workflow_ids["exceeded"]] == {
        "usage_ratio": pytest.approx(1.00000001),
        "status": "exceeded",
    }
    assert statuses[workflow_ids["null_cost"]] == {
        "usage_ratio": pytest.approx(0.0),
        "status": "normal",
    }
    assert statuses[workflow_ids["disabled"]] is None
    assert statuses[workflow_ids["zero_budget"]] is None
    assert statuses[workflow_ids["no_budget"]] is None
    for status in statuses.values():
        if status is None:
            continue
        assert set(status) == {"usage_ratio", "status"}


def test_app_budget_status_uses_kst_month_window():
    organization_id = uuid4()
    workflow_id = uuid4()
    db = _BudgetStatusDb(
        budgets=[_budget_row(organization_id, workflow_id, Decimal("100.00"))],
        usage_logs=[
            # KST 2026-08-01 00:00 - 8월 포함
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("90.00"),
                created_at=datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc),
            ),
            # KST 2026-07-31 23:59:59 - 8월 제외
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("10.00"),
                created_at=datetime(2026, 7, 31, 14, 59, 59, tzinfo=timezone.utc),
            ),
            # KST 2026-09-01 00:00 - 8월 [start, end) 끝 경계라 제외
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("10.00"),
                created_at=datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    statuses = AppService._budget_status_by_workflow_id(
        db,
        [workflow_id],
        organization_id=organization_id,
        now=datetime(2026, 8, 1, 1, 0, tzinfo=KST),
    )

    assert statuses[workflow_id] == {
        "usage_ratio": pytest.approx(0.9),
        "status": "at_risk",
    }


def test_app_budget_status_prefers_canonical_usage_without_projection_double_count():
    organization_id = uuid4()
    workflow_id = uuid4()
    operation_id = uuid4()
    started_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    db = _BudgetStatusDb(
        budgets=[_budget_row(organization_id, workflow_id, Decimal("100.00"))],
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("90.00"),
                created_at=started_at,
                provider_usage_operation_id=operation_id,
            ),
        ],
        provider_usage_operations=[
            _provider_usage_operation(
                operation_id,
                organization_id,
                workflow_id,
                total_cost_microusd=60_000_000,
                provider_started_at=started_at,
            ),
            _provider_usage_operation(
                uuid4(),
                organization_id,
                workflow_id,
                total_cost_microusd=25_000_000,
                provider_started_at=started_at,
            ),
        ],
    )

    statuses = AppService._budget_status_by_workflow_id(
        db,
        [workflow_id],
        organization_id=organization_id,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=KST),
    )

    assert statuses[workflow_id] == {
        "usage_ratio": pytest.approx(0.85),
        "status": "at_risk",
    }


def test_app_operation_metrics_uses_real_monthly_usage_and_trend():
    organization_id = uuid4()
    workflow_id = uuid4()
    db = _BudgetStatusDb(
        budgets=[],
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("10.00"),
                created_at=datetime(2026, 6, 20, 0, 0, tzinfo=KST),
            ),
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("10.00"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=KST),
            ),
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("5.00"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=KST),
                runtime_surface="agent_builder_intent",
            ),
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("100.00"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=KST),
                runtime_surface="agent_builder_intent",
                status="pending",
            ),
        ],
    )

    metrics = AppService._operation_metrics_by_workflow_id(
        db,
        [workflow_id],
        now=datetime(2026, 7, 16, 0, 0, tzinfo=KST),
    )

    assert metrics[workflow_id] == {
        "current_month_cost": pytest.approx(15.0),
        "current_month_workflow_execution_cost": pytest.approx(10.0),
        "current_month_agent_builder_cost": pytest.approx(5.0),
        "projected_month_cost": pytest.approx(31.0),
        "projected_month_workflow_execution_cost": pytest.approx(62 / 3),
        "projected_month_agent_builder_cost": pytest.approx(31 / 3),
        "previous_month_cost": pytest.approx(10.0),
        "trend_percent": pytest.approx(210.0),
        "usage_data_complete": True,
        "unresolved_provider_call_count": 0,
    }


def test_app_operation_metrics_marks_unresolved_provider_usage_incomplete():
    organization_id = uuid4()
    workflow_id = uuid4()
    started_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    db = _BudgetStatusDb(
        budgets=[],
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("10.00"),
                created_at=started_at,
            )
        ],
        provider_usage_operations=[
            _provider_usage_operation(
                uuid4(),
                organization_id,
                workflow_id,
                total_cost_microusd=None,
                provider_started_at=started_at,
                state="outcome_unknown",
            )
        ],
    )

    metrics = AppService._operation_metrics_by_workflow_id(
        db,
        [workflow_id],
        organization_id=organization_id,
        now=datetime(2026, 7, 16, 0, 0, tzinfo=KST),
    )

    assert metrics[workflow_id]["current_month_cost"] == pytest.approx(10.0)
    assert metrics[workflow_id]["usage_data_complete"] is False
    assert metrics[workflow_id]["unresolved_provider_call_count"] == 1


def test_app_budget_status_uses_grouped_cost_lookup(monkeypatch):
    organization_id = uuid4()
    workflow_ids = [uuid4(), uuid4()]
    db = _BudgetStatusDb(
        budgets=[
            _budget_row(organization_id, workflow_ids[0], Decimal("100.00")),
            _budget_row(organization_id, workflow_ids[1], Decimal("100.00")),
        ],
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_ids[0],
                total_cost=Decimal("10.00"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                workflow_ids[1],
                total_cost=Decimal("95.00"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    def fail_per_workflow_cost(*args, **kwargs):
        raise AssertionError("budget_status must not call per-workflow cost lookup")

    monkeypatch.setattr(
        WorkflowBudgetService,
        "get_current_month_cost",
        fail_per_workflow_cost,
    )

    statuses = AppService._budget_status_by_workflow_id(
        db,
        workflow_ids,
        organization_id=organization_id,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=KST),
    )

    assert statuses[workflow_ids[0]]["status"] == "normal"
    assert statuses[workflow_ids[1]]["status"] == "at_risk"


def test_app_budget_status_includes_null_organization_usage_for_primary_workflow():
    organization_id = uuid4()
    workflow_id = uuid4()
    other_workflow_id = uuid4()
    db = _BudgetStatusDb(
        budgets=[_budget_row(organization_id, workflow_id, Decimal("100.00"))],
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("80.00"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                None,
                workflow_id,
                total_cost=Decimal("30.00"),
                created_at=datetime(2026, 7, 11, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                None,
                other_workflow_id,
                total_cost=Decimal("999.00"),
                created_at=datetime(2026, 7, 11, 0, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    statuses = AppService._budget_status_by_workflow_id(
        db,
        [workflow_id],
        organization_id=organization_id,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=KST),
    )

    assert statuses[workflow_id] == {
        "usage_ratio": pytest.approx(1.1),
        "status": "exceeded",
    }


def test_attach_budget_status_ignores_workflow_app_relation_when_app_pointer_is_missing():
    organization_id = uuid4()
    app_id = uuid4()
    workflow_id = uuid4()
    app = SimpleNamespace(
        id=app_id,
        organization_id=organization_id,
        workflow_id=None,
    )
    db = _BudgetStatusDb(
        budgets=[_budget_row(organization_id, workflow_id, Decimal("100.00"))],
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("95.00"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            )
        ],
        workflows=[
            SimpleNamespace(
                id=workflow_id,
                app_id=app_id,
                organization_id=organization_id,
            )
        ],
    )

    AppService._attach_budget_statuses(
        db,
        [app],
        organization_id=organization_id,
    )

    assert app.budget_status is None


def test_attach_budget_status_ignores_secondary_workflow_when_primary_has_no_budget():
    organization_id = uuid4()
    app_id = uuid4()
    primary_workflow_id = uuid4()
    secondary_workflow_id = uuid4()
    app = SimpleNamespace(
        id=app_id,
        organization_id=organization_id,
        workflow_id=primary_workflow_id,
    )
    db = _BudgetStatusDb(
        budgets=[
            _budget_row(
                organization_id,
                secondary_workflow_id,
                Decimal("100.00"),
            )
        ],
        usage_logs=[
            _usage_log(
                organization_id,
                secondary_workflow_id,
                total_cost=Decimal("100.000001"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            )
        ],
        workflows=[
            SimpleNamespace(
                id=primary_workflow_id,
                app_id=app_id,
                organization_id=organization_id,
            ),
            SimpleNamespace(
                id=secondary_workflow_id,
                app_id=app_id,
                organization_id=organization_id,
            ),
        ],
    )

    AppService._attach_budget_statuses(
        db,
        [app],
        organization_id=organization_id,
    )

    assert app.budget_status is None


def test_attach_operation_metrics_rolls_back_failed_optional_lookup(monkeypatch):
    class RollbackTrackingDb:
        def __init__(self):
            self.rollback_calls = 0

        def rollback(self):
            self.rollback_calls += 1

    def fail_metrics_lookup(*_args, **_kwargs):
        raise RuntimeError("simulated projection lookup failure")

    db = RollbackTrackingDb()
    app = SimpleNamespace(workflow_id=uuid4())
    monkeypatch.setattr(
        AppService,
        "_operation_metrics_by_workflow_id",
        staticmethod(fail_metrics_lookup),
    )

    AppService._attach_operation_metrics(db, [app])

    assert db.rollback_calls == 1
    assert app.operation_metrics is None


class _BudgetStatusDb:
    def __init__(
        self,
        *,
        budgets,
        usage_logs,
        workflows=None,
        provider_usage_operations=None,
    ):
        self.budgets = budgets
        self.usage_logs = usage_logs
        self.workflows = workflows or []
        self.provider_usage_operations = provider_usage_operations or []


def _budget_row(organization_id, workflow_id, monthly_budget_usd, *, is_enabled=True):
    return WorkflowBudget(
        id=uuid4(),
        organization_id=organization_id,
        workflow_id=workflow_id,
        monthly_budget_usd=monthly_budget_usd,
        is_enabled=is_enabled,
        created_by=uuid4(),
    )


def _usage_log(
    organization_id,
    workflow_id,
    *,
    total_cost,
    created_at,
    runtime_surface=None,
    status="success",
    provider_usage_operation_id=None,
):
    return SimpleNamespace(
        organization_id=organization_id,
        workflow_id=workflow_id,
        prompt_tokens=1,
        completion_tokens=1,
        total_cost=total_cost,
        created_at=created_at,
        runtime_surface=runtime_surface,
        status=status,
        provider_usage_operation_id=provider_usage_operation_id,
    )


def _provider_usage_operation(
    operation_id,
    organization_id,
    workflow_id,
    *,
    total_cost_microusd,
    provider_started_at,
    state="succeeded",
):
    return SimpleNamespace(
        id=operation_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        purpose="main_generation",
        state=state,
        provider_started_at=provider_started_at,
        prompt_tokens=10,
        completion_tokens=5,
        total_cost_microusd=total_cost_microusd,
    )
