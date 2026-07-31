from __future__ import annotations

from typing import Any

from apps.shared.domain.external_effect_error import ALLOWED_EFFECT_ERROR_CODES

_HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_OUTCOMES = frozenset({"succeeded", "failed_before_effect", "effect_outcome_unknown"})
_REPLAY_DECISIONS = frozenset(
    {
        "reuse_result",
        "result_unavailable",
        "retry_before_effect",
        "replay_same_key",
        "stop",
    }
)
_PROVIDER_OPERATION_BY_NODE_TYPE = {
    "httpRequestNode": ("generic_http", "generic_http.request"),
    "slackPostNode": ("slack", "slack.http.request"),
    "githubNode": ("github", "github.issue_comment.create"),
}
_PROVIDER_OPERATIONS_BY_NODE_TYPE = {
    "slackPostNode": frozenset(
        {
            "slack.http.request",
            "slack.chat.post_message",
            "slack.incoming_webhook.post",
        }
    ),
}
_DEFERRED_CONTAINER_NODE_TYPES = frozenset({"workflowNode", "loopNode"})


def _normalized_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _node_process_data(process_data: Any) -> dict[str, Any]:
    if not isinstance(process_data, dict):
        return {}
    node_options = process_data.get("node_options")
    if isinstance(node_options, dict):
        return node_options
    return process_data


def uses_metadata_only_provider_capture(
    node_type: str | None,
    process_data: Any,
    trace_metadata: Any = None,
) -> bool:
    node_data = _node_process_data(process_data)
    metadata = trace_metadata if isinstance(trace_metadata, dict) else {}
    output_policy = (
        metadata.get("external_effect_output")
        if isinstance(metadata.get("external_effect_output"), dict)
        else {}
    )
    if (
        process_data.get("_external_effect_output_sensitive") is True
        if isinstance(process_data, dict)
        else False
    ) or output_policy.get("sensitive") is True:
        return True
    if node_type in {"httpRequestNode", "slackPostNode"}:
        return True
    if node_type != "githubNode":
        return False
    if (
        str(_normalized_value(node_data.get("action", "get_pr"))) == "comment_pr"
    ):
        return True
    effect = (
        metadata.get("external_effect")
        if isinstance(metadata.get("external_effect"), dict)
        else {}
    )
    return (
        effect.get("provider") == "github"
        and effect.get("operation") == "github.issue_comment.create"
    )


def defers_provider_capture_until_finish(node_type: str | None) -> bool:
    return node_type in _DEFERRED_CONTAINER_NODE_TYPES


def _safe_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def durable_provider_summary(
    *,
    node_type: str | None,
    process_data: Any,
    trace_metadata: Any,
) -> dict[str, Any] | None:
    if not uses_metadata_only_provider_capture(
        node_type,
        process_data,
        trace_metadata,
    ):
        return None
    if node_type not in _PROVIDER_OPERATION_BY_NODE_TYPE:
        return {}

    expected_provider, default_operation = _PROVIDER_OPERATION_BY_NODE_TYPE[node_type]
    metadata = trace_metadata if isinstance(trace_metadata, dict) else {}
    http = metadata.get("http") if isinstance(metadata.get("http"), dict) else {}
    slack = metadata.get("slack") if isinstance(metadata.get("slack"), dict) else {}
    effect = (
        metadata.get("external_effect")
        if isinstance(metadata.get("external_effect"), dict)
        else {}
    )
    allowed_operations = _PROVIDER_OPERATIONS_BY_NODE_TYPE.get(
        node_type,
        frozenset({default_operation}),
    )
    effect_operation = effect.get("operation")
    if (
        effect.get("provider") == expected_provider
        and effect_operation in allowed_operations
    ):
        expected_operation = effect_operation
    elif node_type == "slackPostNode" and slack.get("delivery_mode") == "api":
        expected_operation = "slack.chat.post_message"
    elif node_type == "slackPostNode" and slack.get("delivery_mode") == "webhook":
        expected_operation = "slack.incoming_webhook.post"
    else:
        expected_operation = default_operation
    summary: dict[str, Any] = {
        "provider": expected_provider,
        "operation": expected_operation,
    }

    node_data = _node_process_data(process_data)
    method = _normalized_value(http.get("method"))
    if method is None:
        method = _normalized_value(node_data.get("method"))
    method = str(method or "").upper()
    if method in _HTTP_METHODS:
        summary["method"] = method

    provider_metadata = slack if node_type == "slackPostNode" and slack else http
    status = provider_metadata.get("status_code")
    if (
        not isinstance(status, bool)
        and isinstance(status, int)
        and 100 <= status <= 599
    ):
        summary["status"] = status

    for field in ("request_size", "response_size", "latency_ms"):
        value = _safe_non_negative_int(provider_metadata.get(field))
        if value is not None:
            summary[field] = value

    if (
        effect.get("provider") == expected_provider
        and effect.get("operation") == expected_operation
    ):
        outcome = effect.get("outcome")
        if outcome in _OUTCOMES:
            summary["outcome"] = outcome
        replay_decision = effect.get("replay_decision")
        if replay_decision in _REPLAY_DECISIONS:
            summary["replay_decision"] = replay_decision
        error_code = effect.get("error_code")
        if error_code in ALLOWED_EFFECT_ERROR_CODES:
            summary["error_code"] = error_code

    return {"external_effect": summary}


def sanitize_provider_trace_records(
    records: Any,
    *,
    node_type: str | None,
    process_data: Any,
    trace_metadata: Any,
) -> list[dict[str, Any]]:
    summary = durable_provider_summary(
        node_type=node_type,
        process_data=process_data,
        trace_metadata=trace_metadata,
    )
    if summary is None:
        return list(records or [])

    sanitized: list[dict[str, Any]] = []
    for record in records or []:
        if not isinstance(record, dict) or record.get("payload_kind") != "output":
            continue
        safe_record = dict(record)
        safe_record["redacted_payload"] = summary
        safe_record["raw_payload_encrypted"] = None
        safe_record["redaction_metadata"] = None
        safe_record["secret_detected"] = False
        safe_record["storage_mode"] = "redacted_only"
        sanitized.append(safe_record)
    return sanitized[:1]
