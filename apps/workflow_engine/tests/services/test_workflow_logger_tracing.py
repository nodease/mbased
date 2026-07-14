import uuid
from types import SimpleNamespace

import pytest

from apps.workflow_engine.workflow.core.workflow_logger import WorkflowLogger
from apps.workflow_engine.workflow.errors import NonRetryableWorkflowError


def _passthrough_payloads(
    self,
    payloads,
    default_scope,
    app_id=None,
    default_node_run_id=None,
):
    records = [
        {
            "id": str(uuid.uuid4()),
            "payload_kind": item["payload_kind"],
            "redacted_payload": item["payload"],
        }
        for item in payloads
    ]
    return (
        records,
        {
            "redaction_applied": False,
            "pii_detected": False,
            "payload_storage_mode": "redacted_only",
        },
        {
            "redaction": SimpleNamespace(id=None),
            "retention": SimpleNamespace(id=None),
            "visibility": SimpleNamespace(id=None),
        },
    )


def test_policy_failure_disables_payload_capture_and_redacts_compat_fields(monkeypatch):
    captured = {}

    def raise_policy_error(*args, **kwargs):
        raise RuntimeError("policy_unavailable")

    def capture_submit(self, task_name, data, countdown=0):
        captured["task_name"] = task_name
        captured["data"] = data

    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_logger."
        "TracePolicyService.resolve_redaction_policy",
        raise_policy_error,
    )
    monkeypatch.setattr(WorkflowLogger, "_submit_log", capture_submit)

    logger = WorkflowLogger()
    logger.create_run_log(
        workflow_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        user_input={"email": "person@example.com"},
        is_deployed=False,
        execution_context={"app_id": str(uuid.uuid4())},
    )

    data = captured["data"]
    assert captured["task_name"] == "log.create_run"
    assert data["trace_payloads"] == []
    assert data["payload_storage_mode"] == "metadata_only"
    assert data["user_input"]["email"] == "[REDACTED]"


def test_create_run_log_sanitizes_run_trace_metadata(monkeypatch):
    captured = {}

    def capture_submit(self, task_name, data, countdown=0):
        captured["task_name"] = task_name
        captured["data"] = data

    monkeypatch.setattr(WorkflowLogger, "_submit_log", capture_submit)

    logger = WorkflowLogger()
    logger.create_run_log(
        workflow_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        user_input={"value": "hello"},
        is_deployed=False,
        execution_context={
            "app_id": str(uuid.uuid4()),
            "trace_metadata": {
                "gateway": {
                    "status_code": 200,
                    "response": {"body": "raw"},
                },
                "prompt": "raw prompt",
            },
        },
    )

    assert captured["task_name"] == "log.create_run"
    assert captured["data"]["trace_metadata"] == {"gateway": {"status_code": 200}}


def test_create_run_log_rejects_invalid_trigger_before_run_allocation(monkeypatch):
    def fail_if_called(*args, **kwargs):
        pytest.fail("invalid trigger must not prepare or submit a run log")

    logger = WorkflowLogger()
    monkeypatch.setattr(logger, "_prepare_payloads", fail_if_called)
    monkeypatch.setattr(logger, "_submit_log", fail_if_called)

    with pytest.raises(
        NonRetryableWorkflowError,
        match="^workflow run trigger mode is invalid$",
    ) as exc_info:
        logger.create_run_log(
            workflow_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            user_input={"secret_like_value": "must-not-appear"},
            is_deployed=True,
            execution_context={"trigger_mode": "unknown-secret-like-trigger"},
        )

    assert logger.workflow_run_id is None
    assert logger.app_id is None
    assert "unknown-secret-like-trigger" not in str(exc_info.value)
    assert "must-not-appear" not in str(exc_info.value)


