from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query, Session


def _service():
    from apps.gateway.services.admin_usage_service import (
        AdminUsagePeriod,
        AdminUsageService,
    )

    return AdminUsageService, AdminUsagePeriod


def test_resolve_month_period_kst_returns_calendar_month_boundaries():
    AdminUsageService, _ = _service()

    period = AdminUsageService.resolve_month_period_kst(
        datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    )

    assert period.start_at == datetime(2026, 7, 1, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert period.end_at == datetime(2026, 8, 1, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def test_resolve_period_defaults_to_current_month_and_rejects_invalid_range():
    AdminUsageService, _ = _service()

    default_period = AdminUsageService.resolve_period(
        start_at=None,
        end_at=None,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    explicit_period = AdminUsageService.resolve_period(
        start_at=datetime(2026, 7, 2, 9, 30),
        end_at=datetime(2026, 7, 3, 18, 0),
        now=datetime(2026, 7, 15, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert default_period.start_at == datetime(
        2026, 7, 1, 0, 0, tzinfo=ZoneInfo("Asia/Seoul")
    )
    assert default_period.end_at == datetime(
        2026, 8, 1, 0, 0, tzinfo=ZoneInfo("Asia/Seoul")
    )
    # timezone 없는 query datetime은 KST 기준으로 해석한다.
    assert explicit_period.start_at == datetime(
        2026, 7, 2, 9, 30, tzinfo=ZoneInfo("Asia/Seoul")
    )
    assert explicit_period.end_at == datetime(
        2026, 7, 3, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")
    )

    with pytest.raises(HTTPException) as exc:
        AdminUsageService.resolve_period(
            start_at=datetime(2026, 7, 3, 18, 0),
            end_at=datetime(2026, 7, 3, 18, 0),
            now=datetime(2026, 7, 15, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

    assert exc.value.status_code == 400

    for kwargs in (
        {"start_at": datetime(2026, 7, 1, 0, 0), "end_at": None},
        {"start_at": None, "end_at": datetime(2026, 8, 1, 0, 0)},
    ):
        with pytest.raises(HTTPException) as exc:
            AdminUsageService.resolve_period(
                **kwargs,
                now=datetime(2026, 7, 15, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            )

        assert exc.value.status_code == 400


def test_coalesce_cost_preserves_precision_and_normalizes_none():
    AdminUsageService, _ = _service()

    assert AdminUsageService.coalesce_cost(None) == Decimal("0")
    assert AdminUsageService.coalesce_cost(Decimal("12.345678")) == Decimal(
        "12.345678"
    )


def test_aggregate_workflow_usage_groups_sums_sorts_scopes_and_uses_app_name():
    AdminUsageService, AdminUsagePeriod = _service()
    organization_id = uuid4()
    other_organization_id = uuid4()
    expensive_workflow_id = uuid4()
    cheap_workflow_id = uuid4()
    other_workflow_id = uuid4()
    expensive_app_id = uuid4()
    cheap_app_id = uuid4()
    other_app_id = uuid4()
    start_at = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    end_at = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    db = _UsageSession(
        usage_logs=[
            _usage_log(
                organization_id,
                expensive_workflow_id,
                prompt_tokens=100,
                completion_tokens=20,
                total_cost=Decimal("1.100000"),
                created_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                expensive_workflow_id,
                prompt_tokens=50,
                completion_tokens=10,
                total_cost=None,
                created_at=datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                cheap_workflow_id,
                prompt_tokens=5,
                completion_tokens=3,
                total_cost=Decimal("0.123456"),
                created_at=datetime(2026, 7, 3, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                other_organization_id,
                other_workflow_id,
                prompt_tokens=999,
                completion_tokens=999,
                total_cost=Decimal("99.000000"),
                created_at=datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                expensive_workflow_id,
                prompt_tokens=999,
                completion_tokens=999,
                total_cost=Decimal("9.000000"),
                created_at=end_at,
            ),
        ],
        workflows=[
            _workflow(expensive_workflow_id, expensive_app_id, organization_id),
            _workflow(cheap_workflow_id, cheap_app_id, organization_id),
            _workflow(other_workflow_id, other_app_id, other_organization_id),
        ],
        apps=[
            _app(
                expensive_app_id,
                "비싼 워크플로우",
                workflow_id=expensive_workflow_id,
                organization_id=organization_id,
            ),
            _app(
                cheap_app_id,
                "저렴한 워크플로우",
                workflow_id=cheap_workflow_id,
                organization_id=organization_id,
            ),
            _app(
                other_app_id,
                "다른 조직 워크플로우",
                workflow_id=other_workflow_id,
                organization_id=other_organization_id,
            ),
        ],
    )

    result = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=AdminUsagePeriod(start_at=start_at, end_at=end_at),
        page=1,
        limit=20,
    )
    second_page = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=AdminUsagePeriod(start_at=start_at, end_at=end_at),
        page=2,
        limit=1,
    )

    assert result.total == 2
    assert [
        (
            item.workflow_id,
            item.workflow_name,
            item.prompt_tokens,
            item.completion_tokens,
            item.call_count,
            item.total_cost,
        )
        for item in result.items
    ] == [
        (
            expensive_workflow_id,
            "비싼 워크플로우",
            150,
            30,
            2,
            1.1,
        ),
        (
            cheap_workflow_id,
            "저렴한 워크플로우",
            5,
            3,
            1,
            0.123456,
        ),
    ]
    assert isinstance(result.items[0].total_cost, float)
    assert [item.workflow_id for item in second_page.items] == [cheap_workflow_id]


def test_aggregate_workflow_usage_includes_zero_usage_primary_workflows():
    # 목록 기준은 usage row 존재 여부가 아니라 organization scope 안의
    # App primary workflow(apps.workflow_id) 전체다 (FR-012, BGT-REQ-020).
    AdminUsageService, AdminUsagePeriod = _service()
    organization_id = uuid4()
    other_organization_id = uuid4()
    used_workflow_id = uuid4()
    idle_workflow_id = uuid4()
    other_workflow_id = uuid4()
    used_app_id = uuid4()
    idle_app_id = uuid4()
    other_app_id = uuid4()
    db = _UsageSession(
        usage_logs=[
            _usage_log(
                organization_id,
                used_workflow_id,
                prompt_tokens=100,
                completion_tokens=20,
                total_cost=Decimal("1.100000"),
                created_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            ),
        ],
        workflows=[
            _workflow(used_workflow_id, used_app_id, organization_id),
            _workflow(idle_workflow_id, idle_app_id, organization_id),
            _workflow(other_workflow_id, other_app_id, other_organization_id),
        ],
        apps=[
            _app(
                used_app_id,
                "사용량 있는 워크플로우",
                workflow_id=used_workflow_id,
                organization_id=organization_id,
            ),
            _app(
                idle_app_id,
                "사용량 없는 워크플로우",
                workflow_id=idle_workflow_id,
                organization_id=organization_id,
            ),
            _app(
                other_app_id,
                "다른 조직 워크플로우",
                workflow_id=other_workflow_id,
                organization_id=other_organization_id,
            ),
        ],
    )

    result = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=AdminUsagePeriod(
            start_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        ),
        page=1,
        limit=20,
    )

    # 기간 안에 usage row가 없는 primary workflow도 응답 item으로 포함한다.
    assert result.total == 2
    assert [item.workflow_id for item in result.items] == [
        used_workflow_id,
        idle_workflow_id,
    ]
    idle_item = result.items[1]
    assert idle_item.workflow_name == "사용량 없는 워크플로우"
    assert idle_item.prompt_tokens == 0
    assert idle_item.completion_tokens == 0
    assert idle_item.call_count == 0
    assert idle_item.total_cost == 0
    # 조직 B의 primary workflow는 usage 유무와 무관하게 포함하지 않는다.
    assert other_workflow_id not in {item.workflow_id for item in result.items}


def test_aggregate_workflow_usage_includes_null_organization_usage_for_primary_workflow():
    # admin usage 목록은 App primary workflow로 organization scope를 제한하므로,
    # legacy/migration usage처럼 organization_id가 NULL이어도 같은 workflow 비용은
    # 예산 판정 경로와 동일하게 합산해야 한다.
    AdminUsageService, AdminUsagePeriod = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    app_id = uuid4()
    db = _UsageSession(
        usage_logs=[
            _usage_log(
                None,
                workflow_id,
                prompt_tokens=100,
                completion_tokens=20,
                total_cost=Decimal("1.250000"),
                created_at=datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc),
            )
        ],
        workflows=[_workflow(workflow_id, app_id, organization_id)],
        apps=[
            _app(
                app_id,
                "NULL organization usage 워크플로우",
                workflow_id=workflow_id,
                organization_id=organization_id,
            )
        ],
    )

    result = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=AdminUsagePeriod(
            start_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        ),
        page=1,
        limit=20,
    )

    item = result.items[0]
    assert item.workflow_id == workflow_id
    assert item.prompt_tokens == 100
    assert item.completion_tokens == 20
    assert item.call_count == 1
    assert item.total_cost == pytest.approx(1.25)


def test_admin_usage_counts_canonical_success_once_and_exposes_unresolved():
    AdminUsageService, AdminUsagePeriod = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    app_id = uuid4()
    operation_id = uuid4()
    provider_started_at = datetime(2026, 7, 5, tzinfo=timezone.utc)
    db = _UsageSession(
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                prompt_tokens=2,
                completion_tokens=3,
                total_cost=Decimal("0.250000"),
                created_at=provider_started_at,
            ),
            _usage_log(
                organization_id,
                workflow_id,
                prompt_tokens=11,
                completion_tokens=13,
                total_cost=Decimal("3.000000"),
                created_at=provider_started_at,
                provider_usage_operation_id=operation_id,
            ),
        ],
        provider_usage_operations=[
            _provider_usage_operation(
                organization_id=organization_id,
                workflow_id=workflow_id,
                state="succeeded",
                provider_started_at=provider_started_at,
                prompt_tokens=17,
                completion_tokens=19,
                total_cost_microusd=4_000_000,
            ),
            _provider_usage_operation(
                organization_id=organization_id,
                workflow_id=workflow_id,
                state="outcome_unknown",
                provider_started_at=provider_started_at,
            ),
        ],
        workflows=[_workflow(workflow_id, app_id, organization_id)],
        apps=[
            _app(
                app_id,
                "canonical workflow",
                workflow_id=workflow_id,
                organization_id=organization_id,
            )
        ],
    )
    period = AdminUsagePeriod(
        start_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    result = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=period,
    )
    summary = AdminUsageService.get_organization_summary(
        db,
        organization_id=organization_id,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    item = result.items[0]
    assert item.prompt_tokens == 19
    assert item.completion_tokens == 22
    assert item.call_count == 2
    assert item.total_cost == pytest.approx(4.25)
    assert item.usage_data_complete is False
    assert item.unresolved_provider_call_count == 1
    assert result.usage_data_complete is False
    assert result.unresolved_provider_call_count == 1
    assert summary.total_cost == pytest.approx(4.25)
    assert summary.usage_data_complete is False
    assert summary.unresolved_provider_call_count == 1


def test_aggregate_workflow_usage_excludes_explicit_cross_organization_usage():
    AdminUsageService, AdminUsagePeriod = _service()
    organization_id = uuid4()
    other_organization_id = uuid4()
    workflow_id = uuid4()
    cross_only_workflow_id = uuid4()
    app_id = uuid4()
    cross_only_app_id = uuid4()
    created_at = datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)
    db = _UsageSession(
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                prompt_tokens=10,
                completion_tokens=2,
                total_cost=Decimal("1.000000"),
                created_at=created_at,
            ),
            _usage_log(
                None,
                workflow_id,
                prompt_tokens=20,
                completion_tokens=3,
                total_cost=Decimal("2.000000"),
                created_at=created_at,
            ),
            _usage_log(
                other_organization_id,
                workflow_id,
                prompt_tokens=999,
                completion_tokens=999,
                total_cost=Decimal("99.000000"),
                created_at=created_at,
            ),
            _usage_log(
                other_organization_id,
                cross_only_workflow_id,
                prompt_tokens=999,
                completion_tokens=999,
                total_cost=Decimal("99.000000"),
                created_at=created_at,
            ),
        ],
        workflows=[
            _workflow(workflow_id, app_id, organization_id),
            _workflow(
                cross_only_workflow_id,
                cross_only_app_id,
                organization_id,
            ),
        ],
        apps=[
            _app(
                app_id,
                "조직 범위 워크플로우",
                workflow_id=workflow_id,
                organization_id=organization_id,
            ),
            _app(
                cross_only_app_id,
                "타 조직 사용량만 있는 워크플로우",
                workflow_id=cross_only_workflow_id,
                organization_id=organization_id,
            ),
        ],
    )

    result = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=AdminUsagePeriod(
            start_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        ),
    )

    item = result.items[0]
    assert item.prompt_tokens == 30
    assert item.completion_tokens == 5
    assert item.call_count == 2
    assert item.total_cost == pytest.approx(3.0)
    assert result.total == 2
    cross_only_item = next(
        item for item in result.items if item.workflow_id == cross_only_workflow_id
    )
    assert cross_only_item.prompt_tokens == 0
    assert cross_only_item.completion_tokens == 0
    assert cross_only_item.call_count == 0
    assert cross_only_item.total_cost == 0


