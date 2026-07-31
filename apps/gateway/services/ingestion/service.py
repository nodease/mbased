import hashlib
import json
import logging
import re
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID, uuid4

import tiktoken
from fastapi import UploadFile
from sqlalchemy.orm import Session

from apps.gateway.services.ingestion.factory import IngestionFactory
from apps.gateway.services.storage import get_storage_service
from apps.shared.db.models.knowledge import (
    Document,
    DocumentChunk,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeDocumentIngestionJob,
    SourceType,
)
from apps.shared.db.session import SessionLocal
from apps.shared.distributed_lock import DistributedLock
from apps.shared.domain.knowledge_document_ingestion import (
    RAW_PARSER_EGRESS_UNAVAILABLE_REASON,
)
from apps.shared.services.knowledge_ingestion_fencing import (
    ACTIVE_FENCING_TOKEN_HASH_KEY,
    KnowledgeIngestionFencing,
)
from apps.shared.services.knowledge_ingestion_finalizer import (
    KnowledgeIngestionFinalizationError,
    KnowledgeIngestionFinalizer,
)
from apps.shared.services.ingestion.chunk_selection import (
    filter_chunks_by_selection,
)
from apps.shared.services.ingestion.hierarchical_chunker import (
    HierarchicalChunker,
    HierarchicalChunkerConfig,
    filter_hierarchical_chunks,
)
from apps.shared.services.ingestion.vector_store_service import (
    acquire_document_write_lock,
)
from apps.shared.services.rag_hierarchy import (
    CHUNKING_MODE_HIERARCHICAL,
    HIERARCHY_VERSION,
    chunking_fingerprint_hash,
    parent_overlap_size,
    parent_target_size,
    validate_chunking_request,
)

logger = logging.getLogger(__name__)
PROCESSING_START_TIMEOUT_SECONDS = 60
ACTIVE_PROCESSING_STALL_TIMEOUT_SECONDS = 900
PROCESSING_START_TIMEOUT_MESSAGE = (
    "Document processing did not start in time. Please retry."
)
ACTIVE_PROCESSING_STALL_TIMEOUT_MESSAGE = (
    "Document processing did not complete in time. Please retry."
)
_SAFE_PREVIEW_SOURCE_REASON_CODES = frozenset(
    {
        "configuration.invalid",
        "resource.hidden",
        "source.temporarily_unavailable",
        RAW_PARSER_EGRESS_UNAVAILABLE_REASON,
    }
)


def _recursive_character_text_splitter(**kwargs: Any) -> Any:
    # Import lazily so unrelated Gateway endpoints do not pay the heavy
    # langchain/nltk import cost during application startup.
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    return RecursiveCharacterTextSplitter(**kwargs)


def _nltk_resources_available(nltk: Any) -> bool:
    for resource in (
        "tokenizers/punkt",
        "tokenizers/punkt_tab",
        "corpora/stopwords",
    ):
        try:
            nltk.data.find(resource)
        except LookupError:
            return False
    return True


def _pre_finalization_chunk_progress(completed: int, total: int) -> int:
    safe_total = max(1, int(total))
    safe_completed = max(0, min(int(completed), safe_total))
    return min(99, 80 + int((safe_completed / safe_total) * 20))


class IngestionPreviewSourceError(ValueError):
    """Carry only a safe processor reason across the preview service boundary."""

    def __init__(self, reason_code: object) -> None:
        normalized_reason = str(reason_code)
        self.reason_code = (
            normalized_reason
            if normalized_reason in _SAFE_PREVIEW_SOURCE_REASON_CODES
            else "configuration.invalid"
        )
        super().__init__("Source preview failed.")


def _aware_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def mark_document_processing_queued(doc: Document) -> None:
    meta_info = dict(doc.meta_info or {})
    meta_info.pop(ACTIVE_FENCING_TOKEN_HASH_KEY, None)
    meta_info.pop("processing_started_at", None)
    meta_info.pop("processing_recovered_from_timeout", None)
    meta_info["progress"] = 0
    meta_info["processing_enqueued_at"] = datetime.now(timezone.utc).isoformat()
    meta_info["processing_current_step"] = "Processing queued."
    doc.meta_info = meta_info
    doc.status = "indexing"
    doc.error_message = None


def _has_document_processing_artifacts(db: Session, doc: Document) -> bool:
    meta_info = dict(doc.meta_info or {})
    progress = meta_info.get("progress")
    if isinstance(progress, (int, float)) and progress > 0:
        return True

    if _has_document_chunks(db, doc):
        return True

    return _has_document_version_with_status(
        db,
        doc,
        ("indexing", "staging", "ready"),
    )


def _has_document_completion_artifacts(db: Session, doc: Document) -> bool:
    if _has_document_chunks(db, doc):
        return True
    return _has_document_version_with_status(db, doc, ("ready",))


def _has_document_chunks(db: Session, doc: Document) -> bool:
    if (
        db.query(DocumentChunk.id)
        .filter(DocumentChunk.document_id == doc.id)
        .first()
        is not None
    ):
        return True
    return False


def _has_document_version_with_status(
    db: Session,
    doc: Document,
    statuses: tuple[str, ...],
) -> bool:
    return (
        db.query(DocumentVersion.id)
        .filter(
            DocumentVersion.legacy_document_id == doc.id,
            DocumentVersion.status.in_(statuses),
        )
        .first()
        is not None
    )


def finalize_stale_processing_start(
    db: Session,
    document_id: UUID,
    *,
    timeout_seconds: int = PROCESSING_START_TIMEOUT_SECONDS,
    active_timeout_seconds: int = ACTIVE_PROCESSING_STALL_TIMEOUT_SECONDS,
    now: Optional[datetime] = None,
) -> bool:
    doc = db.query(Document).get(document_id)
    if not doc or doc.status not in {"indexing", "processing"}:
        return False

    latest_job = (
        db.query(KnowledgeDocumentIngestionJob)
        .filter(KnowledgeDocumentIngestionJob.document_id == document_id)
        .order_by(
            KnowledgeDocumentIngestionJob.requested_at.desc(),
            KnowledgeDocumentIngestionJob.id.desc(),
        )
        .first()
    )
    if latest_job is not None:
        if latest_job.status in {"pending", "running", "retry_scheduled"}:
            return False
        if latest_job.status == "succeeded":
            if _has_document_completion_artifacts(db, doc):
                meta_info = dict(doc.meta_info or {})
                meta_info.pop(ACTIVE_FENCING_TOKEN_HASH_KEY, None)
                meta_info["progress"] = 100
                meta_info["processing_current_step"] = "Processing completed."
                doc.meta_info = meta_info
                doc.status = "completed"
                doc.error_message = None
                doc.updated_at = _aware_datetime(now) or datetime.now(timezone.utc)
                db.commit()
                return True
            return False
        if latest_job.status in {"dead_lettered", "cancelled"}:
            meta_info = dict(doc.meta_info or {})
            meta_info.pop(ACTIVE_FENCING_TOKEN_HASH_KEY, None)
            meta_info["progress"] = 0
            meta_info["processing_current_step"] = (
                "Processing failed."
                if latest_job.status == "dead_lettered"
                else "Processing cancelled."
            )
            doc.meta_info = meta_info
            doc.status = "failed"
            doc.error_message = (
                "Document processing failed."
                if latest_job.status == "dead_lettered"
                else "Document processing was cancelled."
            )
            doc.updated_at = _aware_datetime(now) or datetime.now(timezone.utc)
            db.commit()
            return True

    meta_info = dict(doc.meta_info or {})
    checked_at = _aware_datetime(now) or datetime.now(timezone.utc)
    queued_at = (
        _aware_datetime(meta_info.get("processing_enqueued_at"))
        or _aware_datetime(getattr(doc, "updated_at", None))
        or _aware_datetime(getattr(doc, "created_at", None))
    )
    if queued_at is None:
        return False

    active_fencing_token = meta_info.get(ACTIVE_FENCING_TOKEN_HASH_KEY)
    if active_fencing_token:
        started_at = (
            _aware_datetime(meta_info.get("processing_started_at"))
            or _aware_datetime(getattr(doc, "updated_at", None))
            or queued_at
        )
        if (checked_at - started_at).total_seconds() < active_timeout_seconds:
            return False
        progress_updated_at = _aware_datetime(
            meta_info.get("processing_progress_updated_at")
        )
        if (
            progress_updated_at
            and (checked_at - progress_updated_at).total_seconds()
            < active_timeout_seconds
        ):
            return False
        if _has_document_completion_artifacts(db, doc):
            return False

        meta_info.pop(ACTIVE_FENCING_TOKEN_HASH_KEY, None)
        meta_info["progress"] = 0
        meta_info["processing_current_step"] = (
            "Processing did not complete in time."
        )
        doc.meta_info = meta_info
        doc.status = "failed"
        doc.error_message = ACTIVE_PROCESSING_STALL_TIMEOUT_MESSAGE
        doc.updated_at = checked_at
        db.commit()
        return True

    if (checked_at - queued_at).total_seconds() < timeout_seconds:
        return False
    if _has_document_processing_artifacts(db, doc):
        return False

    meta_info.pop(ACTIVE_FENCING_TOKEN_HASH_KEY, None)
    meta_info["progress"] = 0
    meta_info["processing_current_step"] = "Processing did not start in time."
    doc.meta_info = meta_info
    doc.status = "failed"
    doc.error_message = PROCESSING_START_TIMEOUT_MESSAGE
    doc.updated_at = checked_at
    db.commit()
    return True