def test_create_run_log_keeps_valid_webhook_wire_value(monkeypatch):
    captured = {}

    def capture_submit(self, task_name, data, countdown=0):
        captured["task_name"] = task_name
        captured["data"] = data

    monkeypatch.setattr(WorkflowLogger, "_prepare_payloads", _passthrough_payloads)
    monkeypatch.setattr(WorkflowLogger, "_submit_log", capture_submit)

    logger = WorkflowLogger()
    run_id = logger.create_run_log(
        workflow_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        user_input={},
        is_deployed=True,
        execution_context={"trigger_mode": "webhook"},
    )

    assert run_id is not None
    assert captured["task_name"] == "log.create_run"
    assert captured["data"]["trigger_mode"] == "webhook"


def test_create_run_log_commits_durable_admission_before_publish(monkeypatch):
    order = []
    session = SimpleNamespace(close=lambda: order.append("close"))
    monkeypatch.setattr(WorkflowLogger, "_prepare_payloads", _passthrough_payloads)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_logger.SessionLocal",
        lambda: session,
    )
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_logger.admit_workflow_run",
        lambda *args, **kwargs: order.append("admit"),
    )
    monkeypatch.setattr(
        WorkflowLogger,
        "_submit_log",
        lambda *args, **kwargs: order.append("publish"),
    )

    logger = WorkflowLogger(db=object())
    logger.create_run_log(
        workflow_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        user_input={},
        is_deployed=False,
        execution_context={
            "app_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "trigger_mode": "manual",
        },
    )

    assert order == ["admit", "close", "publish"]


def test_mail_node_finish_log_sanitizes_content_before_trace_policy(monkeypatch):
    captured = {}

    def capture_submit(self, task_name, data, countdown=0):
        captured["task_name"] = task_name
        captured["data"] = data

    def passthrough_payloads(
        self,
        payloads,
        default_scope,
        app_id=None,
        default_node_run_id=None,
    ):
        records = [
            {
                "id": str(uuid.uuid4()),
                "payload_kind": item["payload_kind"],
                "redacted_payload": item["payload"],
            }
            for item in payloads
        ]
        return (
            records,
            {"redaction_applied": False, "pii_detected": False},
            {"redaction": SimpleNamespace(id=None)},
        )

    monkeypatch.setattr(WorkflowLogger, "_submit_log", capture_submit)
    monkeypatch.setattr(WorkflowLogger, "_prepare_payloads", passthrough_payloads)
    monkeypatch.setattr(
        WorkflowLogger,
        "_redact_compat_value",
        lambda self, value, payload_kind, app_id=None: value,
    )
    logger = WorkflowLogger()
    logger.workflow_run_id = uuid.uuid4()

    logger.update_node_log_finish(
        uuid.uuid4(),
        "mail-source",
        {
            "emails": [{"body_text": "confidential mail body"}],
            "total_count": 1,
            "folder": "INBOX",
        },
        node_type="mailNode",
    )

    assert captured["task_name"] == "log.update_node_finish"
    assert captured["data"]["outputs"] == {
        "mail_content_redacted": True,
        "total_count": 1,
        "folder": "INBOX",
    }
    assert "confidential mail body" not in str(captured["data"])