def test_aggregate_workflow_usage_excludes_app_workflow_organization_mismatch():
    AdminUsageService, AdminUsagePeriod = _service()
    organization_id = uuid4()
    other_organization_id = uuid4()
    valid_workflow_id = uuid4()
    mismatched_workflow_id = uuid4()
    missing_workflow_id = uuid4()
    valid_app_id = uuid4()
    mismatched_app_id = uuid4()
    missing_app_id = uuid4()
    created_at = datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)
    db = _UsageSession(
        usage_logs=[
            _usage_log(
                organization_id,
                valid_workflow_id,
                prompt_tokens=10,
                completion_tokens=2,
                total_cost=Decimal("1.000000"),
                created_at=created_at,
            ),
            _usage_log(
                organization_id,
                mismatched_workflow_id,
                prompt_tokens=999,
                completion_tokens=999,
                total_cost=Decimal("99.000000"),
                created_at=created_at,
            ),
        ],
        workflows=[
            _workflow(valid_workflow_id, valid_app_id, organization_id),
            _workflow(
                mismatched_workflow_id,
                mismatched_app_id,
                other_organization_id,
            ),
        ],
        apps=[
            _app(
                valid_app_id,
                "정상 워크플로우",
                workflow_id=valid_workflow_id,
                organization_id=organization_id,
            ),
            _app(
                mismatched_app_id,
                "불일치 워크플로우",
                workflow_id=mismatched_workflow_id,
                organization_id=organization_id,
            ),
            _app(
                missing_app_id,
                "누락 워크플로우",
                workflow_id=missing_workflow_id,
                organization_id=organization_id,
            ),
        ],
    )

    result = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=AdminUsagePeriod(
            start_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        ),
    )

    assert result.total == 1
    assert [item.workflow_id for item in result.items] == [valid_workflow_id]


