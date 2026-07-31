from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from apps.shared.db.models.app import App


class AppPrimaryChangedDuringMutationError(RuntimeError):
    """The App primary changed after a Workflow mutation observed its scope."""


def lock_app_for_lifecycle(
    db: Session,
    app_id: Any,
    *,
    organization_id: Any | None = None,
) -> App | None:
    """Serialize commands that can change an App's primary or deployment pointers."""
    query = db.query(App).filter(App.id == app_id)
    if organization_id is not None:
        query = query.filter(App.organization_id == organization_id)
    return query.populate_existing().with_for_update().first()


def lock_app_for_workflow_mutation(
    db: Session,
    *,
    app_id: Any,
    workflow_id: Any,
    organization_id: Any,
) -> App | None:
    """Serialize a Workflow mutation with App primary replacement.

    The first read captures whether the requested Workflow was primary when this
    command reached the lifecycle boundary. The locked read then refreshes the
    canonical App row. A primary replacement completed while the command waited
    is returned as a stale-state error instead of mutating only the old Workflow.
    Mutations that initially target an already non-primary Workflow retain their
    existing Workflow-scoped behavior.
    """
    observed_app = (
        db.query(App)
        .filter(
            App.id == app_id,
            App.organization_id == organization_id,
        )
        .populate_existing()
        .first()
    )
    if observed_app is None:
        return None
    observed_primary_workflow_id = observed_app.workflow_id

    locked_app = lock_app_for_lifecycle(
        db,
        app_id,
        organization_id=organization_id,
    )
    if locked_app is None:
        return None
    if (
        observed_primary_workflow_id == workflow_id
        and locked_app.workflow_id != workflow_id
    ):
        raise AppPrimaryChangedDuringMutationError
    return locked_app
