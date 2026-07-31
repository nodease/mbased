from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from apps.shared.db.models.workflow_budget import WorkflowBudget
from apps.shared.domain.workflow_budget import (
    BudgetExecutionDecision,
    resolve_month_period_kst,
)
from apps.shared.services.provider_usage_cost_read_model import (
    read_workflow_usage_aggregate,
)
from sqlalchemy.orm import Session


def evaluate_workflow_budget_execution(
    db: Session,
    *,
    workflow_id: Any,
    now: datetime,
) -> BudgetExecutionDecision:
    if workflow_id is None:
        return BudgetExecutionDecision(status="allowed")
    normalized_id = _normalize_workflow_id(workflow_id)

    begin_nested = getattr(db, "begin_nested", None)
    if not callable(begin_nested):
        try:
            return _evaluate_workflow_budget_execution(
                db,
                workflow_id=normalized_id,
                now=now,
            )
        except Exception:
            return BudgetExecutionDecision(status="unavailable")

    # Schedule callers hold canonical row locks in the outer transaction. A
    # failed aggregate must roll back only its statement/savepoint so the
    # caller can persist a bounded unavailable/dead-letter transition.
    nested_transaction = begin_nested()
    try:
        with nested_transaction:
            return _evaluate_workflow_budget_execution(
                db,
                workflow_id=normalized_id,
                now=now,
            )
    except Exception:
        return BudgetExecutionDecision(status="unavailable")


def _evaluate_workflow_budget_execution(
    db: Session,
    *,
    workflow_id: Any,
    now: datetime,
) -> BudgetExecutionDecision:
    budget = (
        db.query(WorkflowBudget)
        .filter(WorkflowBudget.workflow_id == workflow_id)
        .first()
    )
    if budget is None or not budget.is_enabled:
        return BudgetExecutionDecision(status="allowed")
    monthly_budget = _decimal(budget.monthly_budget_usd)
    if monthly_budget <= 0:
        return BudgetExecutionDecision(status="allowed")

    period = resolve_month_period_kst(now)
    usage = read_workflow_usage_aggregate(
        db,
        workflow_id=workflow_id,
        organization_id=None,
        start_at=period.start_at,
        end_at=period.end_at,
    )
    if not usage.usage_data_complete:
        return BudgetExecutionDecision(status="unavailable")
    current_cost = usage.total_cost

    return BudgetExecutionDecision(
        status="blocked" if current_cost > monthly_budget else "allowed"
    )


def _normalize_workflow_id(value: Any):
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return value


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