def test_aggregate_workflow_usage_sql_keeps_scope_guards_in_join_conditions(
    monkeypatch,
):
    from apps.gateway.services.admin_usage_service import (
        _aggregate_workflow_usage_query,
    )

    _, AdminUsagePeriod = _service()
    organization_id = uuid4()
    captured_sql = {}

    def capture_count(query):
        captured_sql["count"] = str(
            query.statement.compile(dialect=postgresql.dialect())
        )
        return 0

    def capture_rows(query):
        captured_sql["rows"] = str(
            query.statement.compile(dialect=postgresql.dialect())
        )
        return []

    monkeypatch.setattr(Query, "count", capture_count)
    monkeypatch.setattr(Query, "all", capture_rows)
    db = Session()

    try:
        result = _aggregate_workflow_usage_query(
            db,
            organization_id=organization_id,
            period=AdminUsagePeriod(
                start_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
                end_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
            ),
            page=1,
            limit=20,
            budget_now=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
        )
    finally:
        db.close()

    assert result.total == 0
    sql = " ".join(captured_sql["count"].split())
    assert "JOIN workflows ON workflows.id = apps.workflow_id" in sql
    assert "workflows.organization_id" in sql
    assert "LEFT OUTER JOIN (SELECT" in sql
    assert "llm_usage_logs.provider_usage_operation_id IS NULL" in sql
    assert "llm_usage_logs.organization_id" in sql
    assert "llm_usage_logs.organization_id IS NULL" in sql
    assert "provider_usage_operations.organization_id" in sql
    assert "provider_usage_operations.provider_started_at" in sql
    assert "provider_usage_operations.state" in sql


