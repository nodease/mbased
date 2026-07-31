from __future__ import annotations

from typing import Any, Mapping

_MAIL_EFFECT_NODE_TYPES = frozenset({"gmailDraftNode", "mailAcknowledgeNode"})
_MAX_INPUT_NODE_COUNT = 1000


def sanitize_mail_trace_payload(
    *,
    node_type: str | None,
    payload_kind: str,
    value: Any,
    mail_sensitive_lineage: bool = False,
) -> Any:
    """Remove Mail content before general trace policy processing."""
    if payload_kind == "input" and node_type in _MAIL_EFFECT_NODE_TYPES:
        input_node_count = len(value) if isinstance(value, Mapping) else 0
        return {
            "mail_content_redacted": True,
            "input_node_count": min(input_node_count, _MAX_INPUT_NODE_COUNT),
        }
    if payload_kind == "output" and node_type == "mailNode":
        return _mail_result_summary(value)
    if mail_sensitive_lineage:
        return _lineage_summary(value)
    return _redact_embedded_mail_results(value)


def _redact_embedded_mail_results(value: Any) -> Any:
    if isinstance(value, Mapping):
        if _looks_like_mail_result(value):
            return _mail_result_summary(value)
        return {
            str(key): _redact_embedded_mail_results(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_embedded_mail_results(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_embedded_mail_results(item) for item in value]
    return value


def _looks_like_mail_result(value: Mapping[str, Any]) -> bool:
    return (
        isinstance(value.get("emails"), list)
        and isinstance(value.get("total_count"), int)
        and isinstance(value.get("folder"), str)
    )


def _mail_result_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not _looks_like_mail_result(value):
        return {"mail_content_redacted": True, "total_count": 0}
    total_count = value.get("total_count", 0)
    return {
        "mail_content_redacted": True,
        "total_count": max(0, min(total_count, 100))
        if isinstance(total_count, int)
        else 0,
        "folder": _safe_folder(value.get("folder")),
    }


def _safe_folder(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"INBOX", "SENT", "DRAFTS", "SPAM", "TRASH"}:
        return normalized
    return "UNKNOWN"


def _lineage_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {
            "mail_content_redacted": True,
            "payload_field_count": min(len(value), 1000),
        }
    if isinstance(value, (list, tuple)):
        return {
            "mail_content_redacted": True,
            "payload_item_count": min(len(value), 1000),
        }
    return {"mail_content_redacted": True}
