import logging
import uuid
from types import SimpleNamespace

import pytest

from apps.workflow_engine.workflow.core.workflow_logger import WorkflowLogger
from apps.workflow_engine.workflow.errors import NonRetryableWorkflowError


def test_public_client_history_mode_persists_only_content_free_run_and_node_logs(
    monkeypatch,
):
    submitted = []

    monkeypatch.setattr(
        WorkflowLogger,
        "_submit_log",
        lambda self, task_name, data, countdown=0: submitted.append(
            (task_name, data)
        )
        or True,
    )

    logger = WorkflowLogger()
    run_id = logger.create_run_log(
        workflow_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        user_input={"question": "private-current-question"},
        is_deployed=True,
        execution_context={
            "trigger_mode": "app",
            "suppress_content_persistence": True,
        },
    )
    logger.update_run_log_finish({"answer": "private-current-answer"})
    node_id = logger.create_node_log(
        node_id="llm-1",
        node_type="llmNode",
        inputs={"history": "private-history"},
        process_data={"prompt": "private-prompt"},
    )
    logger.update_node_log_finish(
        node_id,
        "llm-1",
        {"text": "private-node-answer"},
        node_type="llmNode",
        inputs={"history": "private-history"},
        process_data={"prompt": "private-prompt"},
        trace_metadata={
            "llm": {
                "prompt": "private-prompt",
                "credential_id": "private-credential-id",
                "provider": "openai",
                "selected_model": "gpt-4o-mini",
                "fallback_used": True,
                "prompt_tokens": 12,
                "completion_tokens": 7,
                "latency_ms": 31,
                "decision_factors": {"private": "routing-input"},
            }
        },
    )

    assert run_id is not None
    serialized = str(submitted)
    for marker in (
        "private-current-question",
        "private-current-answer",
        "private-history",
        "private-prompt",
        "private-node-answer",
        "private-credential-id",
        "routing-input",
    ):
        assert marker not in serialized

    create_run = next(data for name, data in submitted if name == "log.create_run")
    finish_run = next(
        data for name, data in submitted if name == "log.update_run_finish"
    )
    create_node = next(data for name, data in submitted if name == "log.create_node")
    finish_node = next(
        data for name, data in submitted if name == "log.update_node_finish"
    )
    assert create_run["user_input"] == {}
    assert create_run["trace_payloads"] == []
    assert finish_run["outputs"] == {}
    assert finish_run["trace_payloads"] == []
    assert create_node["inputs"] == {}
    assert create_node["process_data"] == {}
    assert finish_node["inputs"] == {}
    assert finish_node["outputs"] == {}
    assert finish_node["process_data"] == {}
    assert finish_node["trace_metadata"] == {
        "llm": {
            "provider": "openai",
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "latency_ms": 31,
            "selected_model": "gpt-4o-mini",
            "fallback_used": True,
        }
    }


@pytest.mark.parametrize("failure_stage", ["serialize", "publish"])
def test_submit_log_failure_does_not_escape_or_expose_raw_error(
    monkeypatch,
    caplog,
    failure_stage,
):
    raw_detail = "private broker endpoint must not escape"
    logger = WorkflowLogger()

    def raise_failure(*_args, **_kwargs):
        raise RuntimeError(raw_detail)

    if failure_stage == "serialize":
        monkeypatch.setattr(logger, "_serialize_for_celery", raise_failure)
    else:
        monkeypatch.setattr(
            "apps.workflow_engine.workflow.core.workflow_logger.celery_app.send_task",
            raise_failure,
        )

    with caplog.at_level(logging.ERROR):
        submitted = logger._submit_log("log.update_run_finish", {"result": "ok"})

    assert submitted is False
    assert raw_detail not in caplog.text
    assert "task=log.update_run_finish" in caplog.text
    assert "error_type=RuntimeError" in caplog.text


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


def test_create_run_log_keeps_run_id_when_publish_fails(monkeypatch):
    def raise_publish_failure(*_args, **_kwargs):
        raise RuntimeError("private broker detail")

    monkeypatch.setattr(WorkflowLogger, "_prepare_payloads", _passthrough_payloads)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_logger.celery_app.send_task",
        raise_publish_failure,
    )

    logger = WorkflowLogger()
    run_id = logger.create_run_log(
        workflow_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        user_input={"value": "hello"},
        is_deployed=False,
        execution_context={},
    )

    assert run_id is not None
    assert logger.workflow_run_id == run_id


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