def test_sql_usage_response_keeps_global_incomplete_signal_on_empty_page(
    monkeypatch,
):
    from apps.gateway.services.admin_usage_service import (
        _aggregate_workflow_usage_query,
    )

    _, AdminUsagePeriod = _service()
    organization_id = uuid4()
    captured_sql = {}

    monkeypatch.setattr(Query, "count", lambda _query: 1)
    monkeypatch.setattr(Query, "all", lambda _query: [])

    def capture_scalar(query):
        captured_sql["unresolved"] = str(
            query.statement.compile(dialect=postgresql.dialect())
        )
        return 2

    monkeypatch.setattr(Query, "scalar", capture_scalar)
    db = Session()
    try:
        result = _aggregate_workflow_usage_query(
            db,
            organization_id=organization_id,
            period=AdminUsagePeriod(
                start_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
                end_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
            ),
            page=2,
            limit=20,
            budget_now=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
        )
    finally:
        db.close()

    assert result.items == []
    assert result.usage_data_complete is False
    assert result.unresolved_provider_call_count == 2
    sql = " ".join(captured_sql["unresolved"].split())
    assert "sum(coalesce" in sql.lower()
    assert "provider_usage_operations" in sql


def test_aggregate_workflow_usage_total_counts_primary_workflows_for_page_slice():
    # total은 usage row 보유 workflow 수가 아니라 응답 대상
    # App primary workflow 전체 건수이고 items는 page slice다.
    AdminUsageService, AdminUsagePeriod = _service()
    organization_id = uuid4()
    workflow_ids = [uuid4() for _ in range(3)]
    app_ids = [uuid4() for _ in range(3)]
    db = _UsageSession(
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_ids[0],
                prompt_tokens=10,
                completion_tokens=2,
                total_cost=Decimal("5.000000"),
                created_at=datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc),
            ),
        ],
        workflows=[
            _workflow(workflow_id, app_id, organization_id)
            for workflow_id, app_id in zip(workflow_ids, app_ids)
        ],
        apps=[
            _app(
                app_id,
                f"워크플로우 {index}",
                workflow_id=workflow_id,
                organization_id=organization_id,
            )
            for index, (workflow_id, app_id) in enumerate(zip(workflow_ids, app_ids))
        ],
    )
    period = AdminUsagePeriod(
        start_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
    )

    first_page = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=period,
        page=1,
        limit=2,
    )
    second_page = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=period,
        page=2,
        limit=2,
    )

    assert first_page.total == 3
    assert second_page.total == 3
    assert len(first_page.items) == 2
    assert len(second_page.items) == 1
    assert {
        item.workflow_id for item in first_page.items + second_page.items
    } == set(workflow_ids)


