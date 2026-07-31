from datetime import datetime, timezone
from typing import Any

from apps.shared.db.models.knowledge import (
    DocumentVersion,
    KnowledgeBase,
    KnowledgeSourceIdentity,
)
from sqlalchemy.orm import Session


class KnowledgeSyncCursorError(RuntimeError):
    """Cursor/watermark를 안전하게 전진할 수 없을 때 사용한다."""


class KnowledgeSyncCursorService:
    """
    Content cursor와 ACL/permission watermark를 분리해서 전진한다.

    이 서비스는 raw source cursor/token을 받지 않는다. 호출자는 source별 원문 cursor를
    protected storage/HMAC ref로 바꾼 뒤 `*_ref` 값만 전달해야 한다.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def advance_content_cursor_after_finalization(
        self,
        *,
        source_identity: KnowledgeSourceIdentity,
        document_version: DocumentVersion,
        content_cursor_ref: str,
        now: datetime | None = None,
    ) -> None:
        now = now or self._now()
        kb = self.db.get(KnowledgeBase, document_version.knowledge_base_id)
        if not kb or kb.active_document_version_id != document_version.id:
            raise KnowledgeSyncCursorError("content_cursor.requires_active_version")
        if document_version.status != "ready":
            raise KnowledgeSyncCursorError("content_cursor.requires_ready_version")

        self._update_sync_refs(
            source_identity,
            {
                "content_cursor_ref": content_cursor_ref,
                "content_document_version_id": str(document_version.id),
                "content_committed_at": now.isoformat(),
            },
            now=now,
        )

    def advance_acl_watermark_after_permission_commit(
        self,
        *,
        source_identity: KnowledgeSourceIdentity,
        acl_watermark_ref: str,
        freshness_epoch: int,
        candidate_cache_epoch: int | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or self._now()
        if freshness_epoch < 0:
            raise KnowledgeSyncCursorError("acl_watermark.invalid_freshness_epoch")
        payload: dict[str, Any] = {
            "acl_watermark_ref": acl_watermark_ref,
            "acl_freshness_epoch": freshness_epoch,
            "acl_committed_at": now.isoformat(),
        }
        if candidate_cache_epoch is not None:
            payload["candidate_cache_epoch"] = candidate_cache_epoch
        self._update_sync_refs(source_identity, payload, now=now)

    def _update_sync_refs(
        self,
        source_identity: KnowledgeSourceIdentity,
        payload: dict[str, Any],
        *,
        now: datetime,
    ) -> None:
        safe_metadata = dict(source_identity.safe_metadata or {})
        sync_refs = dict(safe_metadata.get("sync_refs") or {})
        sync_refs.update(payload)
        safe_metadata["sync_refs"] = sync_refs
        source_identity.safe_metadata = safe_metadata
        source_identity.updated_at = now
        self.db.flush()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
