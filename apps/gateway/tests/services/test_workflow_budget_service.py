"""WorkflowBudgetService(FR-051, budget-management) 계약 테스트.

TDD red phase: 서비스와 AuditAction 상수가 아직 없으므로 전부 실패해야 한다.
docs/features/budget-management/test_cases.md의 Unit Tests 계약을 검증한다.

- 판정 함수: 80%/100% 경계, Decimal 정밀도, 비활성/0 이하 제외 (BGT-REQ-010~012)
- 당월 집계: KST 달력 월 [start, end), NULL cost 0 합산, now 주입 (BGT-REQ-011)
- 예산 upsert: 생성/갱신/비활성화/no-op/생성 경합, audit 분기 (BGT-REQ-001~005, 040)
- admin summary/usage의 budget 블록 (BGT-REQ-020~021)
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.operators import eq

from apps.gateway.services.app_lifecycle_lock import (
    AppPrimaryChangedDuringMutationError,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.app import App
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_budget import WorkflowBudget

KST = ZoneInfo("Asia/Seoul")


def _service():
    from apps.gateway.services.workflow_budget_service import WorkflowBudgetService

    return WorkflowBudgetService


def _admin_usage_service():
    from apps.gateway.services.admin_usage_service import AdminUsageService

    return AdminUsageService


# --- classify_budget_usage (BGT-REQ-010~012) --------------------------------


@pytest.mark.parametrize(
    ("current_cost", "expected"),
    [
        (Decimal("79.99"), "normal"),
        (Decimal("80.00"), "at_risk"),
        (Decimal("100.00"), "at_risk"),  # 정확히 100%는 초과가 아니다
        (Decimal("100.000001"), "exceeded"),
        (Decimal("0"), "normal"),
        (Decimal("100000.00"), "exceeded"),
    ],
)
def test_classify_budget_usage_boundaries(current_cost, expected):
    service = _service()

    assert (
        service.classify_budget_usage(
            current_cost=current_cost,
            monthly_budget_usd=Decimal("100.00"),
        )
        == expected
    )


def test_classify_budget_usage_uses_decimal_not_float():
    # float로 계산하면 0.24/0.30이 0.8보다 작게 표현될 수 있다.
    # Decimal로는 정확히 80%라 at_risk다 (requirements Policies의 Decimal 규칙).
    service = _service()

    assert (
        service.classify_budget_usage(
            current_cost=Decimal("0.24"),
            monthly_budget_usd=Decimal("0.30"),
        )
        == "at_risk"
    )


def test_classify_budget_usage_reads_the_centralized_at_risk_threshold(monkeypatch):
    """임계값을 한 곳에서 조정하면 모든 판정 경계가 함께 바뀐다."""
    from apps.gateway.services import workflow_budget_service as budget_module

    monkeypatch.setattr(
        budget_module,
        "BUDGET_AT_RISK_RATIO",
        Decimal("0.85"),
    )

    assert (
        budget_module.WorkflowBudgetService.classify_budget_usage(
            current_cost=Decimal("84.00"),
            monthly_budget_usd=Decimal("100.00"),
        )
        == "normal"
    )
    assert (
        budget_module.WorkflowBudgetService.classify_budget_usage(
            current_cost=Decimal("85.00"),
            monthly_budget_usd=Decimal("100.00"),
        )
        == "at_risk"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"monthly_budget_usd": Decimal("100.00"), "is_enabled": False},
        {"monthly_budget_usd": Decimal("0"), "is_enabled": True},
        {"monthly_budget_usd": Decimal("-1.00"), "is_enabled": True},
        {"monthly_budget_usd": None, "is_enabled": True},
    ],
)
def test_classify_budget_usage_excludes_inactive_budgets(kwargs):
    # 비활성/0 이하/미설정 예산은 판정 대상이 아니다 (BGT-REQ-010).
    service = _service()

    assert (
        service.classify_budget_usage(current_cost=Decimal("999.00"), **kwargs)
        is None
    )


def test_has_active_budget_locks_scope_before_reading_budget():
    service = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    budget = WorkflowBudget(
        id=uuid4(),
        organization_id=organization_id,
        workflow_id=workflow_id,
        monthly_budget_usd=Decimal("100.00"),
        is_enabled=True,
    )
    db = _Db([budget])

    assert (
        service.has_active_budget(
            db,
            workflow_id=workflow_id,
            organization_id=organization_id,
        )
        is True
    )
    assert len(db.executed) == 1
    compiled = str(
        db.executed[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "pg_advisory_xact_lock" in compiled
    assert "workflow_budget_scope" in compiled


@pytest.mark.parametrize(
    ("amount", "is_enabled"),
    [
        (Decimal("0.00"), True),
        (Decimal("100.00"), False),
    ],
)
def test_has_active_budget_excludes_inactive_budget(amount, is_enabled):
    service = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    db = _Db(
        [
            WorkflowBudget(
                id=uuid4(),
                organization_id=organization_id,
                workflow_id=workflow_id,
                monthly_budget_usd=amount,
                is_enabled=is_enabled,
            )
        ]
    )

    assert (
        service.has_active_budget(
            db,
            workflow_id=workflow_id,
            organization_id=organization_id,
        )
        is False
    )


def test_has_active_budget_returns_false_when_budget_is_missing():
    service = _service()
    db = _Db([])

    assert (
        service.has_active_budget(
            db,
            workflow_id=uuid4(),
            organization_id=uuid4(),
        )
        is False
    )


# --- get_current_month_cost (BGT-REQ-011) ------------------------------------


def test_get_current_month_cost_uses_kst_month_boundaries_and_null_as_zero():
    service = _service()
    workflow_id = uuid4()
    other_workflow_id = uuid4()
    organization_id = uuid4()
    db = _UsageDb(
        usage_logs=[
            # KST 2026-07-01 00:30 — 7월 포함
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("0.500000"),
                created_at=datetime(2026, 6, 30, 15, 30, tzinfo=timezone.utc),
            ),
            # KST 2026-06-30 23:59 — 6월이라 제외
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("7.000000"),
                created_at=datetime(2026, 6, 30, 14, 59, tzinfo=timezone.utc),
            ),
            # KST 2026-08-01 00:00 — [start, end) 끝 경계라 제외
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("9.000000"),
                created_at=datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc),
            ),
            # NULL cost는 0으로 합산
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=None,
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("100.000000"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
                runtime_surface="agent_builder_intent",
                status="pending",
            ),
            # 다른 workflow 제외
            _usage_log(
                organization_id,
                other_workflow_id,
                total_cost=Decimal("50.000000"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    cost = service.get_current_month_cost(
        db,
        workflow_id=workflow_id,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=KST),
    )

    assert cost == Decimal("0.5")
    assert isinstance(cost, Decimal)


def test_get_current_month_cost_optional_organization_projection_excludes_conflicts():
    service = _service()
    organization_id = uuid4()
    other_organization_id = uuid4()
    workflow_id = uuid4()
    created_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    db = _UsageDb(
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("10.000000"),
                created_at=created_at,
            ),
            _usage_log(
                None,
                workflow_id,
                total_cost=Decimal("20.000000"),
                created_at=created_at,
            ),
            _usage_log(
                other_organization_id,
                workflow_id,
                total_cost=Decimal("100.000000"),
                created_at=created_at,
            ),
        ]
    )
    now = datetime(2026, 7, 15, 9, 0, tzinfo=KST)

    scoped = service.get_current_month_cost(
        db,
        workflow_id=workflow_id,
        now=now,
        organization_id=organization_id,
    )
    unscoped = service.get_current_month_cost(
        db,
        workflow_id=workflow_id,
        now=now,
    )

    assert scoped == Decimal("30.000000")
    assert unscoped == Decimal("130.000000")


def test_current_month_cost_sql_applies_optional_organization_projection(monkeypatch):
    from apps.gateway.services.admin_usage_service import AdminUsagePeriod
    from apps.gateway.services.workflow_budget_service import _current_month_cost_query

    organization_id = uuid4()
    captured_sql = {}

    def capture_execute(_session, statement, *args, **kwargs):
        captured_sql["query"] = str(
            statement.compile(dialect=postgresql.dialect())
        )
        return SimpleNamespace(
            one_or_none=lambda: SimpleNamespace(
                prompt_tokens=0,
                completion_tokens=0,
                call_count=1,
                total_cost=Decimal("30.000000"),
                agent_builder_cost=Decimal("0"),
                unresolved_provider_call_count=0,
            )
        )

    monkeypatch.setattr(Session, "execute", capture_execute)
    db = Session()

    try:
        cost = _current_month_cost_query(
            db,
            workflow_id=uuid4(),
            period=AdminUsagePeriod(
                start_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
                end_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
            ),
            organization_id=organization_id,
        )
    finally:
        db.close()

    assert cost == Decimal("30.000000")
    assert "llm_usage_logs.organization_id" in captured_sql["query"]
    assert "llm_usage_logs.organization_id IS NULL" in captured_sql["query"]
    assert "provider_usage_operations.organization_id" in captured_sql["query"]


def test_get_current_month_cost_returns_zero_for_no_usage():
    service = _service()

    cost = service.get_current_month_cost(
        _UsageDb(usage_logs=[]),
        workflow_id=uuid4(),
        now=datetime(2026, 7, 15, 9, 0, tzinfo=KST),
    )

    assert cost == Decimal("0")


# --- upsert_budget (BGT-REQ-001~005, BGT-REQ-040) ----------------------------


def test_upsert_budget_creates_row_and_records_created_audit():
    service = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    actor_id = uuid4()
    db = _Db()
    _add_primary_workflow_scope(db, organization_id, workflow_id, actor_id)

    budget = service.upsert_budget(
        db,
        organization_id=organization_id,
        workflow_id=workflow_id,
        actor_id=actor_id,
        monthly_budget_usd=Decimal("100.00"),
        is_enabled=True,
    )

    assert budget.organization_id == organization_id
    assert budget.workflow_id == workflow_id
    assert budget.monthly_budget_usd == Decimal("100.00")
    assert budget.is_enabled is True
    assert budget.created_by == actor_id
    audits = db.added_of(AuditLog)
    assert [audit.action for audit in audits] == [
        AuditAction.WORKFLOW_BUDGET_CREATED
    ]
    assert audits[0].target_type == "workflow_budget"
    assert audits[0].audit_metadata == {
        "organization_id": str(organization_id),
        "monthly_budget_usd": "100.00",
        "is_enabled": True,
    }
    assert db.commits >= 1


def test_upsert_budget_rejects_non_primary_workflow_without_writing_budget():
    service = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    actor_id = uuid4()
    db = _Db()
    app, _workflow = _add_primary_workflow_scope(
        db,
        organization_id,
        workflow_id,
        actor_id,
    )
    app.workflow_id = uuid4()

    with pytest.raises(AppPrimaryChangedDuringMutationError):
        service.upsert_budget(
            db,
            organization_id=organization_id,
            workflow_id=workflow_id,
            actor_id=actor_id,
            monthly_budget_usd=Decimal("100.00"),
            is_enabled=True,
        )

    assert db.added_of(WorkflowBudget) == []
    assert db.commits == 0
    assert db.executed == []


def test_upsert_budget_updates_existing_row_and_records_updated_audit():
    service = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    creator_id = uuid4()
    updater_id = uuid4()
    existing = _budget_row(
        organization_id, workflow_id, Decimal("100.00"), created_by=creator_id
    )
    db = _Db(rows=[existing])
    _add_primary_workflow_scope(db, organization_id, workflow_id, updater_id)

    budget = service.upsert_budget(
        db,
        organization_id=organization_id,
        workflow_id=workflow_id,
        actor_id=updater_id,
        monthly_budget_usd=Decimal("250.00"),
        is_enabled=True,
    )

    assert budget is existing
    assert budget.monthly_budget_usd == Decimal("250.00")
    assert budget.created_by == creator_id  # 생성자는 불변
    assert budget.updated_by == updater_id
    assert [audit.action for audit in db.added_of(AuditLog)] == [
        AuditAction.WORKFLOW_BUDGET_UPDATED
    ]


def test_upsert_budget_disable_keeps_row_and_records_updated_audit():
    # 비활성화는 row 삭제가 아니다 (BGT-REQ-002).
    service = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    existing = _budget_row(organization_id, workflow_id, Decimal("100.00"))
    db = _Db(rows=[existing])
    _add_primary_workflow_scope(db, organization_id, workflow_id, existing.created_by)

    budget = service.upsert_budget(
        db,
        organization_id=organization_id,
        workflow_id=workflow_id,
        actor_id=uuid4(),
        monthly_budget_usd=Decimal("100.00"),
        is_enabled=False,
    )

    assert budget is existing
    assert budget.is_enabled is False
    audits = db.added_of(AuditLog)
    assert [audit.action for audit in audits] == [
        AuditAction.WORKFLOW_BUDGET_UPDATED
    ]
    assert audits[0].audit_metadata["is_enabled"] is False


def test_upsert_budget_noop_records_no_audit():
    service = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    existing = _budget_row(organization_id, workflow_id, Decimal("100.00"))
    db = _Db(rows=[existing])
    _add_primary_workflow_scope(db, organization_id, workflow_id, existing.created_by)

    budget = service.upsert_budget(
        db,
        organization_id=organization_id,
        workflow_id=workflow_id,
        actor_id=uuid4(),
        monthly_budget_usd=Decimal("100.00"),
        is_enabled=True,
    )

    assert budget is existing
    assert db.added_of(AuditLog) == []


def test_upsert_budget_create_race_falls_back_to_update():
    # 생성 경합에서 IntegrityError를 받으면 rollback 후 갱신으로 전환한다.
    # 어느 요청도 5xx로 실패하지 않는다 (BGT-REQ-005).
    service = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    competing = _budget_row(organization_id, workflow_id, Decimal("50.00"))
    db = _RacyDb(competing_row=competing)
    _add_primary_workflow_scope(db, organization_id, workflow_id, competing.created_by)

    budget = service.upsert_budget(
        db,
        organization_id=organization_id,
        workflow_id=workflow_id,
        actor_id=uuid4(),
        monthly_budget_usd=Decimal("100.00"),
        is_enabled=True,
    )

    assert budget is competing
    assert budget.monthly_budget_usd == Decimal("100.00")
    assert db.rollbacks >= 1
    assert [audit.action for audit in db.added_of(AuditLog)] == [
        AuditAction.WORKFLOW_BUDGET_UPDATED
    ]


def test_upsert_budget_create_race_rechecks_primary_after_rollback():
    service = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    competing = _budget_row(organization_id, workflow_id, Decimal("50.00"))
    db = _RacyPrimaryChangedDb(competing_row=competing)
    app, _workflow = _add_primary_workflow_scope(
        db,
        organization_id,
        workflow_id,
        competing.created_by,
    )
    db.app = app

    with pytest.raises(AppPrimaryChangedDuringMutationError):
        service.upsert_budget(
            db,
            organization_id=organization_id,
            workflow_id=workflow_id,
            actor_id=uuid4(),
            monthly_budget_usd=Decimal("100.00"),
            is_enabled=True,
        )

    assert competing.monthly_budget_usd == Decimal("50.00")
    assert db.added_of(AuditLog) == []


# --- admin summary/usage budget 블록 (BGT-REQ-020~021) ------------------------


def test_organization_summary_returns_budget_block_from_active_budgets():
    AdminUsageService = _admin_usage_service()
    organization_id = uuid4()
    at_risk_workflow_id = uuid4()
    normal_workflow_id = uuid4()
    disabled_workflow_id = uuid4()
    app_ids = {name: uuid4() for name in ("at_risk", "normal", "disabled")}
    db = _UsageDb(
        usage_logs=[
            _usage_log(
                organization_id,
                at_risk_workflow_id,
                total_cost=Decimal("95.000000"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                normal_workflow_id,
                total_cost=Decimal("10.000000"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                uuid4(),
                at_risk_workflow_id,
                total_cost=Decimal("1000.000000"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
        ],
        workflows=[
            SimpleNamespace(
                id=at_risk_workflow_id,
                app_id=app_ids["at_risk"],
                organization_id=organization_id,
            ),
            SimpleNamespace(
                id=normal_workflow_id,
                app_id=app_ids["normal"],
                organization_id=organization_id,
            ),
            SimpleNamespace(
                id=disabled_workflow_id,
                app_id=app_ids["disabled"],
                organization_id=organization_id,
            ),
        ],
        apps=[
            SimpleNamespace(
                id=app_ids["at_risk"],
                name="at_risk",
                workflow_id=at_risk_workflow_id,
                organization_id=organization_id,
            ),
            SimpleNamespace(
                id=app_ids["normal"],
                name="normal",
                workflow_id=normal_workflow_id,
                organization_id=organization_id,
            ),
            SimpleNamespace(
                id=app_ids["disabled"],
                name="disabled",
                workflow_id=disabled_workflow_id,
                organization_id=organization_id,
            ),
        ],
        budgets=[
            _budget_row(organization_id, at_risk_workflow_id, Decimal("100.00")),
            _budget_row(organization_id, normal_workflow_id, Decimal("100.00")),
            # 비활성 예산은 분모/분자 모두 제외 (BGT-REQ-010)
            _budget_row(
                organization_id,
                disabled_workflow_id,
                Decimal("100.00"),
                is_enabled=False,
            ),
        ],
    )

    summary = AdminUsageService.get_organization_summary(
        db,
        organization_id=organization_id,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=KST),
    )

    assert summary.budget is not None
    assert summary.budget.budgeted_workflow_count == 2
    assert summary.budget.at_risk_count == 1
    assert summary.budget.exceeded_count == 0
    assert summary.budget.ratio == pytest.approx(0.5)


def test_workflow_usage_items_include_current_month_budget_block():
    # budget 블록은 조회 기간 필터와 무관하게 항상 당월(KST) 기준이다 (BGT-REQ-020).
    AdminUsageService = _admin_usage_service()
    from apps.gateway.services.admin_usage_service import AdminUsagePeriod

    organization_id = uuid4()
    workflow_id = uuid4()
    app_id = uuid4()
    db = _UsageDb(
        usage_logs=[
            # 지난달(조회 기간 안) 비용 — total_cost에 반영
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("5.000000"),
                created_at=datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc),
            ),
            # 이번 달(조회 기간 밖) 비용 — budget 블록에 반영
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("95.000000"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                uuid4(),
                workflow_id,
                total_cost=Decimal("500.000000"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
        ],
        workflows=[
            SimpleNamespace(
                id=workflow_id, app_id=app_id, organization_id=organization_id
            )
        ],
        apps=[
            SimpleNamespace(
                id=app_id,
                name="예산 워크플로우",
                workflow_id=workflow_id,
                organization_id=organization_id,
            )
        ],
        budgets=[
            _budget_row(organization_id, workflow_id, Decimal("100.00")),
        ],
    )
    june = AdminUsagePeriod(
        start_at=datetime(2026, 6, 1, 0, 0, tzinfo=KST),
        end_at=datetime(2026, 7, 1, 0, 0, tzinfo=KST),
    )

    result = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=june,
        page=1,
        limit=20,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=KST),
    )

    item = result.items[0]
    assert item.total_cost == pytest.approx(5.0)
    assert item.budget is not None
    assert item.budget.monthly_budget_usd == pytest.approx(100.0)
    assert item.budget.current_month_cost == pytest.approx(95.0)
    assert item.budget.usage_ratio == pytest.approx(0.95)
    assert item.budget.status == "at_risk"


def test_workflow_usage_item_without_budget_returns_null_block():
    AdminUsageService = _admin_usage_service()
    from apps.gateway.services.admin_usage_service import AdminUsagePeriod

    organization_id = uuid4()
    workflow_id = uuid4()
    app_id = uuid4()
    db = _UsageDb(
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                total_cost=Decimal("1.000000"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
        ],
        workflows=[
            SimpleNamespace(
                id=workflow_id, app_id=app_id, organization_id=organization_id
            )
        ],
        apps=[
            SimpleNamespace(
                id=app_id,
                name="예산 없는 워크플로우",
                workflow_id=workflow_id,
                organization_id=organization_id,
            )
        ],
        budgets=[],
    )
    july = AdminUsagePeriod(
        start_at=datetime(2026, 7, 1, 0, 0, tzinfo=KST),
        end_at=datetime(2026, 8, 1, 0, 0, tzinfo=KST),
    )

    result = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=july,
        page=1,
        limit=20,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=KST),
    )

    assert result.items[0].budget is None


def test_workflow_usage_zero_usage_item_keeps_budget_block_with_normal_status():
    # 활성 예산 workflow는 당월 usage row가 없어도 목록에 0 row로 남고
    # budget 블록은 current_month_cost=0, usage_ratio=0, normal이다
    # (BGT-REQ-020, 예산 설정 진입 누락 방지).
    AdminUsageService = _admin_usage_service()
    from apps.gateway.services.admin_usage_service import AdminUsagePeriod

    organization_id = uuid4()
    workflow_id = uuid4()
    app_id = uuid4()
    db = _UsageDb(
        usage_logs=[],
        workflows=[
            SimpleNamespace(
                id=workflow_id, app_id=app_id, organization_id=organization_id
            )
        ],
        apps=[
            SimpleNamespace(
                id=app_id,
                name="예산만 있는 워크플로우",
                workflow_id=workflow_id,
                organization_id=organization_id,
            )
        ],
        budgets=[
            _budget_row(organization_id, workflow_id, Decimal("100.00")),
        ],
    )
    july = AdminUsagePeriod(
        start_at=datetime(2026, 7, 1, 0, 0, tzinfo=KST),
        end_at=datetime(2026, 8, 1, 0, 0, tzinfo=KST),
    )

    result = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=july,
        page=1,
        limit=20,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=KST),
    )

    assert result.total == 1
    item = result.items[0]
    assert item.workflow_id == workflow_id
    assert item.call_count == 0
    assert item.total_cost == 0
    assert item.budget is not None
    assert item.budget.monthly_budget_usd == pytest.approx(100.0)
    assert item.budget.current_month_cost == pytest.approx(0.0)
    assert item.budget.usage_ratio == pytest.approx(0.0)
    assert item.budget.status == "normal"


# --- fakes -------------------------------------------------------------------


class _UsageDb:
    """admin_usage_service의 fake 세션 패턴 (list 기반)."""

    def __init__(self, *, usage_logs, workflows=None, apps=None, budgets=None):
        self.usage_logs = usage_logs
        self.workflows = workflows or []
        self.apps = apps or []
        self.budgets = budgets or []


def _usage_log(
    organization_id,
    workflow_id,
    *,
    total_cost,
    created_at,
    runtime_surface=None,
    status="success",
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
    )


def _budget_row(
    organization_id,
    workflow_id,
    monthly_budget_usd,
    *,
    is_enabled=True,
    created_by=None,
):
    return WorkflowBudget(
        id=uuid4(),
        organization_id=organization_id,
        workflow_id=workflow_id,
        monthly_budget_usd=monthly_budget_usd,
        is_enabled=is_enabled,
        created_by=created_by or uuid4(),
    )


class _Query:
    def __init__(self, items):
        self.items = list(items)
        self.filters = []

    def filter(self, *expressions):
        self.filters.extend(expressions)
        return self

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(self, **kwargs):
        return self

    def populate_existing(self):
        return self

    def all(self):
        return [item for item in self.items if self._matches(item)]

    def first(self):
        return next(iter(self.all()), None)

    def count(self):
        return len(self.all())

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
    """모델 단위 dispatch + eq filter 평가만 지원하는 최소 세션 fake."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.executed = []

    def query(self, model, *rest):
        return _Query([row for row in self.rows if isinstance(row, model)])

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    def execute(self, statement):
        self.executed.append(statement)

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


