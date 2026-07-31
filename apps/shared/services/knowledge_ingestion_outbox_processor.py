import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from apps.shared.db.models.knowledge import (
    DocumentChunk,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeIngestionOutbox,
)
from apps.shared.services.knowledge_ingestion_outbox import (
    DEFAULT_OUTBOX_PROCESS_LIMIT,
    OUTBOX_EVENT_CLEANUP_SUPERSEDED,
    KnowledgeIngestionOutboxService,
)
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class KnowledgeOutboxProcessResult:
    processed_count: int
    recovered_count: int


class SupersededVersionCleanupHandler:
    """현재 active version이 아닌 superseded version의 청크 cleanup만 담당한다."""

    def __init__(self, db: Session, outbox: KnowledgeIngestionOutboxService) -> None:
        self.db = db
        self.outbox = outbox

    def process(
        self,
        event: KnowledgeIngestionOutbox,
        *,
        now: datetime | None = None,
    ) -> int:
        now = now or self._now()
        previous_version_ref = (event.target_ref or {}).get(
            "previous_document_version_id"
        )
        if not previous_version_ref:
            self.outbox.mark_succeeded(
                event,
                safe_metadata={"deleted_chunk_count": 0},
                now=now,
            )
            return 0

        previous_version_id = uuid.UUID(str(previous_version_ref))
        previous_version = self.db.get(DocumentVersion, previous_version_id)
        if previous_version is None:
            self.outbox.mark_succeeded(
                event,
                safe_metadata={"deleted_chunk_count": 0, "version_missing": True},
                now=now,
            )
            return 0

        kb = self.db.get(KnowledgeBase, previous_version.knowledge_base_id)
        if kb and kb.active_document_version_id == previous_version.id:
            raise RuntimeError("active_version_cleanup_refused")

        if previous_version.status != "superseded":
            self.outbox.mark_succeeded(
                event,
                safe_metadata={
                    "deleted_chunk_count": 0,
                    "version_status": previous_version.status,
                },
                now=now,
            )
            return 0

        deleted_count = (
            self.db.query(DocumentChunk)
            .filter(DocumentChunk.document_version_id == previous_version.id)
            .delete(synchronize_session=False)
        )
        self.outbox.mark_succeeded(
            event,
            safe_metadata={"deleted_chunk_count": int(deleted_count or 0)},
            now=now,
        )
        return int(deleted_count or 0)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)


class KnowledgeIngestionOutboxProcessor:
    """Outbox lease/recovery와 event handler dispatch를 Knowledge domain 안에 둔다."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.outbox = KnowledgeIngestionOutboxService(db)
        self.cleanup_handler = SupersededVersionCleanupHandler(db, self.outbox)

    def process_due_events(
        self,
        *,
        owner_token: str,
        limit: int = DEFAULT_OUTBOX_PROCESS_LIMIT,
    ) -> KnowledgeOutboxProcessResult:
        """Due event를 처리하고 상태만 변경한다.

        이 메서드는 transaction을 commit하지 않는다. Celery task나 호출자가
        성공 시 commit, 실패 시 rollback을 담당해야 outbox 처리와 외부 worker
        lifecycle을 한 경계에서 제어할 수 있다.
        """
        recovered_count = self.outbox.recover_stale_leases()
        events = self.outbox.lease_due_events(owner_token=owner_token, limit=limit)
        processed_count = 0
        for event in events:
            try:
                self._process_event(event)
                processed_count += 1
            except Exception:
                self.outbox.mark_retry_or_dead_letter(
                    event,
                    safe_reason_code="outbox.processing_failed",
                )
        return KnowledgeOutboxProcessResult(
            processed_count=processed_count,
            recovered_count=recovered_count,
        )

    def _process_event(self, event: KnowledgeIngestionOutbox) -> None:
        if event.event_type == OUTBOX_EVENT_CLEANUP_SUPERSEDED:
            self.cleanup_handler.process(event)
            return
        self.outbox.mark_retry_or_dead_letter(
            event,
            safe_reason_code="outbox.unsupported_event_type",
        )
