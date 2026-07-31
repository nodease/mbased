import uuid

import pytest

from apps.workflow_engine.workflow.core.workflow_logger import WorkflowLogger


@pytest.mark.parametrize("trigger_mode", ["schedule", "scheduler"])
def test_workflow_logger_emits_correlated_system_schedule_run_without_user(
    trigger_mode,
):
    logger = WorkflowLogger()
    submitted = []
    logger._prepare_payloads = lambda *a, **k: (
        [
            {
                "redacted_payload": {},
                "redaction_applied": False,
                "pii_detected": False,
            }
        ],
        {
            "redaction_applied": False,
            "pii_detected": False,
            "payload_storage_mode": "redacted_only",
        },
        {
            "redaction": type("Policy", (), {"id": None})(),
            "retention": type("Policy", (), {"id": None})(),
            "visibility": type("Policy", (), {"id": None})(),
        },
    )
    logger._submit_log = lambda task_name, data, countdown=0: submitted.append(
        (task_name, data)
    )
    task_id = f"schedule:{uuid.uuid4()}"

    run_id = logger.create_run_log(
        workflow_id=str(uuid.uuid4()),
        user_id=None,
        user_input={},
        is_deployed=True,
        execution_context={
            "trigger_mode": trigger_mode,
            "workflow_task_id": task_id,
        },
        external_run_id=str(uuid.uuid4()),
    )

    assert run_id is not None
    assert submitted[0][0] == "log.create_run"
    assert submitted[0][1]["user_id"] is None
    assert submitted[0][1]["workflow_task_id"] == task_id


def test_workflow_logger_rejects_null_user_for_uncorrelated_run():
    logger = WorkflowLogger()

    assert (
        logger.create_run_log(
            workflow_id=str(uuid.uuid4()),
            user_id=None,
            user_input={},
            is_deployed=True,
            execution_context={"trigger_mode": "manual"},
        )
        is None
    )