class _RacyDb(_Db):
    """첫 flush에서 unique 제약 위반을 일으켜 생성 경합을 재현한다."""

    def __init__(self, competing_row):
        super().__init__(rows=[])
        self.competing_row = competing_row
        self._raised = False

    def flush(self):
        if not self._raised and any(
            isinstance(obj, WorkflowBudget) for obj in self.added
        ):
            self._raised = True
            # 경쟁 트랜잭션이 먼저 커밋된 상태를 재현
            self.added = [
                obj for obj in self.added if not isinstance(obj, WorkflowBudget)
            ]
            self.rows = [
                row for row in self.rows if not isinstance(row, WorkflowBudget)
            ]
            self.rows.append(self.competing_row)
            raise IntegrityError(
                "INSERT INTO workflow_budgets ...",
                {},
                Exception(
                    'duplicate key value violates unique constraint '
                    '"uq_workflow_budgets_workflow_id"'
                ),
            )


class _RacyPrimaryChangedDb(_RacyDb):
    def rollback(self):
        super().rollback()
        self.app.workflow_id = uuid4()


def _add_primary_workflow_scope(db, organization_id, workflow_id, actor_id):
    app = App(
        id=uuid4(),
        organization_id=organization_id,
        name="Budget primary scope",
        workflow_id=workflow_id,
        url_slug=f"budget-primary-{uuid4().hex}",
        auth_secret=None,
        created_by=actor_id,
    )
    workflow = Workflow(
        id=workflow_id,
        organization_id=organization_id,
        app_id=app.id,
        created_by=actor_id,
    )
    db.rows.extend([app, workflow])
    return app, workflow
