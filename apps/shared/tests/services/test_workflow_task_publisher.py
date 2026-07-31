import uuid

import pytest
from apps.shared.services.workflow_task_publisher import (
    PUBLIC_CHAT_WORKFLOW_TASK_NAME,
    WORKFLOW_ARGS_REPR,
    WORKFLOW_KWARGS_REPR,
    WorkflowTaskPublishError,
    send_workflow_task,
)


class _Celery:
    def __init__(self, error=None) -> None:
        self.error = error
        self.calls = []

    def send_task(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        self.calls.append((args, kwargs))
        return object()


def test_workflow_publisher_adds_identity_once_and_redacts_repr() -> None:
    celery = _Celery()
    existing = str(uuid.uuid4())
    args = [{"nodes": []}, {"secret": "not-for-logs"}, {"execution_id": existing}]

    send_workflow_task(celery, "workflow.execute", args=args)

    sent_args = celery.calls[0][1]["args"]
    assert sent_args[2]["execution_id"] == existing
    assert celery.calls[0][1]["argsrepr"] == WORKFLOW_ARGS_REPR
    assert celery.calls[0][1]["kwargsrepr"] == WORKFLOW_KWARGS_REPR
    assert args[2] == {"execution_id": existing}


def test_public_chat_workflow_publisher_adds_identity_and_redacts_repr() -> None:
    celery = _Celery()
    context = {"execution_actor": {"type": "public"}}

    send_workflow_task(
        celery,
        PUBLIC_CHAT_WORKFLOW_TASK_NAME,
        args=[{"nodes": []}, {"question": "private"}, context, True],
    )

    sent = celery.calls[0]
    uuid.UUID(sent[1]["args"][2]["execution_id"])
    assert sent[1]["argsrepr"] == WORKFLOW_ARGS_REPR
    assert sent[1]["kwargsrepr"] == WORKFLOW_KWARGS_REPR
    assert "execution_id" not in context


def test_workflow_publisher_issues_identity_when_missing() -> None:
    celery = _Celery()

    send_workflow_task(celery, "workflow.stream", args=[{}, {}, {}, "run-id"])

    uuid.UUID(celery.calls[0][1]["args"][2]["execution_id"])


@pytest.mark.parametrize("invalid_identity", [None, "", "not-a-uuid"])
def test_workflow_publisher_replaces_invalid_initial_identity(invalid_identity) -> None:
    celery = _Celery()

    send_workflow_task(
        celery,
        "workflow.execute",
        args=[{}, {}, {"execution_id": invalid_identity}],
    )

    issued = celery.calls[0][1]["args"][2]["execution_id"]
    uuid.UUID(issued)
    assert issued != invalid_identity


def test_separate_publish_calls_do_not_share_generated_execution_identity() -> None:
    celery = _Celery()
    shared_context = {"workflow_id": str(uuid.uuid4())}

    send_workflow_task(celery, "workflow.execute", args=[{}, {}, shared_context])
    send_workflow_task(celery, "workflow.execute", args=[{}, {}, shared_context])

    first = celery.calls[0][1]["args"][2]["execution_id"]
    second = celery.calls[1][1]["args"][2]["execution_id"]
    assert first != second
    assert "execution_id" not in shared_context


def test_publish_failure_does_not_expose_broker_exception_message() -> None:
    celery = _Celery(RuntimeError("redis://user:secret@broker"))

    with pytest.raises(WorkflowTaskPublishError) as captured:
        send_workflow_task(celery, "workflow.execute", args=[{}, {}, {}])

    assert "secret" not in str(captured.value)
