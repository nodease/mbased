import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from apps.shared.db.models.knowledge import (
    Document,
    DocumentChunk,
    DocumentVersion,
    KnowledgeBase,
)
from apps.shared.services.knowledge_ingestion_fencing import KnowledgeIngestionFencing
from apps.shared.services.knowledge_ingestion_outbox import (
    OUTBOX_EVENT_CLEANUP_SUPERSEDED,
    KnowledgeIngestionOutboxService,
)
from sqlalchemy import func
from sqlalchemy.orm import Session

DEFAULT_PROCESSING_POLICY_VERSION = "knowledge-ingestion-v1"


class KnowledgeIngestionFinalizationError(RuntimeError):
    """Active version finalization이 안전하게 완료될 수 없을 때 사용한다."""


@dataclass(frozen=True)
class KnowledgeFinalizationResult:
    document_version_id: uuid.UUID
    previous_document_version_id: uuid.UUID | None
    chunk_count: int
    deleted_legacy_chunk_count: int
    outbox_event_id: uuid.UUID | None


class KnowledgeIngestionFinalizer:
    """DocumentVersion을 retrieval-visible active version으로 짧게 교체한다."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_indexing_version(
        self,
        document: Document,
        *,
        content_hash: str,
        chunking_fingerprint: str | None,
        embedding_model: str,
        processing_policy_version: str = DEFAULT_PROCESSING_POLICY_VERSION,
        safe_metadata: dict[str, Any] | None = None,
        fencing_token: str | None = None,
    ) -> DocumentVersion | None:
        kb = document.knowledge_base or self.db.get(
            KnowledgeBase, document.knowledge_base_id
        )
        if not kb or kb.organization_id is None:
            return None

        version_safe_metadata = KnowledgeIngestionFencing.version_metadata(
            safe_metadata, fencing_token
        )

        version = DocumentVersion(
            id=uuid.uuid4(),
            organization_id=kb.organization_id,
            knowledge_base_id=kb.id,
            legacy_document_id=document.id,
            source_identity_id=kb.source_identity_id,
            version_number=self._next_version_number(kb),
            status="indexing",
            content_hash=content_hash,
            chunking_fingerprint=chunking_fingerprint,
            embedding_model=embedding_model,
            processing_policy_version=processing_policy_version,
            safe_metadata=version_safe_metadata,
        )
        self.db.add(version)
        return version

    def finalize_active_version(
        self,
        document_version: DocumentVersion,
        *,
        expected_fencing_token: str | None = None,
        now: datetime | None = None,
    ) -> KnowledgeFinalizationResult:
        now = now or self._now()
        version = self._lock_document_version(document_version.id)
        kb = self._lock_knowledge_base(version.knowledge_base_id)

        if version.knowledge_base_id != kb.id:
            raise KnowledgeIngestionFinalizationError("version_kb_mismatch")
        if version.status not in {"staging", "indexing"}:
            raise KnowledgeIngestionFinalizationError("version_not_finalizable")
        self._verify_fencing_token(version, expected_fencing_token)

        chunk_count = self._version_chunk_count(version.id)
        if chunk_count <= 0:
            raise KnowledgeIngestionFinalizationError("version_has_no_chunks")

        previous_version_id = kb.active_document_version_id
        previous_version = (
            self._previous_active_version(previous_version_id)
            if previous_version_id and previous_version_id != version.id
            else None
        )

        version.status = "ready"
        version.ready_at = now
        version.error_code = None
        version.updated_at = now
        kb.active_document_version_id = version.id
        kb.updated_at = now
        legacy_document = self._lock_legacy_document(version.legacy_document_id)
        if legacy_document:
            legacy_document.content_hash = version.content_hash
            legacy_document.embedding_model = version.embedding_model
            legacy_document.meta_info = (
                KnowledgeIngestionFencing.clear_active_document_metadata(
                    getattr(legacy_document, "meta_info", None)
                )
            )
            legacy_document.updated_at = now

        if previous_version:
            previous_version.status = "superseded"
            previous_version.superseded_at = now
            previous_version.updated_at = now

        legacy_deleted_count = self._delete_legacy_unversioned_chunks(version)
        outbox_event = self._enqueue_cleanup_event(
            version=version,
            previous_version_id=previous_version_id,
            deleted_legacy_chunk_count=legacy_deleted_count,
        )
        self.db.flush()
        return KnowledgeFinalizationResult(
            document_version_id=version.id,
            previous_document_version_id=previous_version_id,
            chunk_count=chunk_count,
            deleted_legacy_chunk_count=legacy_deleted_count,
            outbox_event_id=outbox_event.id if outbox_event else None,
        )

    def mark_failed(
        self,
        document_version_id: uuid.UUID,
        *,
        safe_reason_code: str,
        now: datetime | None = None,
    ) -> None:
        now = now or self._now()
        version = self.db.get(DocumentVersion, document_version_id)
        if not version or version.status == "ready":
            return
        version.status = "failed"
        version.error_code = safe_reason_code
        version.updated_at = now
        self.db.flush()

    def record_failed_indexing_version(
        self,
        *,
        organization_id: uuid.UUID,
        knowledge_base_id: uuid.UUID,
        legacy_document_id: uuid.UUID | None,
        source_identity_id: uuid.UUID | None,
        content_hash: str | None,
        chunking_fingerprint: str | None,
        embedding_model: str | None,
        safe_reason_code: str,
        safe_metadata: dict[str, Any] | None = None,
        fencing_token: str | None = None,
        now: datetime | None = None,
    ) -> DocumentVersion:
        now = now or self._now()
        kb = self.db.get(KnowledgeBase, knowledge_base_id)
        if not kb or kb.organization_id != organization_id:
            raise KnowledgeIngestionFinalizationError("failed_version_kb_mismatch")
        version_safe_metadata = KnowledgeIngestionFencing.version_metadata(
            safe_metadata, fencing_token
        )
        version = DocumentVersion(
            id=uuid.uuid4(),
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            legacy_document_id=legacy_document_id,
            source_identity_id=source_identity_id,
            version_number=self._next_version_number(kb),
            status="failed",
            content_hash=content_hash,
            chunking_fingerprint=chunking_fingerprint,
            embedding_model=embedding_model,
            processing_policy_version=DEFAULT_PROCESSING_POLICY_VERSION,
            safe_metadata=version_safe_metadata,
            error_code=safe_reason_code,
            updated_at=now,
        )
        self.db.add(version)
        self.db.flush()
        return version

    def _next_version_number(self, kb: KnowledgeBase) -> int:
        current = (
            self.db.query(func.max(DocumentVersion.version_number))
            .filter(DocumentVersion.knowledge_base_id == kb.id)
            .scalar()
        )
        return int(current or 0) + 1

    def _lock_document_version(self, document_version_id: uuid.UUID) -> DocumentVersion:
        version = (
            self.db.query(DocumentVersion)
            .filter(DocumentVersion.id == document_version_id)
            .with_for_update()
            .one()
        )
        return version

    def _lock_knowledge_base(self, knowledge_base_id: uuid.UUID) -> KnowledgeBase:
        return (
            self.db.query(KnowledgeBase)
            .filter(KnowledgeBase.id == knowledge_base_id)
            .with_for_update()
            .one()
        )

    def _previous_active_version(
        self, previous_version_id: uuid.UUID | None
    ) -> DocumentVersion | None:
        if not previous_version_id:
            return None
        return self.db.get(DocumentVersion, previous_version_id)

    def _version_chunk_count(self, document_version_id: uuid.UUID) -> int:
        return int(
            self.db.query(func.count(DocumentChunk.id))
            .filter(DocumentChunk.document_version_id == document_version_id)
            .scalar()
            or 0
        )

    def _delete_legacy_unversioned_chunks(self, version: DocumentVersion) -> int:
        if not version.legacy_document_id:
            return 0
        deleted_count = (
            self.db.query(DocumentChunk)
            .filter(
                DocumentChunk.document_id == version.legacy_document_id,
                DocumentChunk.knowledge_base_id == version.knowledge_base_id,
                DocumentChunk.document_version_id.is_(None),
            )
            .delete(synchronize_session=False)
        )
        return int(deleted_count or 0)

    def _enqueue_cleanup_event(
        self,
        *,
        version: DocumentVersion,
        previous_version_id: uuid.UUID | None,
        deleted_legacy_chunk_count: int,
    ):
        if not version.organization_id:
            return None
        return KnowledgeIngestionOutboxService(self.db).enqueue(
            organization_id=version.organization_id,
            knowledge_base_id=version.knowledge_base_id,
            document_version_id=version.id,
            source_identity_id=version.source_identity_id,
            event_type=OUTBOX_EVENT_CLEANUP_SUPERSEDED,
            idempotency_key=f"document-version:{version.id}:cleanup-superseded",
            target_ref={
                "document_version_id": str(version.id),
                "previous_document_version_id": (
                    str(previous_version_id) if previous_version_id else None
                ),
            },
            safe_metadata={
                "deleted_legacy_chunk_count": deleted_legacy_chunk_count,
            },
        )

    def _verify_fencing_token(
        self,
        version: DocumentVersion,
        expected_fencing_token: str | None,
    ) -> None:
        if not expected_fencing_token:
            return
        expected_hash = KnowledgeIngestionFencing.hash_token(expected_fencing_token)
        version_hash = KnowledgeIngestionFencing.version_hash(version)
        if version_hash != expected_hash:
            raise KnowledgeIngestionFinalizationError("fencing_token_mismatch")

        if not version.legacy_document_id:
            return
        legacy_document = self._lock_legacy_document(version.legacy_document_id)
        if not legacy_document:
            return
        active_hash = KnowledgeIngestionFencing.active_document_hash(legacy_document)
        if active_hash != expected_hash:
            raise KnowledgeIngestionFinalizationError("stale_ingestion_worker")

    def _lock_legacy_document(
        self, legacy_document_id: uuid.UUID | None
    ) -> Document | None:
        if not legacy_document_id:
            return None
        return (
            self.db.query(Document)
            .filter(Document.id == legacy_document_id)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
