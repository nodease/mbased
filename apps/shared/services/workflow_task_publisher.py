from __future__ import annotations

import logging
import uuid
from typing import Any

from celery import Task
from celery.worker.request import Request

logger = logging.getLogger(__name__)

WORKFLOW_ARGS_REPR = "[workflow arguments redacted]"
WORKFLOW_KWARGS_REPR = "{workflow arguments redacted}"
PUBLIC_CHAT_WORKFLOW_TASK_NAME = "workflow.execute_public_chat.v1"
PUBLIC_CHAT_WORKFLOW_QUEUE = "workflow-public-chat-v1"

_EXECUTION_CONTEXT_ARG_INDEX = {
    "workflow.execute": 2,
    PUBLIC_CHAT_WORKFLOW_TASK_NAME: 2,
    "workflow.execute_deployed": 2,
    "workflow.execute_by_deployment": 2,
    "workflow.stream": 2,
}


class WorkflowTaskPublishError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("workflow.task_publish_failed")
        self.code = "workflow.task_publish_failed"


class RedactedWorkflowRequest(Request):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._argsrepr = WORKFLOW_ARGS_REPR
        self._kwargsrepr = WORKFLOW_KWARGS_REPR


class RedactedWorkflowTask(Task):
    Request = RedactedWorkflowRequest

    def apply_async(self, args=None, kwargs=None, **options):
        options["argsrepr"] = WORKFLOW_ARGS_REPR
        options["kwargsrepr"] = WORKFLOW_KWARGS_REPR
        return super().apply_async(args=args, kwargs=kwargs, **options)


def _with_execution_identity(task_name: str, args: list[Any] | tuple[Any, ...] | None):
    if args is None:
        return None
    copied = list(args)
    context_index = _EXECUTION_CONTEXT_ARG_INDEX.get(task_name)
    if context_index is None or len(copied) <= context_index:
        return copied
    context = copied[context_index]
    if not isinstance(context, dict):
        return copied
    next_context = dict(context)
    try:
        execution_id = str(uuid.UUID(str(next_context.get("execution_id"))))
    except (TypeError, ValueError, AttributeError):
        execution_id = str(uuid.uuid4())
    next_context["execution_id"] = execution_id
    copied[context_index] = next_context
    return copied


def send_workflow_task(
    celery_app: Any,
    task_name: str,
    *,
    args: list[Any] | tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
    **options: Any,
):
    if not task_name.startswith("workflow."):
        raise ValueError("workflow publisher only accepts workflow tasks")
    safe_options = dict(options)
    safe_options["argsrepr"] = WORKFLOW_ARGS_REPR
    safe_options["kwargsrepr"] = WORKFLOW_KWARGS_REPR
    try:
        return celery_app.send_task(
            task_name,
            args=_with_execution_identity(task_name, args),
            kwargs=dict(kwargs or {}),
            **safe_options,
        )
    except Exception as exc:
        logger.error(
            "workflow task publish failed: task=%s error_type=%s",
            task_name,
            type(exc).__name__,
        )
        raise WorkflowTaskPublishError() from None
