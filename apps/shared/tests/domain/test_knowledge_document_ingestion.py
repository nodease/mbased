import hashlib
import uuid

import pytest
from apps.shared.domain.knowledge_document_ingestion import (
    KnowledgeDocumentIngestionStateError,
    build_idempotency_key,
    build_input_revision,
    retry_delay,
    safe_reason_code,
    validate_runtime_settings,
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_input_revision_is_deterministic_and_intent_sensitive() -> None:
    document_id = uuid.uuid4()
    kwargs = {
        "document_id": document_id,
        "operation": "process",
        "generation": 1,
        "active_document_version_id": uuid.uuid4(),
        "content_hash": _sha256(b"content"),
        "chunking_fingerprint": _sha256(b"chunking"),
        "embedding_model": "text-embedding-3-small",
        "protected_source_revision": _sha256(b"opaque-source-revision"),
    }

    first = build_input_revision(**kwargs)
    second = build_input_revision(**kwargs)
    changed = build_input_revision(**{**kwargs, "operation": "sync"})

    assert first == second
    assert first != changed
    assert len(bytes.fromhex(first)) == 32


def test_input_revision_rejects_raw_or_malformed_digest_material() -> None:
    with pytest.raises(KnowledgeDocumentIngestionStateError):
        build_input_revision(
            document_id=uuid.uuid4(),
            operation="process",
            generation=1,
            active_document_version_id=None,
            content_hash="s3://private-bucket/raw.pdf",
            chunking_fingerprint=None,
            embedding_model="text-embedding-3-small",
        )


def test_input_revision_accepts_versioned_sha256_fingerprint() -> None:
    revision = build_input_revision(
        document_id=uuid.uuid4(),
        operation="process",
        generation=1,
        active_document_version_id=None,
        content_hash=None,
        chunking_fingerprint=f"sha256:{_sha256(b'chunking')}",
        embedding_model="text-embedding-3-small",
    )

    assert len(bytes.fromhex(revision)) == 32


@pytest.mark.parametrize("generation", [0, -1])
def test_input_revision_rejects_nonpositive_generation(generation: int) -> None:
    with pytest.raises(KnowledgeDocumentIngestionStateError):
        build_input_revision(
            document_id=uuid.uuid4(),
            operation="process",
            generation=generation,
            active_document_version_id=None,
            content_hash=None,
            chunking_fingerprint=None,
            embedding_model="text-embedding-3-small",
        )


def test_idempotency_key_is_scoped_by_organization_and_generation() -> None:
    organization_id = uuid.uuid4()
    document_id = uuid.uuid4()
    revision = _sha256(b"revision")

    first = build_idempotency_key(
        organization_id=organization_id,
        document_id=document_id,
        operation="process",
        generation=1,
        input_revision=revision,
    )
    same = build_idempotency_key(
        organization_id=organization_id,
        document_id=document_id,
        operation="process",
        generation=1,
        input_revision=revision,
    )
    next_generation = build_idempotency_key(
        organization_id=organization_id,
        document_id=document_id,
        operation="process",
        generation=2,
        input_revision=revision,
    )

    assert first == same
    assert first != next_generation


def test_retry_delay_is_deterministic_and_bounded() -> None:
    job_id = uuid.uuid4()
    delays = [
        retry_delay(job_id=job_id, attempt_count=attempt).total_seconds()
        for attempt in range(1, 10)
    ]

    assert delays == [
        retry_delay(job_id=job_id, attempt_count=attempt).total_seconds()
        for attempt in range(1, 10)
    ]
    bases = [15, 30, 60, 120, 240, 300, 300, 300, 300]
    assert all(base <= delay <= base * 1.25 for base, delay in zip(bases, delays))


def test_runtime_settings_require_lease_headroom() -> None:
    validate_runtime_settings(lease_seconds=900, heartbeat_seconds=30)

    with pytest.raises(KnowledgeDocumentIngestionStateError):
        validate_runtime_settings(lease_seconds=60, heartbeat_seconds=30)


def test_unknown_reason_is_mapped_to_safe_internal_code() -> None:
    assert safe_reason_code("provider leaked detail") == "ingestion.internal_error"
    assert safe_reason_code("ingestion.timeout") == "ingestion.timeout"
    assert (
        safe_reason_code("knowledge.raw_parser_egress_unavailable")
        == "knowledge.raw_parser_egress_unavailable"
    )
    assert safe_reason_code(None) is None
