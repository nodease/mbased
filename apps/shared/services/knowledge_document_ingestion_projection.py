from __future__ import annotations

from datetime import datetime
from typing import Any

from apps.shared.domain.knowledge_document_ingestion import safe_reason_code

_PROGRESS_CATEGORY = {
    "pending": "queued",
    "running": "running",
    "retry_scheduled": "retrying",
    "succeeded": "complete",
    "dead_lettered": "complete",
    "cancelled": "complete",
}


def project_safe_ingestion_job(value: Any) -> dict[str, object] | None:
    if value is None:
        return None
    status = str(getattr(value, "status", ""))
    operation = str(
        getattr(value, "operation", None) or getattr(value, "operation_kind", "")
    )
    if status not in _PROGRESS_CATEGORY or operation not in {
        "process",
        "sync",
        "resume",
        "reindex",
    }:
        return None
    job_id = getattr(value, "job_id", None) or getattr(value, "id", None)
    if job_id is None:
        return None
    return {
        "job_id": str(job_id),
        "operation": operation,
        "status": status,
        "progress": _PROGRESS_CATEGORY[status],
        "retryable": bool(getattr(value, "retryable", False)),
        "attempt_count": max(0, int(getattr(value, "attempt_count", 0) or 0)),
        "max_attempts": max(1, int(getattr(value, "max_attempts", 1) or 1)),
        "safe_reason_code": safe_reason_code(getattr(value, "safe_reason_code", None)),
        "requested_at": _timestamp(getattr(value, "requested_at", None)),
        "started_at": _timestamp(getattr(value, "started_at", None)),
        "completed_at": _timestamp(getattr(value, "completed_at", None)),
    }


def _timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None
