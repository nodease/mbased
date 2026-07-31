"""Node log tasks should treat missing parent WorkflowRun as a quiet retry."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from apps.log_system import tasks as log_tasks
from celery.exceptions import Retry
from sqlalchemy.exc import IntegrityError


class _MissingQuery:
    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return None


class _MissingRunSession:
    def __init__(self):
        self.rollback_calls = 0
        self.closed = False

    def expire_all(self):
        pass

    def query(self, *args, **kwargs):
        return _MissingQuery()

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.closed = True


class _ExistingNodeQuery:
    def __init__(self, node_run):
        self.node_run = node_run

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.node_run


class _ExistingNodeSession:
    def __init__(self, node_run):
        self.node_run = node_run
        self.committed = False
        self.closed = False

    def expire_all(self):
        pass

    def query(self, model):
        if model is log_tasks.WorkflowNodeRun:
            return _ExistingNodeQuery(self.node_run)
        return _ExistingNodeQuery(None)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def _raise_retry(*, exc, countdown):
    raise Retry(exc=exc, when=countdown)


def _base_node_data(**overrides):
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "id": str(uuid4()),
        "log_id": str(uuid4()),
        "workflow_run_id": str(uuid4()),
        "node_id": "node-1",
        "node_type": "llm",
        "inputs": {},
        "outputs": {"answer": "ok"},
        "process_data": {},
        "error_message": "node failed",
        "started_at": now,
        "finished_at": now,
        "trace_payloads": [],
    }
    data.update(overrides)
    return data


def test_log_system_reapplies_external_effect_metadata_only_policy(monkeypatch):
    started_at = datetime.now(timezone.utc)
    node_run = type(
        "NodeRun",
        (),
        {
            "status": None,
            "outputs": {},
            "started_at": started_at,
            "finished_at": None,
            "duration": None,
            "node_type": "httpRequestNode",
            "trace_metadata": {},
            "redaction_applied": False,
            "pii_detected": False,
            "sequence": None,
            "retry_count": 0,
        },
    )()
    session = _ExistingNodeSession(node_run)
    inserted_payloads = []
    deleted_node_ids = []
    monkeypatch.setattr(log_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        log_tasks,
        "_delete_node_trace_payloads",
        lambda _session, node_id: deleted_node_ids.append(node_id),
    )
    monkeypatch.setattr(
        log_tasks,
        "_insert_trace_payloads",
        lambda _session, _run_id, records: inserted_payloads.extend(records),
    )
    data = _base_node_data(
        node_type="httpRequestNode",
        inputs={"opaque": "request-input"},
        process_data={"method": "POST", "body": "request-body"},
        outputs={"status": 200, "data": "provider-body", "headers": {}},
        trace_metadata={
            "http": {
                "method": "POST",
                "status_code": 200,
                "response_size": 13,
                "latency_ms": 4,
            },
            "external_effect": {
                "provider": "generic_http",
                "operation": "generic_http.request",
                "outcome": "succeeded",
                "replay_decision": "result_unavailable",
            },
        },
        trace_payloads=[
            {
                "id": str(uuid4()),
                "payload_kind": "output",
                "redacted_payload": {"data": "provider-body"},
                "raw_payload_encrypted": "must-not-survive",
            }
        ],
    )

    result = log_tasks.update_node_log_finish.__wrapped__(data)

    assert result["status"] == "success"
    assert session.committed is True
    assert session.closed is True
    assert node_run.inputs == {}
    assert node_run.process_data == {}
    assert node_run.outputs == {
        "external_effect": {
            "provider": "generic_http",
            "operation": "generic_http.request",
            "method": "POST",
            "status": 200,
            "response_size": 13,
            "latency_ms": 4,
            "outcome": "succeeded",
            "replay_decision": "result_unavailable",
        }
    }
    assert [str(node_id) for node_id in deleted_node_ids] == [data["log_id"]]
    assert len(inserted_payloads) == 1
    assert inserted_payloads[0]["redacted_payload"] == node_run.outputs
    assert inserted_payloads[0]["raw_payload_encrypted"] is None
    assert "provider-body" not in str(inserted_payloads)


def test_log_system_restores_safe_deferred_container_values_on_finish(monkeypatch):
    started_at = datetime.now(timezone.utc)
    node_run = type(
        "NodeRun",
        (),
        {
            "status": None,
            "inputs": {},
            "process_data": {},
            "outputs": {},
            "started_at": started_at,
            "finished_at": None,
            "duration": None,
            "node_type": "workflowNode",
            "trace_metadata": {},
            "redaction_applied": False,
            "pii_detected": False,
            "sequence": None,
            "retry_count": 0,
        },
    )()
    session = _ExistingNodeSession(node_run)
    inserted_payloads = []
    monkeypatch.setattr(log_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        log_tasks,
        "_insert_trace_payloads",
        lambda _session, _run_id, records: inserted_payloads.extend(records),
    )
    data = _base_node_data(
        node_type="workflowNode",
        inputs={"value": "safe-parent-input"},
        process_data={"node_options": {"app_id": "child-app"}},
        outputs={"result": "safe-child-output"},
        trace_metadata={"workflow": {"latency_ms": 1}},
        trace_payloads=[
            {
                "id": str(uuid4()),
                "payload_kind": "output",
                "redacted_payload": {"result": "safe-child-output"},
            },
            {
                "id": str(uuid4()),
                "payload_kind": "input",
                "redacted_payload": {"value": "safe-parent-input"},
            },
        ],
    )

    result = log_tasks.update_node_log_finish.__wrapped__(data)

    assert result["status"] == "success"
    assert node_run.inputs == {"value": "safe-parent-input"}
    assert node_run.process_data == {"node_options": {"app_id": "child-app"}}
    assert node_run.outputs == {"result": "safe-child-output"}
    assert {record["payload_kind"] for record in inserted_payloads} == {
        "input",
        "output",
    }


def test_log_system_error_removes_prior_sensitive_trace_payloads(monkeypatch):
    started_at = datetime.now(timezone.utc)
    node_run = type(
        "NodeRun",
        (),
        {
            "status": None,
            "inputs": {"opaque": "old-input"},
            "process_data": {"opaque": "old-process"},
            "started_at": started_at,
            "finished_at": None,
            "duration": None,
            "node_type": "answerNode",
            "trace_metadata": {},
            "sequence": None,
            "retry_count": 0,
        },
    )()
    session = _ExistingNodeSession(node_run)
    deleted_node_ids = []
    monkeypatch.setattr(log_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        log_tasks,
        "_delete_node_trace_payloads",
        lambda _session, node_id: deleted_node_ids.append(node_id),
    )
    data = _base_node_data(
        node_type="answerNode",
        inputs={},
        process_data={},
        trace_metadata={"external_effect_output": {"sensitive": True}},
    )

    result = log_tasks.update_node_log_error.__wrapped__(data)

    assert result["status"] == "success"
    assert node_run.inputs == {}
    assert node_run.process_data == {}
    assert [str(node_id) for node_id in deleted_node_ids] == [data["log_id"]]


@pytest.mark.parametrize(
    "task_func",
    [log_tasks.update_node_log_finish, log_tasks.update_node_log_error],
)
def test_node_terminal_update_retries_integrity_collision(task_func, monkeypatch):
    started_at = datetime.now(timezone.utc)
    node_run = type(
        "NodeRun",
        (),
        {
            "status": None,
            "inputs": {},
            "process_data": {},
            "outputs": {},
            "started_at": started_at,
            "finished_at": None,
            "duration": None,
            "node_type": "answerNode",
            "trace_metadata": {},
            "redaction_applied": False,
            "pii_detected": False,
            "sequence": None,
            "retry_count": 0,
        },
    )()
    session = _ExistingNodeSession(node_run)

    def fail_commit():
        raise IntegrityError("insert", {}, RuntimeError("concurrent insert"))

    def build_retry(*, exc, countdown, throw=False):
        del countdown, throw
        return Retry(exc=exc)

    session.commit = fail_commit
    monkeypatch.setattr(log_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(task_func, "retry", build_retry)

    with pytest.raises(Retry):
        task_func.__wrapped__(_base_node_data(node_type="answerNode"))

    assert session.closed is True


@pytest.mark.parametrize(
    "task_func",
    [
        log_tasks.create_node_log,
        log_tasks.update_node_log_finish,
        log_tasks.update_node_log_error,
    ],
)
def test_missing_workflow_run_retry_is_not_logged_as_error(
    task_func, monkeypatch, caplog
):
    session = _MissingRunSession()
    monkeypatch.setattr(log_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(task_func, "retry", _raise_retry)

    with pytest.raises(Retry):
        task_func.__wrapped__(_base_node_data())

    assert session.rollback_calls == 0
    assert session.closed is True
    assert "실패" not in caplog.text
