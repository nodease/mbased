import uuid

from apps.shared.schemas.rag import ChunkPreview
from apps.shared.services.rag_evidence_policy import (
    PII_POLICY_BLOCK_REASON,
    blocked_evidence_reason_for_chunks,
)


def _chunk_with_classification(classification: str | None) -> ChunkPreview:
    metadata_summary = {}
    if classification is not None:
        metadata_summary["classification"] = classification
    return ChunkPreview(
        chunk_id=uuid.uuid4(),
        content="redacted canonical evidence",
        document_id=uuid.uuid4(),
        filename="policy.md",
        similarity_score=0.9,
        score=0.9,
        metadata_summary=metadata_summary,
    )


def test_blocked_evidence_reason_detects_pii_classification():
    chunks = [
        _chunk_with_classification("internal"),
        _chunk_with_classification("PII"),
    ]

    assert blocked_evidence_reason_for_chunks(chunks) == PII_POLICY_BLOCK_REASON


def test_blocked_evidence_reason_allows_non_blocked_classifications():
    chunks = [
        _chunk_with_classification("public"),
        _chunk_with_classification("internal"),
        _chunk_with_classification(None),
    ]

    assert blocked_evidence_reason_for_chunks(chunks) is None