def recover_timed_out_document_with_artifacts(
    db: Session,
    document_id: UUID,
    *,
    now: Optional[datetime] = None,
) -> bool:
    doc = db.query(Document).get(document_id)
    if (
        not doc
        or doc.status != "failed"
        or doc.error_message
        not in {
            PROCESSING_START_TIMEOUT_MESSAGE,
            ACTIVE_PROCESSING_STALL_TIMEOUT_MESSAGE,
        }
    ):
        return False

    if not (
        _has_document_chunks(db, doc)
        or _has_document_version_with_status(db, doc, ("ready",))
    ):
        return False

    checked_at = _aware_datetime(now) or datetime.now(timezone.utc)
    meta_info = dict(doc.meta_info or {})
    meta_info.pop(ACTIVE_FENCING_TOKEN_HASH_KEY, None)
    meta_info["progress"] = 100
    meta_info["processing_current_step"] = "Processing completed."
    meta_info["processing_recovered_from_timeout"] = True
    doc.meta_info = meta_info
    doc.status = "completed"
    doc.error_message = None
    doc.updated_at = checked_at
    db.commit()
    return True


@dataclass(frozen=True)
class DocumentChunkingResult:
    chunks: List[Dict[str, Any]]
    chunking_mode: str
    chunking_fingerprint: str
    content_hash: str


class DurableIngestionDocumentMissing(RuntimeError):
    pass


class DurableIngestionLockBusy(RuntimeError):
    pass


class DurableIngestionNoContent(RuntimeError):
    pass


class DurableIngestionLeaseLost(RuntimeError):
    pass


class DurableIngestionSourceFailure(RuntimeError):
    """Carry one allowlisted source failure across the durable worker boundary."""

    _SAFE_REASON_CODES = frozenset(
        {
            "configuration.invalid",
            "processing.failed",
            "resource.hidden",
            "source.temporarily_unavailable",
            RAW_PARSER_EGRESS_UNAVAILABLE_REASON,
        }
    )

    def __init__(self, reason_code: object) -> None:
        normalized = str(reason_code)
        self.reason_code = (
            normalized
            if normalized in self._SAFE_REASON_CODES
            else "processing.failed"
        )
        super().__init__("Source processing failed.")


