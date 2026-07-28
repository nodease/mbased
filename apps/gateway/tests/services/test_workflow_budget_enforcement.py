"""예산 초과 실행 차단 helper와 배포/스케줄 경로 계약 테스트.

TDD red phase: WorkflowBudgetService.ensure_workflow_budget_allows_execution이
아직 없으므로 실패해야 한다. docs/features/budget-management/test_cases.md의
"실행 차단 helper" / "실행 경로별 차단 연결" 계약을 검증한다.

- BGT-REQ-030: exceeded workflow는 dispatch 전에 차단
- BGT-REQ-031: 일관된 429 budget.exceeded, 응답에 금액 미노출
- BGT-REQ-033: 미설정은 집계 없이 통과(기존 경로 보존), 집계 실패는 fail-closed
- BGT-REQ-034: 판정 캐시 금지 — 매 호출 새로 집계
- BGT-REQ-041: 차단 audit은 policy.block + reason budget.exceeded + trigger mode
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.operators import eq

from apps.shared.audit.actions import AuditAction
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    app_auth_secret_verifier,
)
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.llm import LLMUsageLog
from apps.shared.db.models.workflow_budget import WorkflowBudget
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 7, 15, 9, 0, tzinfo=KST)


def _service():
    from apps.gateway.services.workflow_budget_service import WorkflowBudgetService

    return WorkflowBudgetService


def _ensure(db, workflow_id, **kwargs):
    service = _service()
    kwargs.setdefault("trigger_mode", "test")
    kwargs.setdefault("now", NOW)
    return service.ensure_workflow_budget_allows_execution(
        db, workflow_id=workflow_id, **kwargs
    )


# --- helper: 통과 경로 --------------------------------------------------------


def test_no_budget_allows_execution_without_cost_aggregation():
    # 예산 미설정 workflow는 집계 쿼리 없이 기존 경로 그대로 통과한다 (NFR-005).
    db = _ExplodingAggregationDb(rows=[])

    _ensure(db, uuid4())  # 예외 없음 = 집계를 시도하지 않았다는 뜻

    assert db.added_of(AuditLog) == []


@pytest.mark.parametrize(
    ("amount", "is_enabled"),
    [(Decimal("100.00"), False), (Decimal("0"), True), (Decimal("-1.00"), True)],
)
def test_inactive_budget_allows_execution_without_cost_aggregation(
    amount, is_enabled
):
    workflow_id = uuid4()
    db = _ExplodingAggregationDb(
        rows=[_budget_row(uuid4(), workflow_id, amount, is_enabled=is_enabled)]
    )

    _ensure(db, workflow_id)

    assert db.added_of(AuditLog) == []


@pytest.mark.parametrize(
    "current_cost",
    [Decimal("50.000000"), Decimal("95.000000"), Decimal("100.000000")],
)
def test_normal_at_risk_and_exact_100_percent_allow_execution(current_cost):
    # 정확히 100%는 at_risk이며 차단하지 않는다 (BGT-REQ-012/030).
    workflow_id = uuid4()
    db = _enforcement_db(
        budget=_budget_row(uuid4(), workflow_id, Decimal("100.00")),
        usage_logs=[_usage_log(workflow_id, current_cost)],
    )

    _ensure(db, workflow_id)

    assert db.added_of(AuditLog) == []


# --- helper: 차단 경로 --------------------------------------------------------


def test_exceeded_blocks_with_429_budget_exceeded_and_hides_amounts():
    workflow_id = uuid4()
    db = _enforcement_db(
        budget=_budget_row(uuid4(), workflow_id, Decimal("100.00")),
        usage_logs=[_usage_log(workflow_id, Decimal("100.010000"))],
    )

    with pytest.raises(HTTPException) as exc_info:
        _ensure(db, workflow_id)

    assert exc_info.value.status_code == 429
    detail = exc_info.value.detail
    assert detail["code"] == "budget.exceeded"
    # 응답 메시지에 예산/비용 금액 원문을 노출하지 않는다 (BGT-REQ-031).
    assert not any(char.isdigit() for char in detail["message"])


def test_block_records_policy_block_audit_with_actor_and_trigger_mode():
    organization_id = uuid4()
    workflow_id = uuid4()
    actor_id = uuid4()
    db = _enforcement_db(
        budget=_budget_row(organization_id, workflow_id, Decimal("100.00")),
        usage_logs=[_usage_log(workflow_id, Decimal("150.000000"))],
    )

    with pytest.raises(HTTPException):
        _ensure(db, workflow_id, trigger_mode="test", actor_id=actor_id)

    audits = db.added_of(AuditLog)
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == AuditAction.POLICY_BLOCK
    assert audit.target_type == "workflow"
    assert audit.target_id == str(workflow_id)
    assert audit.status == "failure"
    assert audit.actor_id == actor_id
    assert audit.audit_metadata["reason"] == "budget.exceeded"
    assert audit.audit_metadata["policy_reason"] == "budget.exceeded"
    assert audit.audit_metadata["trigger_mode"] == "test"
    assert audit.audit_metadata["organization_id"] == str(organization_id)
    # 요청은 실패해도 차단 audit은 커밋되어야 한다.
    assert db.commits >= 1


def test_anonymous_block_records_actorless_audit():
    # public(app) 실행은 인증 actor가 없다 (requirements Policies).
    workflow_id = uuid4()
    db = _enforcement_db(
        budget=_budget_row(uuid4(), workflow_id, Decimal("100.00")),
        usage_logs=[_usage_log(workflow_id, Decimal("150.000000"))],
    )

    with pytest.raises(HTTPException):
        _ensure(db, workflow_id, trigger_mode="app", actor_id=None)

    audits = db.added_of(AuditLog)
    assert len(audits) == 1
    assert audits[0].actor_id is None
    assert audits[0].audit_metadata["trigger_mode"] == "app"


def test_aggregation_failure_fails_closed():
    # 활성 예산 workflow에서 집계가 실패하면 실행을 차단한다 (BGT-REQ-033).
    workflow_id = uuid4()
    db = _ExplodingAggregationDb(
        rows=[_budget_row(uuid4(), workflow_id, Decimal("100.00"))]
    )

    with pytest.raises(HTTPException) as exc_info:
        _ensure(db, workflow_id)

    assert exc_info.value.status_code == 429
    audits = db.added_of(AuditLog)
    assert len(audits) == 1
    assert audits[0].action == AuditAction.POLICY_BLOCK
    assert audits[0].audit_metadata["reason"] == "budget.exceeded"
    assert db.commits >= 1


def test_unresolved_provider_attempt_makes_active_budget_unavailable():
    workflow_id = uuid4()
    organization_id = uuid4()
    db = _enforcement_db(
        budget=_budget_row(organization_id, workflow_id, Decimal("100.00")),
        provider_usage_operations=[
            _provider_usage_operation(
                organization_id=organization_id,
                workflow_id=workflow_id,
                state="outcome_unknown",
            )
        ],
    )

    decision = _service().evaluate_workflow_budget_execution(
        db,
        workflow_id=workflow_id,
        now=NOW,
    )

    assert decision.status == "unavailable"
    with pytest.raises(HTTPException) as exc_info:
        _ensure(db, workflow_id)
    assert exc_info.value.status_code == 429
    audits = db.added_of(AuditLog)
    assert len(audits) == 1
    assert audits[0].action == AuditAction.POLICY_BLOCK
    assert audits[0].audit_metadata["reason"] == "budget.exceeded"
    assert audits[0].audit_metadata["trigger_mode"] == "test"
    assert db.commits >= 1


def test_canonical_success_is_counted_once_when_projection_exists():
    workflow_id = uuid4()
    organization_id = uuid4()
    operation_id = uuid4()
    db = _enforcement_db(
        budget=_budget_row(organization_id, workflow_id, Decimal("100.00")),
        usage_logs=[
            _usage_log(
                workflow_id,
                Decimal("150.000000"),
                provider_usage_operation_id=operation_id,
            )
        ],
        provider_usage_operations=[
            _provider_usage_operation(
                organization_id=organization_id,
                workflow_id=workflow_id,
                state="succeeded",
                total_cost_microusd=150_000_000,
            ),
            _provider_usage_operation(
                organization_id=organization_id,
                workflow_id=workflow_id,
                state="succeeded",
                total_cost_microusd=25_000_000,
            ),
        ],
    )

    cost = _service().get_current_month_cost(
        db,
        workflow_id=workflow_id,
        organization_id=organization_id,
        now=NOW,
    )

    assert cost == Decimal("175")


def test_schedule_budget_decision_is_side_effect_free_and_distinguishes_unavailable():
    workflow_id = uuid4()
    db = _ExplodingAggregationDb(
        rows=[_budget_row(uuid4(), workflow_id, Decimal("100.00"))]
    )

    decision = _service().evaluate_workflow_budget_execution(
        db,
        workflow_id=workflow_id,
        now=NOW,
    )

    assert decision.status == "unavailable"
    assert db.added_of(AuditLog) == []
    assert db.commits == 0


def test_schedule_budget_decision_returns_blocked_without_audit_or_commit():
    workflow_id = uuid4()
    db = _enforcement_db(
        budget=_budget_row(uuid4(), workflow_id, Decimal("100.00")),
        usage_logs=[_usage_log(workflow_id, Decimal("150.000000"))],
    )

    decision = _service().evaluate_workflow_budget_execution(
        db,
        workflow_id=workflow_id,
        now=NOW,
    )

    assert decision.status == "blocked"
    assert db.added_of(AuditLog) == []
    assert db.commits == 0


def test_judgment_is_recomputed_on_every_call():
    # 판정 결과를 캐시하지 않는다 (BGT-REQ-034).
    workflow_id = uuid4()
    db = _enforcement_db(
        budget=_budget_row(uuid4(), workflow_id, Decimal("100.00")),
        usage_logs=[_usage_log(workflow_id, Decimal("50.000000"))],
    )

    _ensure(db, workflow_id)  # 50% — 통과

    db.usage_logs.append(_usage_log(workflow_id, Decimal("60.000000")))

    with pytest.raises(HTTPException) as exc_info:
        _ensure(db, workflow_id)  # 110% — 차단

    assert exc_info.value.status_code == 429


# --- 배포 실행 경로 (api/app) --------------------------------------------------


@pytest.mark.parametrize(
    ("trigger_mode", "require_auth", "auth_token"),
    [("api", True, "deploy-secret"), ("app", False, None)],
)
def test_run_deployment_blocks_exceeded_budget_before_dispatch(
    monkeypatch, trigger_mode, require_auth, auth_token
):
    from apps.gateway.services import deployment_service as deployment_module

    organization_id = uuid4()
    workflow_id = uuid4()
    deployment_type = (
        DeploymentType.API if trigger_mode == "api" else DeploymentType.CHATBOT
    )
    app_row, deployment_row = _deployed_app(
        workflow_id,
        organization_id,
        deployment_type=deployment_type,
    )
    db = _enforcement_db(
        budget=_budget_row(organization_id, workflow_id, Decimal("100.00")),
        usage_logs=[_usage_log(workflow_id, Decimal("150.000000"), now_utc=True)],
        extra_rows=[app_row, deployment_row],
    )
    monkeypatch.setattr(deployment_module, "celery_app", _DispatchGuard())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            deployment_module.DeploymentService.run_deployment(
                db=db,
                url_slug=app_row.url_slug,
                user_inputs={},
                trigger_mode=trigger_mode,
                runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
                auth_token=auth_token,
                require_auth=require_auth,
                **(
                    {
                        "allow_stateless_public_chatbot_compatibility": True,
                        "public_request_deadline_at": datetime.now(timezone.utc)
                        + timedelta(seconds=600),
                    }
                    if trigger_mode == "app"
                    else {}
                ),
            )
        )

    # 500(engine failed)이 아니라 429여야 한다 — 차단은 dispatch try 블록 밖이다.
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "budget.exceeded"
    audits = db.added_of(AuditLog)
    assert len(audits) == 1
    assert audits[0].audit_metadata["trigger_mode"] == trigger_mode
    assert audits[0].actor_id is None


# --- fakes -------------------------------------------------------------------


def _budget_row(organization_id, workflow_id, amount, *, is_enabled=True):
    return WorkflowBudget(
        id=uuid4(),
        organization_id=organization_id,
        workflow_id=workflow_id,
        monthly_budget_usd=amount,
        is_enabled=is_enabled,
        created_by=uuid4(),
    )


def _usage_log(
    workflow_id,
    total_cost,
    *,
    now_utc=False,
    provider_usage_operation_id=None,
):
    created_at = (
        datetime.now(timezone.utc)
        if now_utc
        else datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    )
    return SimpleNamespace(
        workflow_id=workflow_id,
        organization_id=None,
        total_cost=total_cost,
        created_at=created_at,
        prompt_tokens=0,
        completion_tokens=0,
        runtime_surface=None,
        status="success",
        provider_usage_operation_id=provider_usage_operation_id,
    )


def _provider_usage_operation(
    *,
    organization_id,
    workflow_id,
    state,
    total_cost_microusd=None,
):
    return SimpleNamespace(
        organization_id=organization_id,
        workflow_id=workflow_id,
        purpose="main_generation",
        state=state,
        provider_started_at=datetime(
            2026, 7, 10, 0, 0, tzinfo=timezone.utc
        ),
        prompt_tokens=0 if state == "succeeded" else None,
        completion_tokens=0 if state == "succeeded" else None,
        total_cost_microusd=total_cost_microusd,
    )


def _deployed_app(
    workflow_id,
    organization_id,
    *,
    deployment_type=DeploymentType.CHATBOT,
):
    deployment_id = uuid4()
    app_row = App(
        id=uuid4(),
        name="예산 초과 앱",
        url_slug=f"blocked-{uuid4().hex[:8]}",
        auth_secret=None,
        auth_secret_verifier=app_auth_secret_verifier("deploy-secret"),
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
        type=deployment_type,
        graph_snapshot={"nodes": [], "edges": []},
        is_active=True,
        created_by=uuid4(),
    )
    return app_row, deployment_row


def _enforcement_db(
    *,
    budget=None,
    usage_logs=None,
    provider_usage_operations=None,
    extra_rows=None,
):
    rows = list(extra_rows or [])
    if budget is not None:
        rows.append(budget)
    db = _Db(rows=rows)
    db.usage_logs = list(usage_logs or [])
    db.provider_usage_operations = list(provider_usage_operations or [])
    return db


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
        self.closed = False

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

    def close(self):
        self.closed = True

    def added_of(self, model):
        return [obj for obj in self.added if isinstance(obj, model)]


class _ExplodingAggregationDb(_Db):
    """usage 집계가 시도되면 터지는 세션. usage_logs 속성이 없어 query 경로를 탄다."""

    def query(self, model, *rest):
        if model is LLMUsageLog:
            raise RuntimeError("usage aggregation failed")
        return super().query(model, *rest)


class _DispatchGuard:
    """차단됐어야 할 실행이 Celery로 넘어가면 즉시 실패시킨다 (hang 방지)."""

    def send_task(self, *args, **kwargs):
        raise AssertionError(
            "budget-blocked run must not be dispatched to Celery"
        )
