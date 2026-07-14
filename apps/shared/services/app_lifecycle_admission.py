from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.shared.db.models.app import App
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import (
    RunStatus,
    RunTriggerMode,
    WorkflowRun,
)


class AppWorkflowAdmissionUnavailable(RuntimeError):
    code = "workflow.target_unavailable"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class LockedAppWorkflow:
    app: App
    workflow: Workflow


def lock_app_workflow_for_admission(
    db: Session,
    *,
    app_id: uuid.UUID,
    workflow_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> LockedAppWorkflow | None:
    """Lock lifecycle rows in App→Workflow order before active admission."""
    app = (
        db.query(App)
        .filter(
            App.id == app_id,
            App.organization_id == organization_id,
        )
        .populate_existing()
        .with_for_update(key_share=True)
        .first()
    )
    if app is None or app.workflow_id != workflow_id:
        return None

    workflow = (
        db.query(Workflow)
        .filter(
            Workflow.id == workflow_id,
            Workflow.app_id == app_id,
            Workflow.organization_id == organization_id,
        )
        .populate_existing()
        .with_for_update(key_share=True)
        .first()
    )
    if workflow is None:
        return None
    return LockedAppWorkflow(app=app, workflow=workflow)


def admit_workflow_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    app_id: uuid.UUID,
    workflow_id: uuid.UUID,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    trigger_mode: RunTriggerMode | str,
    inputs: dict,
    deployment_id: uuid.UUID | None = None,
    workflow_version: int | None = None,
    correlation_id: str | None = None,
    conversation_id: uuid.UUID | None = None,
    request_id: str | None = None,
    workflow_task_id: str | None = None,
    trace_metadata: dict | None = None,
    redaction_applied: bool = False,
    pii_detected: bool = False,
    redaction_policy_id: uuid.UUID | None = None,
    retention_policy_id: uuid.UUID | None = None,
    visibility_policy_id: uuid.UUID | None = None,
    payload_storage_mode: str = "redacted_only",
) -> WorkflowRun:
    """Persist the running blocker before a worker may start node effects."""
    try:
        locked = lock_app_workflow_for_admission(
            db,
            app_id=app_id,
            workflow_id=workflow_id,
            organization_id=organization_id,
        )
        if locked is None:
            raise AppWorkflowAdmissionUnavailable()

        existing = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if existing is not None:
            if (
                existing.app_id != app_id
                or existing.workflow_id != workflow_id
                or existing.user_id != user_id
            ):
                raise AppWorkflowAdmissionUnavailable()
            db.commit()
            return existing

        normalized_trigger = (
            trigger_mode
            if isinstance(trigger_mode, RunTriggerMode)
            else RunTriggerMode(str(trigger_mode))
        )
        run = WorkflowRun(
            id=run_id,
            workflow_id=workflow_id,
            app_id=app_id,
            user_id=user_id,
            status=RunStatus.RUNNING,
            trigger_mode=normalized_trigger,
            inputs=inputs,
            deployment_id=deployment_id,
            workflow_version=workflow_version,
            correlation_id=correlation_id,
            conversation_id=conversation_id,
            request_id=request_id,
            workflow_task_id=workflow_task_id,
            trace_metadata=trace_metadata or {},
            redaction_applied=redaction_applied,
            pii_detected=pii_detected,
            redaction_policy_id=redaction_policy_id,
            retention_policy_id=retention_policy_id,
            visibility_policy_id=visibility_policy_id,
            payload_storage_mode=payload_storage_mode,
        )
        db.add(run)
        db.commit()
        return run
    except AppWorkflowAdmissionUnavailable:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        existing = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if (
            existing is None
            or existing.app_id != app_id
            or existing.workflow_id != workflow_id
            or existing.user_id != user_id
        ):
            raise AppWorkflowAdmissionUnavailable() from None
        return existing
    except Exception:
        db.rollback()
        raise


def uuid_or_none(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None