def test_aggregate_workflow_usage_sorts_cost_ties_by_name_then_id():
    # 정렬은 total_cost 내림차순, 동률은 workflow 이름 오름차순,
    # 같은 이름은 workflow_id 오름차순으로 안정 정렬한다 (FR-012).
    AdminUsageService, AdminUsagePeriod = _service()
    organization_id = uuid4()
    top_workflow_id = UUID(int=4)
    tie_name_second_id = UUID(int=2)
    tie_name_first_id = UUID(int=1)
    tie_later_name_id = UUID(int=3)
    workflow_apps = [
        # (workflow_id, app_id, app name)
        (tie_later_name_id, UUID(int=13), "나 워크플로우"),
        (tie_name_second_id, UUID(int=12), "가 워크플로우"),
        (tie_name_first_id, UUID(int=11), "가 워크플로우"),
        (top_workflow_id, UUID(int=14), "다 워크플로우"),
    ]
    db = _UsageSession(
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                prompt_tokens=10,
                completion_tokens=2,
                total_cost=total_cost,
                created_at=datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc),
            )
            for workflow_id, total_cost in [
                (tie_later_name_id, Decimal("1.000000")),
                (tie_name_second_id, Decimal("1.000000")),
                (tie_name_first_id, Decimal("1.000000")),
                (top_workflow_id, Decimal("2.000000")),
            ]
        ],
        workflows=[
            _workflow(workflow_id, app_id, organization_id)
            for workflow_id, app_id, _ in workflow_apps
        ],
        apps=[
            _app(
                app_id,
                name,
                workflow_id=workflow_id,
                organization_id=organization_id,
            )
            for workflow_id, app_id, name in workflow_apps
        ],
    )

    result = AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=AdminUsagePeriod(
            start_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        ),
        page=1,
        limit=20,
    )

    assert [item.workflow_id for item in result.items] == [
        top_workflow_id,
        tie_name_first_id,
        tie_name_second_id,
        tie_later_name_id,
    ]


