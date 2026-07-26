"""Disposable-session workflow budget adapter for background execution."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from apps.shared.domain.workflow_budget import BudgetExecutionDecision
from apps.shared.services.workflow_budget_execution import (
    evaluate_workflow_budget_execution,
)


class DisposableWorkflowBudgetDecisionAdapter:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def evaluate(
        self,
        *,
        workflow_id: uuid.UUID,
        now: datetime,
    ) -> BudgetExecutionDecision:
        db = self._session_factory()
        try:
            return evaluate_workflow_budget_execution(
                db,
                workflow_id=workflow_id,
                now=now,
            )
        finally:
            close = getattr(db, "close", None)
            if callable(close):
                close()


__all__ = ["DisposableWorkflowBudgetDecisionAdapter"]
