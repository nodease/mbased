from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

_WORKFLOW_BUDGET_SCOPE = "workflow_budget_scope"


def lock_workflow_budget_scope(
    db: Session,
    *,
    organization_id: Any,
    workflow_id: Any,
) -> None:
    """Serialize a Workflow budget mutation with primary replacement checks."""
    lock_key = f"{organization_id}:{workflow_id}"
    db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtext(_WORKFLOW_BUDGET_SCOPE),
                func.hashtext(lock_key),
            )
        )
    )