def test_get_organization_summary_sums_current_month_costs_with_null_as_zero():
    AdminUsageService, _ = _service()
    organization_id = uuid4()
    other_organization_id = uuid4()
    workflow_id = uuid4()
    other_workflow_id = uuid4()
    app_id = uuid4()
    other_app_id = uuid4()
    db = _UsageSession(
        usage_logs=[
            _usage_log(
                organization_id,
                workflow_id,
                prompt_tokens=100,
                completion_tokens=20,
                total_cost=Decimal("1.100000"),
                created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                workflow_id,
                prompt_tokens=50,
                completion_tokens=10,
                total_cost=None,
                created_at=datetime(2026, 7, 11, 0, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                organization_id,
                workflow_id,
                prompt_tokens=5,
                completion_tokens=3,
                total_cost=Decimal("0.234567"),
                created_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
                runtime_surface="agent_builder_intent",
            ),
            _usage_log(
                None,
                workflow_id,
                prompt_tokens=2,
                completion_tokens=1,
                total_cost=Decimal("0.400000"),
                created_at=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                other_organization_id,
                workflow_id,
                prompt_tokens=999,
                completion_tokens=999,
                total_cost=Decimal("98.000000"),
                created_at=datetime(2026, 7, 12, 2, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                None,
                other_workflow_id,
                prompt_tokens=999,
                completion_tokens=999,
                total_cost=Decimal("97.000000"),
                created_at=datetime(2026, 7, 12, 3, 0, tzinfo=timezone.utc),
            ),
            _usage_log(
                other_organization_id,
                other_workflow_id,
                prompt_tokens=999,
                completion_tokens=999,
                total_cost=Decimal("99.000000"),
                created_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
            ),
        ],
        workflows=[
            _workflow(workflow_id, app_id, organization_id),
            _workflow(other_workflow_id, other_app_id, other_organization_id),
        ],
        apps=[
            _app(
                app_id,
                "요약 워크플로우",
                workflow_id=workflow_id,
                organization_id=organization_id,
            ),
            _app(
                other_app_id,
                "다른 조직 워크플로우",
                workflow_id=other_workflow_id,
                organization_id=other_organization_id,
            ),
        ],
    )

    summary = AdminUsageService.get_organization_summary(
        db,
        organization_id=organization_id,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert summary.month == "2026-07"
    assert summary.total_cost == pytest.approx(1.734567)
    assert summary.workflow_execution_cost == pytest.approx(1.5)
    assert summary.agent_builder_cost == pytest.approx(0.234567)
    # 예산 feature(FR-051) 확정 전에는 budget 블록을 None으로 반환한다.
    assert summary.budget is None


def test_get_organization_summary_uses_kst_month_boundaries():
    AdminUsageService, _ = _service()
    organization_id = uuid4()
    workflow_id = uuid4()
    app_id = uuid4()
    db = _UsageSession(
        usage_logs=[
            # KST 2026-07-01 00:30 저장분 — 7월 집계에 포함
            _usage_log(
                organization_id,
                workflow_id,
                prompt_tokens=10,
                completion_tokens=2,
                total_cost=Decimal("0.500000"),
                created_at=datetime(2026, 6, 30, 15, 30, tzinfo=timezone.utc),
            ),
            # KST 2026-06-30 23:59 저장분 — 6월 집계로 제외
            _usage_log(
                organization_id,
                workflow_id,
                prompt_tokens=10,
                completion_tokens=2,
                total_cost=Decimal("7.000000"),
                created_at=datetime(2026, 6, 30, 14, 59, tzinfo=timezone.utc),
            ),
            # KST 2026-08-01 00:00 저장분 — [start, end) 끝 경계라 제외
            _usage_log(
                organization_id,
                workflow_id,
                prompt_tokens=10,
                completion_tokens=2,
                total_cost=Decimal("9.000000"),
                created_at=datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc),
            ),
        ],
        workflows=[_workflow(workflow_id, app_id, organization_id)],
        apps=[
            _app(
                app_id,
                "경계 워크플로우",
                workflow_id=workflow_id,
                organization_id=organization_id,
            )
        ],
    )

    summary = AdminUsageService.get_organization_summary(
        db,
        organization_id=organization_id,
        now=datetime(2026, 7, 15, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert summary.month == "2026-07"
    assert summary.total_cost == pytest.approx(0.5)


class _UsageSession:
    def __init__(
        self,
        *,
        usage_logs,
        workflows,
        apps,
        provider_usage_operations=None,
    ):
        self.usage_logs = usage_logs
        self.workflows = workflows
        self.apps = apps
        self.provider_usage_operations = provider_usage_operations or []


def _usage_log(
    organization_id,
    workflow_id,
    *,
    prompt_tokens,
    completion_tokens,
    total_cost,
    created_at,
    runtime_surface=None,
    provider_usage_operation_id=None,
):
    return SimpleNamespace(
        organization_id=organization_id,
        workflow_id=workflow_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_cost=total_cost,
        created_at=created_at,
        runtime_surface=runtime_surface,
        status="success",
        provider_usage_operation_id=provider_usage_operation_id,
    )


def _provider_usage_operation(
    *,
    organization_id,
    workflow_id,
    state,
    provider_started_at,
    prompt_tokens=None,
    completion_tokens=None,
    total_cost_microusd=None,
):
    return SimpleNamespace(
        organization_id=organization_id,
        workflow_id=workflow_id,
        purpose="main_generation",
        state=state,
        provider_started_at=provider_started_at,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_cost_microusd=total_cost_microusd,
    )


def _workflow(workflow_id, app_id, organization_id):
    return SimpleNamespace(
        id=workflow_id,
        app_id=app_id,
        organization_id=organization_id,
    )


def _app(app_id, name, *, workflow_id=None, organization_id=None):
    return SimpleNamespace(
        id=app_id,
        name=name,
        workflow_id=workflow_id,
        organization_id=organization_id,
    )
