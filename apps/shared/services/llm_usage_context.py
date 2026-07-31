import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import WorkflowRun
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class LLMUsageContext:
    organization_id: Optional[uuid.UUID]
    workflow_id: Optional[uuid.UUID]
    workflow_run_id: Optional[uuid.UUID]


def _log_skip(logger: Optional[logging.Logger], reason: str) -> None:
    if logger is not None:
        logger.error("[LLMService] Usage log skipped: %s", reason)


def _log_context_warning(logger: Optional[logging.Logger], reason: str) -> None:
    if logger is not None:
        logger.warning("[LLMService] Usage log context warning: %s", reason)


def _parse_uuid(
    value: object,
    field_name: str,
    logger: Optional[logging.Logger],
) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        _log_skip(logger, f"{field_name} is invalid.")
        return None


def resolve_llm_usage_context(
    db: Session,
    *,
    organization_id: object = None,
    workflow_id: object = None,
    workflow_run_id: object = None,
    logger: Optional[logging.Logger] = None,
) -> Optional[LLMUsageContext]:
    organization_uuid = None
    if organization_id is not None:
        organization_uuid = _parse_uuid(organization_id, "organization_id", logger)
        if organization_uuid is None:
            return None

    workflow_uuid = None
    if workflow_id is not None:
        workflow_uuid = _parse_uuid(workflow_id, "workflow_id", logger)
        if workflow_uuid is None:
            return None

    workflow_run_uuid = None
    if workflow_run_id is not None:
        workflow_run_uuid = _parse_uuid(workflow_run_id, "workflow_run_id", logger)
        if workflow_run_uuid is None:
            return None

        run_log = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == workflow_run_uuid)
            .first()
        )
        if not run_log:
            _log_skip(logger, "workflow_run_id not found.")
            return None

        run_workflow_uuid = _parse_uuid(
            getattr(run_log, "workflow_id", None),
            "workflow_run.workflow_id",
            logger,
        )
        if run_workflow_uuid is None:
            return None
        if workflow_uuid is not None and workflow_uuid != run_workflow_uuid:
            _log_skip(logger, "workflow_id does not match workflow_run_id.")
            return None

        workflow_uuid = run_workflow_uuid

    workflow = None
    if workflow_uuid is not None:
        workflow = db.query(Workflow).filter(Workflow.id == workflow_uuid).first()
        if workflow is None:
            _log_skip(logger, "workflow_id not found.")
            return None

    if organization_uuid is None and workflow is not None:
        workflow_organization_id = getattr(workflow, "organization_id", None)
        if workflow_organization_id is not None:
            try:
                organization_uuid = uuid.UUID(str(workflow_organization_id))
            except (TypeError, ValueError):
                _log_context_warning(
                    logger,
                    "workflow.organization_id is invalid; continuing without organization scope.",
                )

    return LLMUsageContext(
        organization_id=organization_uuid,
        workflow_id=workflow_uuid,
        workflow_run_id=workflow_run_uuid,
    )