def test_external_effect_finish_log_persists_summary_without_provider_payload(
    monkeypatch,
):
    captured = {}

    def capture_submit(self, task_name, data, countdown=0):
        captured["task_name"] = task_name
        captured["data"] = data

    def passthrough_payloads(
        self,
        payloads,
        default_scope,
        app_id=None,
        default_node_run_id=None,
    ):
        records = [
            {
                "id": str(uuid.uuid4()),
                "payload_kind": item["payload_kind"],
                "redacted_payload": item["payload"],
            }
            for item in payloads
        ]
        return (
            records,
            {"redaction_applied": False, "pii_detected": False},
            {"redaction": SimpleNamespace(id=None)},
        )

    monkeypatch.setattr(WorkflowLogger, "_submit_log", capture_submit)
    monkeypatch.setattr(WorkflowLogger, "_prepare_payloads", passthrough_payloads)
    monkeypatch.setattr(
        WorkflowLogger,
        "_redact_compat_value",
        lambda self, value, payload_kind, app_id=None: value,
    )
    logger = WorkflowLogger()
    logger.workflow_run_id = uuid.uuid4()

    logger.update_node_log_finish(
        uuid.uuid4(),
        "http-1",
        {
            "status": 200,
            "data": {"opaque": "customer-response"},
            "headers": {"x-provider": "opaque-header"},
        },
        node_type="httpRequestNode",
        inputs={"upstream": "opaque-input"},
        process_data={
            "method": "POST",
            "url": "https://example.test/private",
            "body": "opaque-request",
        },
        trace_metadata={
            "http": {
                "method": "POST",
                "status_code": 200,
                "request_size": 14,
                "response_size": 31,
                "latency_ms": 9,
            },
            "external_effect": {
                "provider": "generic_http",
                "operation": "generic_http.request",
                "outcome": "succeeded",
                "replay_decision": "result_unavailable",
            },
        },
    )

    data = captured["data"]
    assert captured["task_name"] == "log.update_node_finish"
    assert data["inputs"] == {}
    assert data["process_data"] == {}
    assert data["outputs"] == {
        "external_effect": {
            "provider": "generic_http",
            "operation": "generic_http.request",
            "method": "POST",
            "status": 200,
            "request_size": 14,
            "response_size": 31,
            "latency_ms": 9,
            "outcome": "succeeded",
            "replay_decision": "result_unavailable",
        }
    }
    assert len(data["trace_payloads"]) == 1
    assert data["trace_payloads"][0]["redacted_payload"] == data["outputs"]
    assert "customer-response" not in str(data)
    assert "opaque-header" not in str(data)
    assert "opaque-request" not in str(data)
    assert "opaque-input" not in str(data)


def test_github_comment_start_log_drops_nested_node_options_and_inputs(monkeypatch):
    captured = {}

    def capture_submit(self, task_name, data, countdown=0):
        captured["task_name"] = task_name
        captured["data"] = data

    def passthrough_payloads(
        self,
        payloads,
        default_scope,
        app_id=None,
        default_node_run_id=None,
    ):
        records = [
            {
                "id": str(uuid.uuid4()),
                "payload_kind": item["payload_kind"],
                "redacted_payload": item["payload"],
            }
            for item in payloads
        ]
        return (
            records,
            {"redaction_applied": False, "pii_detected": False},
            {"redaction": SimpleNamespace(id=None)},
        )

    monkeypatch.setattr(WorkflowLogger, "_submit_log", capture_submit)
    monkeypatch.setattr(WorkflowLogger, "_prepare_payloads", passthrough_payloads)
    monkeypatch.setattr(
        WorkflowLogger,
        "_redact_compat_value",
        lambda self, value, payload_kind, app_id=None: value,
    )
    logger = WorkflowLogger()
    logger.workflow_run_id = uuid.uuid4()

    logger.create_node_log(
        "github-1",
        "githubNode",
        {"comment": "opaque-input"},
        process_data={
            "node_options": {
                "action": "comment_pr",
                "comment": "opaque-template",
            }
        },
    )

    data = captured["data"]
    assert captured["task_name"] == "log.create_node"
    assert data["inputs"] == {}
    assert data["process_data"] == {}
    assert data["trace_payloads"][0]["redacted_payload"] == {}
    assert "opaque-input" not in str(data)
    assert "opaque-template" not in str(data)


