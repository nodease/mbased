from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

_PROGRESS_FIELDS = ("progress", "processing_progress")
_TIMESTAMP_FIELDS = (
    "processing_enqueued_at",
    "processing_started_at",
    "processing_progress_updated_at",
)
_BOOLEAN_FIELDS = (
    "processing_recovered_from_timeout",
    "remove_urls_emails",
    "remove_whitespace",
)
_ENUM_FIELDS = {
    "chunking_mode": frozenset({"flat", "hierarchical"}),
    "strategy": frozenset({"general", "llamaparse"}),
    "upload_method": frozenset({"backend", "direct"}),
}
_COST_FIELD_LIMITS = {
    "pages": 1_000_000,
    "credits": 1_000_000_000,
    "cost_usd": 1_000_000,
}
_PUBLIC_DOCUMENT_STATUSES = frozenset(
    {
        "pending",
        "indexing",
        "processing",
        "waiting_for_approval",
        "completed",
        "failed",
    }
)
_PUBLIC_PROGRESS_MESSAGES = {
    "pending": "Document processing is queued.",
    "indexing": "Document processing is in progress.",
    "processing": "Document processing is in progress.",
    "waiting_for_approval": "Document processing is waiting for approval.",
    "completed": "Document processing completed.",
    "failed": "Document processing failed. You can retry the document.",
}
_PUBLIC_FAILURE_MESSAGE = "Document processing failed. You can retry the document."
_PUBLIC_NOTICE_MESSAGE = "Document processing completed with a notice."


def project_safe_document_metadata(meta_info: Any) -> dict[str, object]:
    """Project internal document metadata to the KB read-response allowlist."""

    if not isinstance(meta_info, Mapping):
        return {}

    projected: dict[str, object] = {}
    for field in _PROGRESS_FIELDS:
        value = _bounded_number(meta_info.get(field), upper=100)
        if value is not None:
            projected[field] = value

    for field in _TIMESTAMP_FIELDS:
        timestamp = _aware_iso_timestamp(meta_info.get(field))
        if timestamp is not None:
            projected[field] = timestamp

    for field in _BOOLEAN_FIELDS:
        value = meta_info.get(field)
        if isinstance(value, bool):
            projected[field] = value

    for field, allowed_values in _ENUM_FIELDS.items():
        value = meta_info.get(field)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in allowed_values:
                projected[field] = normalized

    cost_estimate = _safe_cost_estimate(meta_info.get("cost_estimate"))
    if cost_estimate:
        projected["cost_estimate"] = cost_estimate

    return projected


def project_safe_document_status(value: Any) -> str:
    if value is None:
        return "pending"
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _PUBLIC_DOCUMENT_STATUSES:
            return normalized
    return "failed"


def project_safe_document_error(status: Any, persisted_error: Any) -> str | None:
    public_status = project_safe_document_status(status)
    if public_status == "failed":
        return _PUBLIC_FAILURE_MESSAGE
    if (
        public_status == "completed"
        and isinstance(persisted_error, str)
        and bool(persisted_error.strip())
    ):
        return _PUBLIC_NOTICE_MESSAGE
    return None


def project_safe_document_progress_message(status: Any) -> str:
    public_status = project_safe_document_status(status)
    return _PUBLIC_PROGRESS_MESSAGES[public_status]


def project_safe_document_progress(status: Any, redis_progress: Any) -> int:
    public_status = project_safe_document_status(status)
    if public_status == "completed":
        return 100
    if public_status == "failed":
        return 0
    if public_status not in {"indexing", "processing"}:
        return 0
    if isinstance(redis_progress, bool):
        return 0
    if isinstance(redis_progress, int):
        progress = redis_progress
    elif isinstance(redis_progress, (bytes, str)):
        if len(redis_progress) > 3:
            return 0
        try:
            progress = int(redis_progress)
        except (TypeError, ValueError):
            return 0
    else:
        return 0
    return progress if 0 <= progress <= 100 else 0


def _aware_iso_timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    timestamp = value.strip()
    if not timestamp or len(timestamp) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return timestamp


def _safe_cost_estimate(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, int | float] = {}
    for field, upper in _COST_FIELD_LIMITS.items():
        number = _bounded_number(value.get(field), upper=upper)
        if number is not None:
            projected[field] = number
    return projected


def _bounded_number(value: Any, *, upper: int) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or value > upper:
        return None
    return value
