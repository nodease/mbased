from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# admin_usage_service는 이 모듈을 함수 안에서 지연 import한다 (순환 의존 차단).
# top-level import는 이 방향(budget → admin_usage)만 허용한다.
from apps.gateway.services.admin_usage_service import (
    KST,
    AdminUsageService,
)
from apps.gateway.services.audit_records import add_action_audit
from apps.gateway.services.app_lifecycle_lock import (
    AppPrimaryChangedDuringMutationError,
    lock_app_for_workflow_mutation,
)
from apps.gateway.services.workflow_budget_lock import lock_workflow_budget_scope
from apps.shared.domain.workflow_budget import BudgetExecutionDecision
from apps.shared.services.workflow_budget_execution import (
    evaluate_workflow_budget_execution,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_budget import WorkflowBudget
from apps.shared.services.provider_usage_cost_read_model import (
    ProviderUsageAggregate,
    read_workflow_usage_aggregate,
)

BUDGET_AT_RISK_RATIO = Decimal("0.8")
BUDGET_EXCEEDED_RATIO = Decimal("1.0")


class WorkflowBudgetScopeUnavailableError(RuntimeError):
    """The canonical Workflow/App scope disappeared before a budget mutation."""


class WorkflowBudgetService:
    """Workflow monthly LLM budget helpers."""

    @staticmethod
    def classify_budget_usage(
        current_cost: Any,
        monthly_budget_usd: Any,
        is_enabled: bool = True,
    ) -> str | None:
        budget = _active_budget_amount(monthly_budget_usd, is_enabled)
        if budget is None:
            return None

        ratio = _to_decimal(current_cost) / budget
        if ratio > BUDGET_EXCEEDED_RATIO:
            return "exceeded"
        if ratio >= BUDGET_AT_RISK_RATIO:
            return "at_risk"
        return "normal"

    @staticmethod
    def get_current_month_cost(
        db: Session,
        workflow_id: Any,
        now: datetime,
        organization_id: Any = None,
    ) -> Decimal:
        return WorkflowBudgetService.get_current_month_usage(
            db,
            workflow_id=workflow_id,
            now=now,
            organization_id=organization_id,
        ).total_cost

    @staticmethod
    def get_current_month_usage(
        db: Session,
        workflow_id: Any,
        now: datetime,
        organization_id: Any = None,
    ) -> ProviderUsageAggregate:
        period = AdminUsageService.resolve_month_period_kst(now)
        return read_workflow_usage_aggregate(
            db,
            workflow_id=workflow_id,
            organization_id=organization_id,
            start_at=period.start_at,
            end_at=period.end_at,
        )

    @staticmethod
    def has_active_budget(
        db: Session,
        *,
        workflow_id: Any,
        organization_id: Any,
    ) -> bool:
        lock_workflow_budget_scope(
            db,
            workflow_id=workflow_id,
            organization_id=organization_id,
        )
        budget = _find_budget(db, workflow_id, organization_id=organization_id)
        return budget is not None and _active_budget_amount(
            budget.monthly_budget_usd,
            budget.is_enabled,
        ) is not None

    @staticmethod
    def upsert_budget(
        db: Session,
        organization_id: Any,
        workflow_id: Any,
        actor_id: Any,
        monthly_budget_usd: Any,
        is_enabled: bool,
    ) -> WorkflowBudget:
        amount = _to_decimal(monthly_budget_usd)
        enabled = bool(is_enabled)
        _lock_budget_mutation_scope(
            db,
            workflow_id=workflow_id,
            organization_id=organization_id,
        )
        existing = _find_budget(db, workflow_id, organization_id=organization_id)

        if existing is None:
            budget = WorkflowBudget(
                id=uuid.uuid4(),
                organization_id=organization_id,
                workflow_id=workflow_id,
                monthly_budget_usd=amount,
                is_enabled=enabled,
                created_by=actor_id,
            )
            db.add(budget)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                # rollback releases both the App row lock and the advisory lock.
                # Rebuild the full scope before touching the competing row.
                _lock_budget_mutation_scope(
                    db,
                    workflow_id=workflow_id,
                    organization_id=organization_id,
                )
                existing = _find_budget(
                    db, workflow_id, organization_id=organization_id
                )
                if existing is None:
                    raise
                return _update_budget(
                    db,
                    existing,
                    actor_id=actor_id,
                    monthly_budget_usd=amount,
                    is_enabled=enabled,
                )

            _add_budget_audit(
                db,
                AuditAction.WORKFLOW_BUDGET_CREATED,
                budget,
                actor_id=actor_id,
            )
            db.commit()
            db.refresh(budget)
            return budget

        return _update_budget(
            db,
            existing,
            actor_id=actor_id,
            monthly_budget_usd=amount,
            is_enabled=enabled,
        )

    @staticmethod
    def evaluate_workflow_budget_execution(
        db: Session,
        *,
        workflow_id: Any,
        now: datetime | None = None,
    ) -> BudgetExecutionDecision:
        """Return a side-effect-free budget decision for caller-owned transactions."""
        return evaluate_workflow_budget_execution(
            db,
            workflow_id=workflow_id,
            now=now or datetime.now(KST),
        )

    @staticmethod
    def ensure_workflow_budget_allows_execution(
        db: Session,
        *,
        workflow_id: Any,
        trigger_mode: str,
        actor_id: Any = None,
        now: datetime | None = None,
    ) -> None:
        """예산 초과 workflow의 실행을 dispatch 전에 차단한다 (BGT-REQ-030~035).

        활성 예산이 없는 workflow는 집계 없이 통과한다 (기존 경로 보존).
        판정은 매 호출 새로 집계하며 (캐시 금지), 차단 audit은 요청 실패와
        무관하게 커밋한다.
        """
        decision = WorkflowBudgetService.evaluate_workflow_budget_execution(
            db,
            workflow_id=workflow_id,
            now=now,
        )
        if decision.status == "allowed":
            return

        normalized_id = _normalize_workflow_id(workflow_id)
        budget = _find_budget(db, normalized_id)
        if budget is None:
            raise _budget_exceeded_error()

        add_action_audit(
            db,
            AuditAction.POLICY_BLOCK,
            actor_id=actor_id,
            target_type="workflow",
            target_id=normalized_id,
            organization_id=budget.organization_id,
            metadata={
                "reason": "budget.exceeded",
                "policy_reason": "budget.exceeded",
                "trigger_mode": trigger_mode,
            },
            status="failure",
        )
        db.commit()
        raise _budget_exceeded_error()


class WorkflowBudgetDecisionAdapter:
    def __init__(self, db: Session) -> None:
        self.db = db

    def evaluate(
        self,
        *,
        workflow_id: uuid.UUID | None,
        now: datetime,
    ) -> BudgetExecutionDecision:
        return WorkflowBudgetService.evaluate_workflow_budget_execution(
            self.db,
            workflow_id=workflow_id,
            now=now,
        )


def _budget_exceeded_error() -> HTTPException:
    # 응답 메시지에 예산/비용 금액을 노출하지 않는다 (BGT-REQ-031).
    return HTTPException(
        status_code=429,
        detail={
            "code": "budget.exceeded",
            "message": "Workflow monthly budget exceeded.",
        },
    )


def _normalize_workflow_id(value: Any):
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return value


def _lock_budget_mutation_scope(
    db: Session,
    *,
    workflow_id: Any,
    organization_id: Any,
) -> None:
    workflow = (
        db.query(Workflow)
        .filter(
            Workflow.id == workflow_id,
            Workflow.organization_id == organization_id,
        )
        .populate_existing()
        .first()
    )
    if workflow is None:
        raise WorkflowBudgetScopeUnavailableError
    app = lock_app_for_workflow_mutation(
        db,
        app_id=workflow.app_id,
        workflow_id=workflow_id,
        organization_id=organization_id,
    )
    if app is None:
        raise WorkflowBudgetScopeUnavailableError
    if app.workflow_id != workflow_id:
        raise AppPrimaryChangedDuringMutationError
    lock_workflow_budget_scope(
        db,
        workflow_id=workflow_id,
        organization_id=organization_id,
    )


def _find_budget(db: Session, workflow_id: Any, organization_id: Any = None):
    query = db.query(WorkflowBudget).filter(
        WorkflowBudget.workflow_id == workflow_id
    )
    if organization_id is not None:
        query = query.filter(WorkflowBudget.organization_id == organization_id)
    return query.first()


def _active_budget_amount(monthly_budget_usd: Any, is_enabled: Any) -> Decimal | None:
    """활성 예산 금액. 비활성/미설정/0 이하는 None — 판정·차단 제외 (BGT-REQ-010)."""
    if not is_enabled or monthly_budget_usd is None:
        return None
    amount = _to_decimal(monthly_budget_usd)
    if amount <= 0:
        return None
    return amount


def _current_month_cost_fake(
    db: Session,
    *,
    workflow_id: Any,
    period: Any,
    organization_id: Any = None,
) -> Decimal:
    return read_workflow_usage_aggregate(
        db,
        workflow_id=workflow_id,
        organization_id=organization_id,
        start_at=period.start_at,
        end_at=period.end_at,
    ).total_cost


def _current_month_cost_query(
    db: Session,
    *,
    workflow_id: Any,
    period: Any,
    organization_id: Any = None,
) -> Decimal:
    return read_workflow_usage_aggregate(
        db,
        workflow_id=workflow_id,
        organization_id=organization_id,
        start_at=period.start_at,
        end_at=period.end_at,
    ).total_cost


def _update_budget(
    db: Session,
    budget: WorkflowBudget,
    *,
    actor_id: Any,
    monthly_budget_usd: Decimal,
    is_enabled: bool,
) -> WorkflowBudget:
    if (
        _to_decimal(budget.monthly_budget_usd) == monthly_budget_usd
        and bool(budget.is_enabled) is is_enabled
    ):
        return budget

    budget.monthly_budget_usd = monthly_budget_usd
    budget.is_enabled = is_enabled
    budget.updated_by = actor_id
    _add_budget_audit(
        db,
        AuditAction.WORKFLOW_BUDGET_UPDATED,
        budget,
        actor_id=actor_id,
    )
    db.commit()
    db.refresh(budget)
    return budget


def _add_budget_audit(
    db: Session,
    action: str,
    budget: WorkflowBudget,
    *,
    actor_id: Any,
) -> None:
    add_action_audit(
        db,
        action,
        actor_id=actor_id,
        target_type="workflow_budget",
        target_id=budget.id,
        organization_id=budget.organization_id,
        metadata={
            "monthly_budget_usd": _format_budget_amount(budget.monthly_budget_usd),
            "is_enabled": bool(budget.is_enabled),
        },
    )


def _format_budget_amount(value: Any) -> str:
    return f"{_to_decimal(value):.2f}"


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
