import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from apps.shared.services.knowledge_document_ingestion_projection import (
    project_safe_ingestion_job,
)


def test_projection_exposes_only_allowlisted_operational_fields() -> None:
    job_id = uuid.uuid4()
    value = SimpleNamespace(
        id=job_id,
        operation_kind="process",
        status="running",
        retryable=True,
        attempt_count=1,
        max_attempts=3,
        safe_reason_code=None,
        requested_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
        started_at=datetime(2026, 7, 16, 0, 0, 1, tzinfo=timezone.utc),
        completed_at=None,
        owner_token="must-not-leak",
        fencing_token="must-not-leak",
        input_revision="must-not-leak",
        idempotency_key="must-not-leak",
        safe_metadata={"source_url": "must-not-leak"},
    )

    projected = project_safe_ingestion_job(value)

    assert projected == {
        "job_id": str(job_id),
        "operation": "process",
        "status": "running",
        "progress": "running",
        "retryable": True,
        "attempt_count": 1,
        "max_attempts": 3,
        "safe_reason_code": None,
        "requested_at": "2026-07-16T00:00:00+00:00",
        "started_at": "2026-07-16T00:00:01+00:00",
        "completed_at": None,
    }


def test_projection_lowers_unknown_reason_and_rejects_invalid_state() -> None:
    value = SimpleNamespace(
        id=uuid.uuid4(),
        operation_kind="sync",
        status="dead_lettered",
        retryable=False,
        safe_reason_code="postgresql://private-host/raw-error",
    )

    projected = project_safe_ingestion_job(value)

    assert projected is not None
    assert projected["safe_reason_code"] == "ingestion.internal_error"
    assert project_safe_ingestion_job(
        SimpleNamespace(id=uuid.uuid4(), operation_kind="unknown", status="pending")
    ) is None