class IngestionOrchestrator:
    def __init__(
        self,
        db: Session,
        user_id: Optional[UUID] = None,
        organization_id: Optional[UUID] = None,
        chunk_size=1000,
        chunk_overlap=200,
        ai_model="text-embedding-3-small",
    ):
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id
        self.ai_model = ai_model
        self._durable_job_mode = False
        self._active_progress_fencing_token: str | None = None
        self._active_progress_lease_guard: Callable[[], bool] | None = None

        self.text_splitter = _recursive_character_text_splitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ".", " ", ""],
            keep_separator=True,
        )

    def save_temp_file(self, file: UploadFile) -> str:
        """
        StorageService를 사용하여 파일을 저장하고 경로를 반환합니다.
        (Local: 파일 경로, S3: s3://...)
        """
        storage = get_storage_service()
        return storage.upload(file)

    def _filter_chunks(
        self,
        chunks: List[Dict[str, Any]],
        selection_mode: str,
        chunk_range: Optional[str],
        keyword_filter: Optional[str],
    ) -> List[Dict[str, Any]]:
        """
        주어진 조건에 따라 청크 리스트를 필터링합니다.
        """
        return filter_chunks_by_selection(
            chunks,
            selection_mode=selection_mode,
            chunk_range=chunk_range,
            keyword_filter=keyword_filter,
        )

    def process_document(self, document_id: UUID):
        # 락 획득 시도 (2분 TTL)
        with self._document_processing_lock(document_id) as acquired:
            if not acquired:
                logger.error(
                    f"[IngestionOrchestrator] Document {document_id} is already being processed by another worker"
                )
                self._handle_lock_not_acquired(document_id)
                return

            session = SessionLocal()
            self.db = session
            document_version: DocumentVersion | None = None
            ingestion_fencing_token = str(uuid4())
            failed_version_context: dict[str, Any] | None = None
            finalization_committed = False

            try:
                doc = self.db.query(Document).get(document_id)
                if not doc:
                    logger.warning(f"Document {document_id} not found")
                    return

                # 초기 상태 저장 (업데이트 전)
                initial_status = doc.status

                # 이미 처리 완료된 문서 건너뛰기
                if doc.status == "completed":
                    return

                self._mark_document_indexing(document_id, ingestion_fencing_token)
                acquire_document_write_lock(self.db, document_id)

                raw_blocks = self._extract_raw_blocks(doc)
                if not raw_blocks:
                    logger.warning(
                        f"[IngestionOrchestrator] Document {document_id}의 raw_blocks가 비어있음."
                    )
                    self._update_status(
                        document_id,
                        "completed",
                        error_message="추출 가능한 콘텐츠가 없습니다.",
                        meta_updates={ACTIVE_FENCING_TOKEN_HASH_KEY: None},
                    )
                    return

                chunking_result = self._build_document_chunks(doc, raw_blocks)
                if self._has_current_artifacts(
                    doc,
                    chunking_result=chunking_result,
                    initial_status=initial_status,
                ):
                    self._update_status(
                        document_id,
                        "completed",
                        meta_updates={ACTIVE_FENCING_TOKEN_HASH_KEY: None},
                    )
                    return

                document_version, failed_version_context = (
                    self._create_indexing_version(
                        doc,
                        chunking_result=chunking_result,
                        fencing_token=ingestion_fencing_token,
                    )
                )
                if document_version is None:
                    # organization_id가 없는 legacy KB는 기존 처리 상태 commit 경로를 유지한다.
                    doc.content_hash = chunking_result.content_hash

                self._persist_chunks_with_embeddings(
                    doc,
                    chunking_result.chunks,
                    chunking_mode=chunking_result.chunking_mode,
                    chunking_fingerprint=chunking_result.chunking_fingerprint,
                    document_version=document_version,
                )
                if document_version is not None:
                    self._finalize_indexing_version(
                        doc,
                        document_version,
                        fencing_token=ingestion_fencing_token,
                    )
                    finalization_committed = True
                else:
                    self._update_status(document_id, "completed")
                self._update_progress_redis(
                    document_id, 100, expire=True
                )  # 완료 시 키 만료 또는 100 유지 후 만료
                logger.info(
                    f"[DEBUG] process_document 완료 - document_id: {document_id}"
                )

            except Exception as e:
                self._handle_processing_failure(
                    document_id,
                    e,
                    failed_version_context=failed_version_context,
                    finalization_committed=finalization_committed,
                    fencing_token=ingestion_fencing_token,
                )

            finally:
                if session:
                    session.close()

    def process_document_for_job(
        self,
        document_id: UUID,
        *,
        session: Session,
        fencing_token: str,
        finalize_job: Callable[[UUID | None, datetime], bool],
        lease_is_current: Callable[[], bool],
    ) -> UUID | None:
        """Execute ingestion under a durable job and one finalization transaction."""

        with self._document_processing_lock(document_id) as acquired:
            if not acquired:
                raise DurableIngestionLockBusy()

            previous_db = self.db
            self.db = session
            self._durable_job_mode = True
            self._active_progress_fencing_token = fencing_token
            self._active_progress_lease_guard = lease_is_current
            try:
                doc = self._lock_durable_document_scope(document_id)

                initial_status = doc.status
                self._mark_document_indexing(
                    document_id,
                    fencing_token,
                    commit=False,
                )
                acquire_document_write_lock(self.db, document_id)

                raw_blocks = self._extract_raw_blocks(doc)
                if not raw_blocks:
                    raise DurableIngestionNoContent()

                chunking_result = self._build_document_chunks(doc, raw_blocks)
                if self._has_current_artifacts(
                    doc,
                    chunking_result=chunking_result,
                    initial_status=initial_status,
                ):
                    self._update_status(
                        document_id,
                        "completed",
                        progress=100,
                        meta_updates={
                            ACTIVE_FENCING_TOKEN_HASH_KEY: None,
                            "processing_progress": 100,
                            "processing_progress_updated_at": datetime.now(
                                timezone.utc
                            ).isoformat(),
                            "processing_current_step": "Processing completed.",
                        },
                        commit=False,
                    )
                    result_version_id = doc.knowledge_base.active_document_version_id
                    completed_at = datetime.now(timezone.utc)
                    if not finalize_job(result_version_id, completed_at):
                        raise DurableIngestionLeaseLost()
                    self.db.commit()
                    self._update_progress_redis(
                        document_id,
                        100,
                        expire=True,
                        persist_metadata=False,
                    )
                    return result_version_id

                document_version, _ = self._create_indexing_version(
                    doc,
                    chunking_result=chunking_result,
                    fencing_token=fencing_token,
                )
                if document_version is None:
                    raise RuntimeError("organization-scoped ingestion version is required")

                self._persist_chunks_with_embeddings(
                    doc,
                    chunking_result.chunks,
                    chunking_mode=chunking_result.chunking_mode,
                    chunking_fingerprint=chunking_result.chunking_fingerprint,
                    document_version=document_version,
                )
                self._finalize_indexing_version(
                    doc,
                    document_version,
                    fencing_token=fencing_token,
                    commit=False,
                )
                self._update_status(
                    document_id,
                    "completed",
                    progress=100,
                    meta_updates={
                        "processing_progress": 100,
                        "processing_progress_updated_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        "processing_current_step": "Processing completed.",
                    },
                    commit=False,
                )
                completed_at = datetime.now(timezone.utc)
                if not finalize_job(document_version.id, completed_at):
                    raise DurableIngestionLeaseLost()
                self.db.commit()
                self._update_progress_redis(
                    document_id,
                    100,
                    expire=True,
                    persist_metadata=False,
                )
                return document_version.id
            except Exception:
                self.db.rollback()
                raise
            finally:
                self._active_progress_fencing_token = None
                self._active_progress_lease_guard = None
                self._durable_job_mode = False
                self.db = previous_db

    def _lock_durable_document_scope(self, document_id: UUID) -> Document:
        knowledge_base_id = (
            self.db.query(Document.knowledge_base_id)
            .filter(Document.id == document_id)
            .scalar()
        )
        if knowledge_base_id is None:
            raise DurableIngestionDocumentMissing()

        knowledge_base = (
            self.db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.organization_id == self.organization_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if knowledge_base is None:
            raise DurableIngestionDocumentMissing()

        document = (
            self.db.query(Document)
            .filter(
                Document.id == document_id,
                Document.knowledge_base_id == knowledge_base.id,
            )
            .with_for_update()
            .one_or_none()
        )
        if document is None:
            raise DurableIngestionDocumentMissing()
        return document

    @contextmanager
    def _document_processing_lock(self, document_id: UUID):
        lock_context = None
        try:
            lock = DistributedLock(f"doc_processing:{document_id}", ttl=120)
            lock_context = lock.lock()
            acquired = lock_context.__enter__()
        except Exception as exc:
            logger.warning(
                "Document processing distributed lock unavailable; continuing without Redis lock - ID: %s, error_type: %s",
                document_id,
                type(exc).__name__,
            )
            yield True
            return

        try:
            yield acquired
        finally:
            if lock_context is not None:
                lock_context.__exit__(None, None, None)

    def _handle_lock_not_acquired(self, document_id: UUID) -> None:
        time.sleep(1)
        session = SessionLocal()
        try:
            if finalize_stale_processing_start(session, document_id):
                self._update_progress_redis(document_id, 0, expire=True)
        except Exception as exc:
            session.rollback()
            logger.error(
                "Failed to finalize document after lock acquisition failure: "
                "document_id=%s error_type=%s",
                document_id,
                type(exc).__name__,
            )
        finally:
            session.close()

    def _mark_document_indexing(
        self,
        document_id: UUID,
        ingestion_fencing_token: str,
        *,
        commit: bool = True,
    ) -> None:
        processing_started_at = datetime.now(timezone.utc).isoformat()
        self._update_status(
            document_id,
            "indexing",
            meta_updates={
                **KnowledgeIngestionFencing.active_document_meta_update(
                    ingestion_fencing_token
                ),
                "processing_started_at": processing_started_at,
                "processing_current_step": "Processing started.",
            },
            commit=commit,
        )
        self._update_progress_redis(document_id, 0)

    def _extract_raw_blocks(self, doc: Document) -> List[Dict[str, Any]]:
        processor = IngestionFactory.get_processor(
            doc.source_type,
            self.db,
            self.user_id,
            self.organization_id,
        )
        result = processor.process(self._build_config(doc))
        if result.metadata.get("error"):
            raise DurableIngestionSourceFailure(result.metadata.get("reason_code"))
        return result.chunks

    def _build_document_chunks(
        self, doc: Document, raw_blocks: List[Dict[str, Any]]
    ) -> DocumentChunkingResult:
        meta = dict(doc.meta_info or {})
        chunking_mode = validate_chunking_request(
            chunking_mode=meta.get("chunking_mode"),
            source_type=doc.source_type,
            selection_mode=meta.get("selection_mode", "all"),
        )
        chunking_fingerprint = chunking_fingerprint_hash(
            meta_info=meta,
            chunk_size=doc.chunk_size,
            chunk_overlap=doc.chunk_overlap,
            source_type=doc.source_type,
        )
        content_hash = hashlib.sha256(
            "".join([b["content"] for b in raw_blocks]).encode("utf-8")
        ).hexdigest()

        if doc.source_type == "DB":
            final_chunks = self._refine_chunks(raw_blocks, override_chunk_size=8000)
            filtered_chunks = self._filter_chunks(
                final_chunks,
                meta.get("selection_mode", "all"),
                meta.get("chunk_range"),
                meta.get("keyword_filter"),
            )
        elif chunking_mode == CHUNKING_MODE_HIERARCHICAL:
            filtered_chunks = self._build_hierarchical_chunks(doc, raw_blocks, meta)
        else:
            filtered_chunks = self._build_flat_chunks(doc, raw_blocks, meta)

        return DocumentChunkingResult(
            chunks=filtered_chunks,
            chunking_mode=chunking_mode,
            chunking_fingerprint=chunking_fingerprint,
            content_hash=content_hash,
        )

    def _build_hierarchical_chunks(
        self, doc: Document, raw_blocks: List[Dict[str, Any]], meta: dict
    ) -> List[Dict[str, Any]]:
        preprocessed_blocks = self._preprocess_blocks(raw_blocks, meta)
        parent_size = parent_target_size(doc.chunk_size)
        chunker = HierarchicalChunker(
            HierarchicalChunkerConfig(
                child_chunk_size=doc.chunk_size,
                child_chunk_overlap=doc.chunk_overlap,
                segment_identifier=meta.get("segment_identifier", "\\n\\n"),
                parent_target_size=parent_size,
                parent_chunk_overlap=parent_overlap_size(
                    doc.chunk_overlap,
                    parent_size,
                ),
            )
        )
        final_chunks = chunker.build(preprocessed_blocks)
        return filter_hierarchical_chunks(
            final_chunks,
            selection_mode=meta.get("selection_mode", "all"),
            keyword_filter=meta.get("keyword_filter"),
        )

    def _build_flat_chunks(
        self, doc: Document, raw_blocks: List[Dict[str, Any]], meta: dict
    ) -> List[Dict[str, Any]]:
        full_text = "\n".join([b["content"] for b in raw_blocks])
        preprocessed_text = self.preprocess_text(full_text, meta)
        preprocessed_blocks = [{"content": preprocessed_text, "metadata": {}}]

        chunk_size = doc.chunk_size
        chunk_overlap = doc.chunk_overlap
        segment_identifier = meta.get("segment_identifier", "\\n\\n")

        separators = ["\n\n", "\n", ".", " ", ""]
        if segment_identifier:
            identifier = segment_identifier.replace("\\n", "\n")
            if identifier not in separators:
                separators.insert(0, identifier)

        doc_specific_splitter = _recursive_character_text_splitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators,
            keep_separator=True,
        )

        final_chunks = self._refine_chunks(
            preprocessed_blocks, splitter=doc_specific_splitter
        )
        return self._filter_chunks(
            final_chunks,
            meta.get("selection_mode", "all"),
            meta.get("chunk_range"),
            meta.get("keyword_filter"),
        )

    def _has_current_artifacts(
        self,
        doc: Document,
        *,
        chunking_result: DocumentChunkingResult,
        initial_status: str,
    ) -> bool:
        meta = dict(doc.meta_info or {})
        knowledge_base = getattr(doc, "knowledge_base", None)
        active_version = (
            getattr(knowledge_base, "active_document_version", None)
            if knowledge_base is not None
            else None
        )
        has_document_owned_active_version = (
            active_version is not None
            and getattr(knowledge_base, "active_document_version_id", None)
            == active_version.id
            and active_version.status == "ready"
            and active_version.legacy_document_id == doc.id
        )
        # 실패했던 문서는 같은 content라도 재처리한다. 임베딩 모델이나 chunking 설정이 바뀐 경우도 재처리 대상이다.
        return (
            has_document_owned_active_version
            and doc.content_hash == chunking_result.content_hash
            and doc.embedding_model == self.ai_model
            and meta.get("chunking_fingerprint_hash")
            == chunking_result.chunking_fingerprint
            and initial_status != "failed"
        )

    def _create_indexing_version(
        self,
        doc: Document,
        *,
        chunking_result: DocumentChunkingResult,
        fencing_token: str,
    ) -> tuple[DocumentVersion | None, dict[str, Any] | None]:
        finalizer = KnowledgeIngestionFinalizer(self.db)
        document_version = finalizer.create_indexing_version(
            doc,
            content_hash=chunking_result.content_hash,
            chunking_fingerprint=chunking_result.chunking_fingerprint,
            embedding_model=self.ai_model,
            safe_metadata={
                "source_type": str(getattr(doc.source_type, "value", doc.source_type)),
                "chunking_mode": chunking_result.chunking_mode,
            },
            fencing_token=fencing_token,
        )
        if document_version is None:
            return None, None
        return document_version, {
            "organization_id": document_version.organization_id,
            "knowledge_base_id": document_version.knowledge_base_id,
            "legacy_document_id": document_version.legacy_document_id,
            "source_identity_id": document_version.source_identity_id,
            "content_hash": document_version.content_hash,
            "chunking_fingerprint": document_version.chunking_fingerprint,
            "embedding_model": document_version.embedding_model,
            "safe_metadata": dict(document_version.safe_metadata or {}),
        }

    def _finalize_indexing_version(
        self,
        doc: Document,
        document_version: DocumentVersion,
        *,
        fencing_token: str,
        commit: bool = True,
    ) -> None:
        KnowledgeIngestionFinalizer(self.db).finalize_active_version(
            document_version,
            expected_fencing_token=fencing_token,
        )
        doc.status = "completed"
        doc.error_message = None
        doc.updated_at = datetime.now(timezone.utc)
        if commit:
            self.db.commit()
        else:
            self.db.flush()

    def _handle_processing_failure(
        self,
        document_id: UUID,
        error: Exception,
        *,
        failed_version_context: dict[str, Any] | None,
        finalization_committed: bool,
        fencing_token: str,
    ) -> None:
        logger.error(
            "Document processing failed - ID: %s, error_type: %s",
            document_id,
            type(error).__name__,
        )
        self.db.rollback()
        if finalization_committed:
            logger.warning(
                "Document processing post-finalization failure ignored - ID: %s, error_type: %s",
                document_id,
                type(error).__name__,
            )
            return
        if failed_version_context is not None:
            self._record_failed_indexing_version(
                failed_version_context,
                error=error,
                fencing_token=fencing_token,
            )
        self._update_status(
            document_id,
            "failed",
            self._safe_ingestion_error_message(error),
            progress=0,
            meta_updates={ACTIVE_FENCING_TOKEN_HASH_KEY: None},
        )
        self._update_progress_redis(document_id, 0, expire=True)

    def _record_failed_indexing_version(
        self,
        failed_version_context: dict[str, Any],
        *,
        error: Exception,
        fencing_token: str,
    ) -> None:
        try:
            KnowledgeIngestionFinalizer(self.db).record_failed_indexing_version(
                **failed_version_context,
                safe_reason_code=(
                    "ingestion.finalization_failed"
                    if isinstance(error, KnowledgeIngestionFinalizationError)
                    else (
                        error.reason_code
                        if isinstance(error, DurableIngestionSourceFailure)
                        and error.reason_code
                        == RAW_PARSER_EGRESS_UNAVAILABLE_REASON
                        else "ingestion.processing_failed"
                    )
                ),
                fencing_token=fencing_token,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()

    def _update_progress_redis(
        self,
        document_id: UUID,
        progress: int,
        expire: bool = False,
        *,
        persist_metadata: bool = True,
    ):
        """
        진행률을 Redis에 저장 (DB 과부하 방지)
        이전 값보다 작으면 업데이트하지 않음
        expire=True: 완료/실패 시 100을 저장하고 짧은 TTL로 자동 만료
        """
        from apps.shared.pubsub import get_redis_client

        # Durable attempts must pass the DB fencing check before publishing an
        # advisory Redis progress value. Otherwise a stale worker could make
        # the UI observe progress that its DB transaction can no longer own.
        if persist_metadata:
            if (
                self._durable_job_mode
                and self._active_progress_lease_guard is not None
                and not self._active_progress_lease_guard()
            ):
                raise DurableIngestionLeaseLost()
            self._update_progress_metadata(document_id, progress)
        try:
            redis_client = get_redis_client()
            key = f"knowledge_progress:{document_id}"

            if expire:
                # 완료/실패 시 100을 저장하고 30초 후 자동 삭제
                redis_client.set(key, str(progress), ex=30)
                return

            current_val = redis_client.get(key)
            if current_val:
                try:
                    current_progress = int(current_val)
                    if progress < current_progress:
                        return
                except ValueError:
                    pass

            # 진행률 저장
            redis_client.set(key, str(progress), ex=600)
        except Exception as exc:
            logger.warning(
                "Failed to update progress in Redis: error_type=%s",
                type(exc).__name__,
            )

    def _update_progress_metadata(self, document_id: UUID, progress: int) -> None:
        if self._durable_job_mode:
            self._update_progress_metadata_in_active_session(document_id, progress)
            return
        session = SessionLocal()
        try:
            doc = session.query(Document).get(document_id)
            if not doc or doc.status not in {"indexing", "processing"}:
                return
            meta_info = dict(doc.meta_info or {})
            current_progress = meta_info.get("progress")
            if (
                isinstance(current_progress, (int, float))
                and progress < current_progress
            ):
                return
            meta_info["progress"] = progress
            meta_info["processing_progress"] = progress
            meta_info["processing_progress_updated_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            doc.meta_info = meta_info
            session.commit()
        except Exception:
            session.rollback()
            logger.warning(
                "Failed to update document processing progress metadata: %s",
                document_id,
            )
        finally:
            session.close()

    def _update_progress_metadata_in_active_session(
        self, document_id: UUID, progress: int
    ) -> None:
        doc = self.db.query(Document).get(document_id)
        if not doc or doc.status not in {"indexing", "processing"}:
            return
        fencing_token = self._active_progress_fencing_token
        if fencing_token:
            expected_hash = KnowledgeIngestionFencing.hash_token(fencing_token)
            if KnowledgeIngestionFencing.active_document_hash(doc) != expected_hash:
                raise DurableIngestionLeaseLost()
        meta_info = dict(doc.meta_info or {})
        current_progress = meta_info.get("progress")
        if isinstance(current_progress, (int, float)) and progress < current_progress:
            return
        meta_info["progress"] = progress
        meta_info["processing_progress"] = progress
        meta_info["processing_progress_updated_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        doc.meta_info = meta_info
        self.db.flush()

    def resume_processing(self, document_id: UUID, strategy: str):
        """
        사용자 승인 후 파싱 재개
        """
        session = SessionLocal()
        try:
            doc = session.query(Document).get(document_id)
            if not doc:
                return

            new_meta = dict(doc.meta_info or {})
            new_meta["strategy"] = strategy
            doc.meta_info = new_meta
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        self.process_document(document_id)

    def _preprocess_blocks(
        self, raw_blocks: List[Dict[str, Any]], meta_info: dict
    ) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        options = {
            "remove_urls_emails": bool(meta_info.get("remove_urls_emails", False)),
            "normalize_whitespace": bool(meta_info.get("remove_whitespace", True)),
        }
        for index, block in enumerate(raw_blocks, start=1):
            content = self.preprocess_text(str(block.get("content") or ""), options)
            if not content.strip():
                continue
            metadata = dict(block.get("metadata") or {})
            metadata.setdefault("section_path", [f"Section {index}"])
            blocks.append({"content": content, "metadata": metadata})
        return blocks

    async def analyze_document(self, document_id: UUID) -> Dict[str, Any]:
        """
        문서 분석 (비용 예측)
        """
        doc = self.db.query(Document).get(document_id)
        if not doc:
            raise ValueError("Document not found")

        processor = IngestionFactory.get_processor(
            doc.source_type,
            self.db,
            self.user_id,
            self.organization_id,
        )
        source_config = self._build_config(doc)

        analysis_result = processor.analyze(source_config)

        pages = analysis_result.get("pages", 0)

        # Pricing Policy (LlamaCloud Standard Mode - fast_mode=False):
        # source: https://cloud.llamaindex.ai/pricing
        # Rate: $0.003 per page for Standard Parsing (OCR enabled)
        # Free Tier: First 1000 pages/day are free, but we calculate full potential cost here.

        target_price_per_page = 0.003
        credits_est = pages * 1  # 1 page = 1 credit unit
        cost_usd_est = pages * target_price_per_page

        cost_estimate = {
            "pages": pages,
            "credits": credits_est,
            "cost_usd": cost_usd_est,
        }

        return {
            "cost_estimate": cost_estimate,
            "filename": doc.filename,
            "is_cached": False,
        }

    def preview_chunking(
        self,
        file_path: str,
        chunk_size: int,
        chunk_overlap: int,
        segment_identifier: str,
        remove_urls_emails: bool = False,
        remove_whitespace: bool = True,
        strategy: str = "general",
        source_type: SourceType = SourceType.FILE,
        chunking_mode: str = "flat",
        meta_info: dict = None,
        db_config: dict = None,
        # 필터링 파라미터 추가
        selection_mode: str = "all",
        chunk_range: Optional[str] = None,
        keyword_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        미리보기 (DB 저장 없음)
        """
        chunking_mode = validate_chunking_request(
            chunking_mode=chunking_mode,
            source_type=source_type,
            selection_mode=selection_mode,
        )

        processor = IngestionFactory.get_processor(
            source_type,
            self.db,
            self.user_id,
            self.organization_id,
        )

        source_config = {}
        if source_type == SourceType.FILE:
            source_config = {"file_path": file_path, "strategy": strategy}
            # LlamaParse 미리보기 속도 개선을 위해 일부 페이지만 파싱
            if strategy == "llamaparse":
                target_pages = "0-4"  # Default (1-5p)

                # 사용자가 청크 범위를 지정한 경우, 해당 범위가 포함된 페이지를 파싱하도록 조정
                # 예: chunk_range="6-10" -> 대략 1페이지당 3~5개 청크 가정 -> 2~3페이지부터 시작
                # 정확한 매핑은 불가능하므로, "시작 청크 번호"를 기준으로 페이지를 추정
                if selection_mode == "range" and chunk_range:
                    try:
                        # "5-35", "5", "5, 10" 등 다양한 형식에서 첫 번째 숫자 추출
                        first_chunk_idx = int(re.split(r"[,-]", chunk_range)[0].strip())

                        # 대략적인 페이지 추정 (1페이지 = 1000자, 청크=500자 가정 시 페이지당 2~3개 청크)
                        # 보수적으로 1페이지당 2개 청크로 계산하여 페이지를 추정
                        est_page_start = max(0, (first_chunk_idx - 1) // 2)
                        est_page_end = est_page_start + 4
                        target_pages = f"{est_page_start}-{est_page_end}"

                    except Exception as e:
                        logger.error(
                            f"Failed to parse chunk_range for page targeting: {e}"
                        )

                source_config["target_pages"] = target_pages
        elif source_type == SourceType.API:
            api_config = meta_info.get("api_config", {})
            source_config = self._build_api_source_config(api_config)
        elif source_type == SourceType.DB:
            base_config = meta_info or {}
            # db_config가 JSON string일 수 있으므로 파싱
            if isinstance(db_config, str):
                try:
                    db_config = json.loads(db_config)
                except (TypeError, json.JSONDecodeError):
                    db_config = {}
            source_config = {**base_config, **(db_config or {})}

        result = processor.process(source_config)

        # Check for errors from processor
        if result.metadata and "error" in result.metadata:
            raise IngestionPreviewSourceError(
                result.metadata.get("reason_code")
            )

        raw_blocks = result.chunks
        meta_info = dict(meta_info or {})

        # DB인 경우 이미 Row 단위로 구조화되어 있으므로, Merge & Re-split 하지 않음
        if source_type == SourceType.DB:
            # 8000자 초과 시에만 분할
            final_splits = self._refine_chunks(raw_blocks, override_chunk_size=8000)

            # Tiktoken 인코더 준비
            try:
                encoding = tiktoken.encoding_for_model(self.ai_model)
            except Exception:
                encoding = tiktoken.get_encoding("cl100k_base")

            preview = []
            for block in final_splits:
                content = block["content"]
                preview.append(
                    {
                        "content": content,
                        "token_count": len(encoding.encode(content)),
                        "char_count": len(content),
                    }
                )

            # 필터링 적용 후 반환
            return self._filter_chunks(
                preview, selection_mode, chunk_range, keyword_filter
            )

        if chunking_mode == CHUNKING_MODE_HIERARCHICAL:
            preprocessed_blocks = self._preprocess_blocks(
                raw_blocks,
                {
                    **meta_info,
                    "remove_urls_emails": remove_urls_emails,
                    "remove_whitespace": remove_whitespace,
                },
            )
            parent_size = parent_target_size(chunk_size)
            chunker = HierarchicalChunker(
                HierarchicalChunkerConfig(
                    child_chunk_size=chunk_size,
                    child_chunk_overlap=chunk_overlap,
                    segment_identifier=segment_identifier,
                    parent_target_size=parent_size,
                    parent_chunk_overlap=parent_overlap_size(
                        chunk_overlap,
                        parent_size,
                    ),
                )
            )
            hierarchy_chunks = filter_hierarchical_chunks(
                chunker.build(preprocessed_blocks),
                selection_mode=selection_mode,
                keyword_filter=keyword_filter,
            )
            try:
                encoding = tiktoken.encoding_for_model(self.ai_model)
            except Exception:
                encoding = tiktoken.get_encoding("cl100k_base")
            return [
                {
                    "content": chunk["content"],
                    "token_count": len(encoding.encode(chunk["content"])),
                    "char_count": len(chunk["content"]),
                }
                for chunk in hierarchy_chunks
                if chunk.get("chunk_level") == "child"
            ]

        # 2. 전처리
        full_text = "\n".join([b["content"] for b in raw_blocks])

        options = {
            "remove_urls_emails": remove_urls_emails,
            "normalize_whitespace": remove_whitespace,  # 파라미터명 호환
        }
        full_text = self.preprocess_text(full_text, options)

        # 3. 청킹
        separators = ["\n\n", "\n", ".", " ", ""]
        if segment_identifier:
            identifier = segment_identifier.replace("\\n", "\n")
            if identifier not in separators:
                separators.insert(0, identifier)

        splitter = _recursive_character_text_splitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators,
            keep_separator=True,
        )
        splits = splitter.split_text(full_text)

        # 4. 미리보기 형식 반환
        try:
            encoding = tiktoken.encoding_for_model(self.ai_model)
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")

        preview = []
        for s in splits:
            preview.append(
                {
                    "content": s,
                    "token_count": len(encoding.encode(s)),
                    "char_count": len(s),
                }
            )

        # 필터링 적용
        return self._filter_chunks(preview, selection_mode, chunk_range, keyword_filter)

    def _build_config(self, doc: Document) -> Dict[str, Any]:
        config = {"document_id": str(doc.id)}
        if doc.source_type == SourceType.FILE:
            config["file_path"] = doc.file_path
            if doc.meta_info and "strategy" in doc.meta_info:
                config["strategy"] = doc.meta_info["strategy"]
        elif doc.source_type == SourceType.API:
            api_config = (doc.meta_info or {}).get("api_config", {})
            config.update(self._build_api_source_config(api_config))
        elif doc.source_type == SourceType.DB:
            config.update(doc.meta_info or {})
            # Flatten db_config if it exists (DB Processor expects selections at root)
            if "db_config" in config and isinstance(config["db_config"], dict):
                config.update(config["db_config"])
        return config

    def _build_api_source_config(self, api_config: Any) -> Dict[str, Any]:
        if isinstance(api_config, str):
            try:
                api_config = json.loads(api_config)
            except (TypeError, json.JSONDecodeError):
                api_config = {}
        api_config = dict(api_config or {})
        return {
            "url": self._decrypt_api_source_value(
                api_config,
                encrypted_key="url_encrypted",
                legacy_key="url",
            ),
            "method": str(api_config.get("method") or "GET").upper(),
            "headers": self._decrypt_api_source_value(
                api_config,
                encrypted_key="headers_encrypted",
                legacy_key="headers",
                default={},
            ),
            "body": self._decrypt_api_source_value(
                api_config,
                encrypted_key="body_encrypted",
                legacy_key="body",
            ),
        }

    def _decrypt_api_source_value(
        self,
        api_config: Dict[str, Any],
        *,
        encrypted_key: str,
        legacy_key: str,
        default: Any = None,
    ) -> Any:
        from apps.shared.utils.encryption import encryption_manager

        encrypted_value = api_config.get(encrypted_key)
        if encrypted_value:
            return encryption_manager.decrypt(str(encrypted_value))

        legacy_value = api_config.get(legacy_key, default)
        if not isinstance(legacy_value, str):
            return legacy_value
        try:
            return encryption_manager.decrypt(legacy_value)
        except Exception:
            return legacy_value

    def _refine_chunks(
        self,
        raw_blocks: List[Dict[str, Any]],
        override_chunk_size: int = None,
        splitter: Any = None,
    ) -> List[Dict[str, Any]]:
        refined = []

        target_splitter = splitter if splitter else self.text_splitter

        # 청크 사이즈 오버라이드 (DB 대형 Row 처리 등)
        if override_chunk_size is not None:
            target_splitter = _recursive_character_text_splitter(
                chunk_size=override_chunk_size,
                chunk_overlap=self.text_splitter._chunk_overlap,
                separators=self.text_splitter._separators,
                keep_separator=self.text_splitter._keep_separator,
            )

        for block in raw_blocks:
            splits = target_splitter.split_text(block["content"])
            original_meta = block.get("metadata", {})
            for split in splits:
                new_meta = original_meta.copy()
                refined.append({"content": split, "metadata": new_meta})
        return refined

    def _persist_chunks_with_embeddings(
        self,
        doc: Document,
        chunks: List[Dict[str, Any]],
        *,
        chunking_mode: str = "flat",
        chunking_fingerprint: str | None = None,
        document_version: DocumentVersion | None = None,
    ):
        """Chunk embedding 생성, 암호화, versioned chunk 저장을 한 ingestion boundary에서 처리한다."""
        import tiktoken
        from services.llm_service import LLMService
        from utils.encryption import encryption_manager
        from utils.template_utils import count_tokens

        is_hierarchical = chunking_mode == CHUNKING_MODE_HIERARCHICAL
        prepared_chunks = []

        # LLM 클라이언트 초기화 (임베딩 생성용)
        # API Key 오류 등 발생 시 즉시 실패 처리 (상위에서 catch)
        llm_client = None
        if self.user_id:
            llm_client = LLMService.get_client_for_user(
                db=self.db,
                user_id=self.user_id,
                model_id=self.ai_model,  # 예: "text-embedding-3-small"
            )
        else:
            logger.warning("No user_id provided for embedding generation")
            # user_id가 없는 경우도 에러로 처리하거나, 필요하다면 정책 결정.
            # 현재 로직상 user_id 필수.

        # ========================================
        # 토큰 기반 배치 구성
        # ========================================
        MAX_TOKENS_PER_TEXT = 8000  # 개별 텍스트 최대
        MAX_TEXTS_PER_BATCH = 50  # 배치당 최대 텍스트 개수

        batches = []
        current_batch = []

        for i, chunk in enumerate(chunks):
            content = chunk["content"]
            tokens = count_tokens(content)

            # 개별 텍스트 토큰 체크
            if tokens > MAX_TOKENS_PER_TEXT:
                logger.warning(
                    f"Text too long ({tokens} tokens), truncating to {MAX_TOKENS_PER_TEXT}"
                )
                # 토큰 수만큼 자르기
                try:
                    encoding = tiktoken.encoding_for_model(self.ai_model)
                    encoded = encoding.encode(content)
                    truncated = encoded[:MAX_TOKENS_PER_TEXT]
                    content = encoding.decode(truncated)
                    chunk["content"] = content
                except Exception as exc:
                    logger.error(
                        "Failed to truncate embedding input: error_type=%s",
                        type(exc).__name__,
                    )

            # 배치 크기 체크
            if len(current_batch) >= MAX_TEXTS_PER_BATCH:
                batches.append(current_batch)
                current_batch = []

            current_batch.append((i, chunk))

        if current_batch:
            batches.append(current_batch)

        # ========================================
        # 배치별 임베딩 생성
        # ========================================
        all_embeddings = {}  # {chunk_index: embedding}

        for batch_idx, batch in enumerate(batches):
            batch_texts = [chunk["content"] for _, chunk in batch]
            batch_indices = [idx for idx, _ in batch]

            # 진행률 업데이트 (임베딩 80% 비중)
            current_processed = sum(len(b) for b in batches[: batch_idx + 1])
            progress = int((current_processed / len(chunks)) * 80)

            # Redis에 진행률 저장
            self._update_progress_redis(doc.id, progress)

            if llm_client:
                # 배치 임베딩 호출 - 실패 시 즉시 에러 발생 (Raise)
                import asyncio
                import inspect

                # embed_batch가 async 함수이므로 동기적으로 결과 실행
                embeddings_result = llm_client.embed_batch(batch_texts)
                if inspect.iscoroutine(embeddings_result):
                    # [FIX] 중첩 이벤트 루프 문제 해결
                    # 이미 실행 중인 루프가 있으면 별도 스레드에서 실행
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None

                    if loop and loop.is_running():
                        import concurrent.futures

                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(asyncio.run, embeddings_result)
                            batch_embeddings = future.result()
                    else:
                        batch_embeddings = asyncio.run(embeddings_result)
                else:
                    batch_embeddings = embeddings_result

                # 인덱스 매핑
                for idx, embedding in zip(batch_indices, batch_embeddings):
                    all_embeddings[idx] = embedding
            else:
                # 여기까지 왔는데 client가 없으면 에러
                raise ValueError("LLM Client initialization failed.")

        # ========================================
        # content 암호화 & DB 저장 준비
        # ========================================
        keyword_error_logged = False
        for i, chunk in enumerate(chunks):
            # 10개마다 진행률 업데이트 (나머지 20% 비중)
            if (i + 1) % 10 == 0 or (i + 1) == len(chunks):
                progress = _pre_finalization_chunk_progress(i + 1, len(chunks))
                # Redis에 진행률 저장
                self._update_progress_redis(doc.id, progress)

            content = chunk["content"]
            embedding = all_embeddings.get(i)

            if not embedding:
                # 이론상 발생 불가하지만 안전장치
                raise ValueError(f"Embedding not found for chunk {i}")

            # Keyword Extraction (for Hybrid Search)
            keywords = []
            try:
                import nltk
                from rake_nltk import Rake

                if _nltk_resources_available(nltk):
                    r = Rake()
                    r.extract_keywords_from_text(content)
                    keywords = r.get_ranked_phrases()[:10]
                elif not keyword_error_logged:
                    logger.warning(
                        "Keyword extraction skipped: required NLTK resources unavailable"
                    )
                    keyword_error_logged = True
            except Exception as exc:
                # 키워드 추출 실패는 치명적이지 않음 (로그만 남김)
                if not keyword_error_logged:
                    logger.warning(
                        "Keyword extraction failed: error_type=%s",
                        type(exc).__name__,
                    )
                    keyword_error_logged = True

            # 메타데이터에 키워드 추가
            chunk_metadata = chunk.get("metadata", {}) or {}
            chunk_metadata["keywords"] = keywords

            # Content 전체 암호화
            try:
                encrypted_content = encryption_manager.encrypt(content)
            except Exception as exc:
                logger.error(
                    "Failed to encrypt chunk content: chunk_index=%s error_type=%s",
                    i,
                    type(exc).__name__,
                )
                if is_hierarchical:
                    raise RuntimeError(
                        "hierarchical chunk encryption failed"
                    ) from exc
                # 기존 flat 경로의 호환성은 이번 범위에서 유지한다.
                encrypted_content = content

            chunk_level = chunk.get("chunk_level") or "flat"
            if chunk_level not in {"flat", "parent", "child"}:
                raise ValueError(f"Unsupported chunk_level: {chunk_level}")
            if not is_hierarchical and chunk_level != "flat":
                raise ValueError("non-flat chunk payload requires hierarchical mode")

            prepared_chunks.append(
                {
                    "content": encrypted_content,
                    "chunk_index": i,
                    "chunk_level": chunk_level,
                    "token_count": chunk.get("token_count", 0),
                    "metadata": chunk_metadata,
                    "embedding": embedding,
                    "section_path": chunk.get("section_path"),
                    "heading": chunk.get("heading"),
                    "local_ref": chunk.get("local_ref"),
                    "parent_ref": chunk.get("parent_ref"),
                }
            )

        # !!! CRITICAL: 기존 검색 가능 청크는 준비가 끝나기 전에는 삭제하지 않는다. !!!
        # Versioned 경로는 새 document_version_id의 staging chunk만 교체하고,
        # legacy NULL chunk 정리는 active pointer swap transaction에서 수행한다.
        if document_version is not None:
            self.db.flush()
        chunk_delete_query = self.db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id
        )
        if document_version is not None:
            chunk_delete_query = chunk_delete_query.filter(
                DocumentChunk.document_version_id == document_version.id
            )
        chunk_delete_query.delete(synchronize_session=False)

        if is_hierarchical:
            parent_objects = {}
            child_payloads = []
            for payload in prepared_chunks:
                if payload["chunk_level"] == "parent":
                    parent = DocumentChunk(
                        document_id=doc.id,
                        document_version_id=(
                            document_version.id if document_version else None
                        ),
                        knowledge_base_id=doc.knowledge_base_id,
                        content=payload["content"],
                        chunk_index=payload["chunk_index"],
                        chunk_level="parent",
                        token_count=payload["token_count"],
                        metadata_=payload["metadata"],
                        embedding=payload["embedding"],
                        section_path=payload["section_path"],
                        heading=payload["heading"],
                    )
                    self.db.add(parent)
                    parent_objects[payload["local_ref"]] = parent
                elif payload["chunk_level"] == "child":
                    child_payloads.append(payload)
                else:
                    self.db.add(
                        DocumentChunk(
                            document_id=doc.id,
                            document_version_id=(
                                document_version.id if document_version else None
                            ),
                            knowledge_base_id=doc.knowledge_base_id,
                            content=payload["content"],
                            chunk_index=payload["chunk_index"],
                            chunk_level="flat",
                            token_count=payload["token_count"],
                            metadata_=payload["metadata"],
                            embedding=payload["embedding"],
                            section_path=payload["section_path"],
                            heading=payload["heading"],
                        )
                    )

            self.db.flush()
            for payload in child_payloads:
                parent = parent_objects.get(payload["parent_ref"])
                if parent is None:
                    raise ValueError("child chunk parent reference is missing")
                self.db.add(
                    DocumentChunk(
                        document_id=doc.id,
                        document_version_id=(
                            document_version.id if document_version else None
                        ),
                        knowledge_base_id=doc.knowledge_base_id,
                        content=payload["content"],
                        chunk_index=payload["chunk_index"],
                        parent_chunk_id=parent.id,
                        chunk_level="child",
                        token_count=payload["token_count"],
                        metadata_=payload["metadata"],
                        embedding=payload["embedding"],
                        section_path=payload["section_path"],
                        heading=payload["heading"],
                    )
                )
        else:
            self.db.bulk_save_objects(
                [
                    DocumentChunk(
                        document_id=doc.id,
                        document_version_id=(
                            document_version.id if document_version else None
                        ),
                        knowledge_base_id=doc.knowledge_base_id,
                        content=payload["content"],
                        chunk_index=payload["chunk_index"],
                        chunk_level="flat",
                        token_count=payload["token_count"],
                        metadata_=payload["metadata"],
                        embedding=payload["embedding"],
                    )
                    for payload in prepared_chunks
                ]
            )

        # 임베딩 생성 시 사용한 모델명 저장
        doc.embedding_model = self.ai_model
        new_meta = dict(doc.meta_info or {})
        new_meta["chunking_mode"] = chunking_mode
        if chunking_fingerprint:
            new_meta["chunking_fingerprint_hash"] = chunking_fingerprint
        if is_hierarchical:
            parent_size = parent_target_size(doc.chunk_size)
            new_meta["hierarchy_version"] = HIERARCHY_VERSION
            new_meta["hierarchy_parent_target_size"] = parent_size
            new_meta["hierarchy_parent_overlap"] = parent_overlap_size(
                doc.chunk_overlap,
                parent_size,
            )
            new_meta["hierarchy_child_size"] = doc.chunk_size
            new_meta["hierarchy_child_overlap"] = doc.chunk_overlap
        doc.meta_info = new_meta
        self.db.add(doc)

        if document_version is not None:
            self.db.flush()
        else:
            self.db.commit()

    def _update_status(
        self,
        document_id: UUID,
        status: str,
        error_message: str = None,
        progress: int = None,
        meta_updates: dict[str, Any | None] | None = None,
        commit: bool = True,
    ):
        doc = self.db.query(Document).get(document_id)
        if doc:
            doc.status = status
            doc.error_message = error_message
            doc.updated_at = datetime.now(timezone.utc)

            # 진행률 업데이트 (meta_info 활용)
            if progress is not None or meta_updates:
                new_meta = dict(doc.meta_info or {})
                if progress is not None:
                    new_meta["progress"] = progress
                for key, value in (meta_updates or {}).items():
                    if value is None:
                        new_meta.pop(key, None)
                    else:
                        new_meta[key] = value
                doc.meta_info = new_meta

            if commit:
                self.db.commit()
            else:
                self.db.flush()

    def _safe_ingestion_error_message(self, error: Exception) -> str:
        if isinstance(error, KnowledgeIngestionFinalizationError):
            return "문서 색인 최종화에 실패했습니다."
        if (
            isinstance(error, DurableIngestionSourceFailure)
            and error.reason_code == RAW_PARSER_EGRESS_UNAVAILABLE_REASON
        ):
            return "외부 문서 파서를 사용할 수 없습니다."
        return "문서 처리에 실패했습니다."

    def reindex_knowledge_base(self, kb_id: UUID, new_model: str):
        """
        KB의 모든 문서를 새 임베딩 모델로 재인덱싱
        """
        self.ai_model = new_model

        documents = (
            self.db.query(Document).filter(Document.knowledge_base_id == kb_id).all()
        )

        if not documents:
            logger.warning(f"No documents found for KB {kb_id}")
            return

        for doc in documents:
            try:
                self._update_status(doc.id, "pending")
                self.process_document(doc.id)
            except Exception as error:
                logger.error(
                    "Failed to re-index document %s, error_type: %s",
                    doc.id,
                    type(error).__name__,
                )
                self._update_status(
                    doc.id,
                    "failed",
                    self._safe_ingestion_error_message(error),
                )

    def preprocess_text(self, text: str, options: Dict[str, Any]) -> str:
        """
        RAG를 위한 텍스트 전처리

        Args:
            text: 원본 텍스트
            options: 전처리 옵션
                - remove_urls_emails: URL/이메일 제거 (기본: False)
                - normalize_whitespace: 공백 정규화 (기본: True)
                - remove_markdown_separators: 마크다운 구분자 제거 (기본: True)
                - remove_control_chars: 제어 문자 제거 (기본: True)

        Returns:
            전처리된 텍스트
        """
        # === 필수 전처리 (항상 적용) ===

        # 1. 유니코드 정규화 (한글 자모 분리 방지)
        text = unicodedata.normalize("NFC", text)

        # === 선택적 전처리 ===

        # 2. 마크다운 구분자 처리 (LlamaParse 출력용)
        if options.get("remove_markdown_separators", True):
            # 수평선을 단일 줄바꿈으로 변환
            text = re.sub(r"^-{3,}$", "\n", text, flags=re.MULTILINE)
            text = re.sub(r"^\*{3,}$", "\n", text, flags=re.MULTILINE)
            text = re.sub(r"^={3,}$", "\n", text, flags=re.MULTILINE)

        # 3. URL/이메일 제거
        if options.get("remove_urls_emails", False):
            # URL 제거 (http, https, ftp, ftps)
            text = re.sub(
                r"(?:https?|ftps?)://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
                "",
                text,
            )
            # 이메일 제거
            text = re.sub(r"[\w\.-]+@[\w\.-]+\.\w+", "", text)

        # 4. 공백 정규화
        if options.get("normalize_whitespace", True):
            # 단일 공백
            text = re.sub(r"[ \t]+", " ", text)
            # 과도한 줄바꿈 (3줄 이상 → 2줄)
            text = re.sub(r"\n{3,}", "\n\n", text)
            # 줄 끝 공백 제거
            text = re.sub(r"[ \t]+\n", "\n", text)
            # 줄 시작 공백 제거
            text = re.sub(r"\n[ \t]+", "\n", text)

        # 5. 제어 문자 제거 (탭, 줄바꿈 제외)
        if options.get("remove_control_chars", True):
            text = "".join(
                char
                for char in text
                if unicodedata.category(char)[0] != "C" or char in "\n\t"
            )

        # === 필수 전처리 (마무리) ===

        # 6. 앞뒤 공백 제거
        text = text.strip()

        return text
