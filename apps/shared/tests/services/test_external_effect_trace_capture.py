from apps.shared.services.external_effect_trace_capture import (
    defers_provider_capture_until_finish,
    durable_provider_summary,
    sanitize_provider_trace_records,
    uses_metadata_only_provider_capture,
)


def test_container_capture_is_deferred_until_output_sensitivity_is_known() -> None:
    assert defers_provider_capture_until_finish("workflowNode")
    assert defers_provider_capture_until_finish("loopNode")
    assert not defers_provider_capture_until_finish("answerNode")


def test_github_comment_capture_reads_nested_node_options() -> None:
    assert uses_metadata_only_provider_capture(
        "githubNode",
        {"node_options": {"action": "comment_pr", "comment": "opaque"}},
    )
    assert not uses_metadata_only_provider_capture(
        "githubNode",
        {"node_options": {"action": "get_pr"}},
    )


def test_child_external_effect_marker_redacts_container_output() -> None:
    metadata = {"external_effect_output": {"sensitive": True}}

    assert uses_metadata_only_provider_capture("workflowNode", {}, metadata)
    assert durable_provider_summary(
        node_type="workflowNode",
        process_data={},
        trace_metadata=metadata,
    ) == {}


def test_github_comment_summary_survives_sanitized_process_data() -> None:
    metadata = {
        "http": {
            "method": "POST",
            "status_code": 403,
            "response_size": 17,
            "latency_ms": 5,
            "body": "opaque-response",
        },
        "external_effect": {
            "provider": "github",
            "operation": "github.issue_comment.create",
            "outcome": "failed_before_effect",
            "replay_decision": "stop",
            "error_code": "provider_rejected_request",
        },
    }

    summary = durable_provider_summary(
        node_type="githubNode",
        process_data={},
        trace_metadata=metadata,
    )

    assert summary == {
        "external_effect": {
            "provider": "github",
            "operation": "github.issue_comment.create",
            "method": "POST",
            "status": 403,
            "response_size": 17,
            "latency_ms": 5,
            "outcome": "failed_before_effect",
            "replay_decision": "stop",
            "error_code": "provider_rejected_request",
        }
    }
    assert "opaque-response" not in str(summary)


def test_provider_trace_record_drops_raw_encrypted_payload() -> None:
    metadata = {
        "http": {"method": "POST", "status_code": 200},
        "external_effect": {
            "provider": "generic_http",
            "operation": "generic_http.request",
            "outcome": "succeeded",
            "replay_decision": "result_unavailable",
        },
    }

    records = sanitize_provider_trace_records(
        [
            {
                "id": "payload-1",
                "payload_kind": "output",
                "redacted_payload": {"data": "opaque-response"},
                "raw_payload_encrypted": "encrypted-opaque-response",
            },
            {
                "id": "payload-2",
                "payload_kind": "http_response",
                "redacted_payload": "opaque-response",
            },
        ],
        node_type="httpRequestNode",
        process_data={},
        trace_metadata=metadata,
    )

    assert len(records) == 1
    assert records[0]["raw_payload_encrypted"] is None
    assert records[0]["redacted_payload"] == durable_provider_summary(
        node_type="httpRequestNode",
        process_data={},
        trace_metadata=metadata,
    )
    assert "opaque-response" not in str(records)


def test_dedicated_slack_summary_uses_safe_section_and_new_operation() -> None:
    metadata = {
        "slack": {
            "delivery_mode": "api",
            "delivery_status": "delivered",
            "status_code": 200,
            "request_size": 18,
            "response_size": 32,
            "latency_ms": 7,
            "message_ref": "must-not-survive",
            "url": "must-not-survive",
        },
        "external_effect": {
            "provider": "slack",
            "operation": "slack.chat.post_message",
            "outcome": "succeeded",
            "replay_decision": "reuse_result",
        },
    }

    summary = durable_provider_summary(
        node_type="slackPostNode",
        process_data={},
        trace_metadata=metadata,
    )

    assert summary == {
        "external_effect": {
            "provider": "slack",
            "operation": "slack.chat.post_message",
            "status": 200,
            "request_size": 18,
            "response_size": 32,
            "latency_ms": 7,
            "outcome": "succeeded",
            "replay_decision": "reuse_result",
        }
    }
    assert "must-not-survive" not in str(summary)
