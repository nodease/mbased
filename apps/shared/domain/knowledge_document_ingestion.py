from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Literal

KnowledgeDocumentIngestionOperation = Literal[
    "process",
    "sync",
    "resume",
    "reindex",
]
KnowledgeDocumentIngestionStatus = Literal[
    "pending",
    "running",
    "retry_scheduled",
    "succeeded",
    "dead_lettered",
    "cancelled",
]

ACTIVE_JOB_STATUSES = frozenset({"pending", "running", "retry_scheduled"})
TERMINAL_JOB_STATUSES = frozenset({"succeeded", "dead_lettered", "cancelled"})
SUPPORTED_OPERATIONS = frozenset({"process", "sync", "resume", "reindex"})

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 15 * 60
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_DISPATCH_LEASE_SECONDS = 2 * 60
DEFAULT_RECOVERY_BATCH_SIZE = 25
DEFAULT_TERMINAL_RETENTION_DAYS = 30
RAW_PARSER_EGRESS_UNAVAILABLE_REASON = "knowledge.raw_parser_egress_unavailable"

SAFE_REASON_CODES = frozenset(
    {
        "ingestion.authorization_revoked",
        "ingestion.configuration_invalid",
        "ingestion.document_missing",
        "ingestion.internal_error",
        "ingestion.lease_lost",
        "ingestion.lifecycle_blocked",
        "ingestion.no_content",
        "ingestion.processing_failed",
        "ingestion.retry_not_available",
        "ingestion.source_temporarily_unavailable",
        "ingestion.superseded",
        "ingestion.timeout",
        "ingestion.worker_interrupted",
        RAW_PARSER_EGRESS_UNAVAILABLE_REASON,
    }
)


class KnowledgeDocumentIngestionStateError(ValueError):
    pass


def safe_reason_code(value: str | None) -> str | None:
    if value is None:
        return None
    if value in SAFE_REASON_CODES:
        return value
    return "ingestion.internal_error"


def build_input_revision(
    *,
    document_id: uuid.UUID,
    operation: str,
    generation: int,
    active_document_version_id: uuid.UUID | None,
    content_hash: str | None,
    chunking_fingerprint: str | None,
    embedding_model: str,
    protected_source_revision: str | None = None,
) -> str:
    """Build a revision from opaque identifiers and pre-protected digests only."""

    _require_operation(operation)
    if generation < 1:
        raise KnowledgeDocumentIngestionStateError("generation must be positive")
    if not embedding_model or len(embedding_model) > 255:
        raise KnowledgeDocumentIngestionStateError("embedding model is invalid")

    digest = hashlib.sha256(b"knowledge-document-input-v1\x00")
    _frame(digest, document_id.bytes)
    _frame(digest, operation.encode("ascii"))
    _frame(digest, generation.to_bytes(8, byteorder="big", signed=False))
    _frame(digest, active_document_version_id.bytes if active_document_version_id else b"")
    _frame_digest(digest, content_hash)
    _frame_digest(digest, chunking_fingerprint)
    _frame(digest, embedding_model.encode("utf-8"))
    _frame_digest(digest, protected_source_revision)
    return digest.hexdigest()


def build_idempotency_key(
    *,
    organization_id: uuid.UUID,
    document_id: uuid.UUID,
    operation: str,
    generation: int,
    input_revision: str,
) -> str:
    _require_operation(operation)
    if generation < 1:
        raise KnowledgeDocumentIngestionStateError("generation must be positive")
    _require_sha256(input_revision, "input revision")

    digest = hashlib.sha256(b"knowledge-document-ingestion-v1\x00")
    _frame(digest, organization_id.bytes)
    _frame(digest, document_id.bytes)
    _frame(digest, operation.encode("ascii"))
    _frame(digest, generation.to_bytes(8, byteorder="big", signed=False))
    _frame(digest, bytes.fromhex(input_revision))
    return digest.hexdigest()


def retry_delay(*, job_id: uuid.UUID, attempt_count: int) -> timedelta:
    """Return bounded exponential backoff with deterministic per-job jitter."""

    if attempt_count < 1:
        raise KnowledgeDocumentIngestionStateError("attempt count must be positive")
    base_seconds = min(15 * (2 ** (attempt_count - 1)), 5 * 60)
    jitter_seed = hashlib.sha256(
        b"knowledge-document-retry-v1\x00"
        + job_id.bytes
        + attempt_count.to_bytes(8, byteorder="big", signed=False)
    ).digest()
    jitter_seconds = int.from_bytes(jitter_seed[:2], "big") % max(
        1, base_seconds // 4 + 1
    )
    return timedelta(seconds=base_seconds + jitter_seconds)


def validate_runtime_settings(*, lease_seconds: int, heartbeat_seconds: int) -> None:
    if heartbeat_seconds < 1:
        raise KnowledgeDocumentIngestionStateError(
            "heartbeat interval must be positive"
        )
    if lease_seconds <= heartbeat_seconds * 2:
        raise KnowledgeDocumentIngestionStateError(
            "lease must exceed two heartbeat intervals"
        )


def _require_operation(value: str) -> None:
    if value not in SUPPORTED_OPERATIONS:
        raise KnowledgeDocumentIngestionStateError("operation is not supported")


def _frame(digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(4, byteorder="big", signed=False))
    digest.update(value)


def _frame_digest(digest, value: str | None) -> None:
    if not value:
        _frame(digest, b"")
        return
    _frame(digest, _decode_sha256(value, "protected digest"))


def _require_sha256(value: str, label: str) -> None:
    _decode_sha256(value, label)


def _decode_sha256(value: str, label: str) -> bytes:
    normalized = value.removeprefix("sha256:")
    try:
        encoded = bytes.fromhex(normalized)
    except ValueError as exc:
        raise KnowledgeDocumentIngestionStateError(
            f"{label} must be hexadecimal"
        ) from exc
    if len(encoded) != 32:
        raise KnowledgeDocumentIngestionStateError(
            f"{label} must be a SHA-256 digest"
        )
    return encoded
