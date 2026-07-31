from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

_WORKFLOW_PERMISSION_SCOPE = "workflow_permission_scope"


def lock_workflow_permission_scope(
    db: Session,
    *,
    organization_id: Any,
    workflow_id: Any,
) -> None:
    """Serialize direct grant snapshots and mutations for one Workflow."""
    lock_key = f"{organization_id}:{workflow_id}"
    db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtext(_WORKFLOW_PERMISSION_SCOPE),
                func.hashtext(lock_key),
            )
        )
    )