def test_external_effect_downstream_finish_log_drops_copied_output(monkeypatch):
    captured = {}

    def capture_submit(self, task_name, data, countdown=0):
        captured["data"] = data

    def passthrough_payloads(
        self,
        payloads,
        default_scope,
        app_id=None,
        default_node_run_id=None,
    ):
        records = [
            {
                "id": str(uuid.uuid4()),
                "payload_kind": item["payload_kind"],
                "redacted_payload": item["payload"],
            }
            for item in payloads
        ]
        return (
            records,
            {"redaction_applied": False, "pii_detected": False},
            {"redaction": SimpleNamespace(id=None)},
        )

    monkeypatch.setattr(WorkflowLogger, "_submit_log", capture_submit)
    monkeypatch.setattr(WorkflowLogger, "_prepare_payloads", passthrough_payloads)
    logger = WorkflowLogger()
    logger.workflow_run_id = uuid.uuid4()

    logger.update_node_log_finish(
        uuid.uuid4(),
        "answer-1",
        {"answer": "copied-provider-response"},
        node_type="answerNode",
        inputs={"value": "copied-provider-response"},
        process_data={"_external_effect_output_sensitive": True},
    )

    data = captured["data"]
    assert data["inputs"] == {}
    assert data["process_data"] == {}
    assert data["outputs"] == {}
    assert data["trace_payloads"][0]["redacted_payload"] == {}
    assert data["trace_metadata"]["external_effect_output"] == {"sensitive": True}
    assert "copied-provider-response" not in str(data)


def test_container_start_defers_input_capture_until_finish(monkeypatch):
    captured = {}

    def capture_submit(self, task_name, data, countdown=0):
        captured["data"] = data

    monkeypatch.setattr(WorkflowLogger, "_submit_log", capture_submit)
    logger = WorkflowLogger()
    logger.workflow_run_id = uuid.uuid4()

    logger.create_node_log(
        "workflow-1",
        "workflowNode",
        {"value": "not-yet-classified"},
        process_data={"node_options": {"app_id": "child-app"}},
    )

    data = captured["data"]
    assert data["inputs"] == {}
    assert data["process_data"] == {}
    assert data["trace_payloads"] == []
    assert "not-yet-classified" not in str(data)


def test_safe_container_finish_captures_deferred_input(monkeypatch):
    captured = {}

    def capture_submit(self, task_name, data, countdown=0):
        captured["data"] = data

    monkeypatch.setattr(WorkflowLogger, "_submit_log", capture_submit)
    monkeypatch.setattr(WorkflowLogger, "_prepare_payloads", _passthrough_payloads)
    logger = WorkflowLogger()
    logger.workflow_run_id = uuid.uuid4()

    logger.update_node_log_finish(
        uuid.uuid4(),
        "workflow-1",
        {"result": "safe-child-output"},
        node_type="workflowNode",
        inputs={"value": "safe-parent-input"},
        process_data={"node_options": {"app_id": "child-app"}},
        trace_metadata={"workflow": {"latency_ms": 1}},
    )

    data = captured["data"]
    assert data["inputs"] == {"value": "safe-parent-input"}
    assert data["process_data"] == {"node_options": {"app_id": "child-app"}}
    assert {record["payload_kind"] for record in data["trace_payloads"]} == {
        "input",
        "output",
    }


def test_sensitive_container_finish_never_captures_deferred_input(monkeypatch):
    captured = {}

    def capture_submit(self, task_name, data, countdown=0):
        captured["data"] = data

    monkeypatch.setattr(WorkflowLogger, "_submit_log", capture_submit)
    monkeypatch.setattr(WorkflowLogger, "_prepare_payloads", _passthrough_payloads)
    logger = WorkflowLogger()
    logger.workflow_run_id = uuid.uuid4()

    logger.update_node_log_finish(
        uuid.uuid4(),
        "workflow-1",
        {"result": "opaque-child-provider-response"},
        node_type="workflowNode",
        inputs={"value": "opaque-child-provider-input"},
        process_data={"node_options": {"app_id": "child-app"}},
        trace_metadata={"external_effect_output": {"sensitive": True}},
    )

    data = captured["data"]
    assert data["inputs"] == {}
    assert data["process_data"] == {}
    assert data["outputs"] == {}
    assert len(data["trace_payloads"]) == 1
    assert data["trace_payloads"][0]["payload_kind"] == "output"
    assert "opaque-child-provider" not in str(data)
