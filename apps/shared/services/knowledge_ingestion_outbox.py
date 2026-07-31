import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.shared.db.models.knowledge import (
    KnowledgeIngestionOutbox,
)
from sqlalchemy import or_
from sqlalchemy.orm import Session

OUTBOX_STATUS_PENDING = "pending"
OUTBOX_STATUS_LEASED = "leased"
OUTBOX_STATUS_SUCCEEDED = "succeeded"
OUTBOX_STATUS_RETRY_SCHEDULED = "retry_scheduled"
OUTBOX_STATUS_DEAD_LETTERED = "dead_lettered"
OUTBOX_EVENT_CLEANUP_SUPERSEDED = "document_version.cleanup_superseded"
DEFAULT_OUTBOX_MAX_ATTEMPTS = 5
DEFAULT_OUTBOX_LEASE_SECONDS = 300
DEFAULT_OUTBOX_RETRY_SECONDS = 60
DEFAULT_OUTBOX_PROCESS_LIMIT = 100
MAX_OUTBOX_PROCESS_LIMIT = 5000


class KnowledgeIngestionOutboxService:
    """Knowledge ingestion side effect를 idempotent outbox로 조정한다."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def enqueue(
        self,
        *,
        organization_id: uuid.UUID,
        event_type: str,
        idempotency_key: str,
        knowledge_base_id: uuid.UUID | None = None,
        document_version_id: uuid.UUID | None = None,
        source_identity_id: uuid.UUID | None = None,
        target_ref: dict[str, Any] | None = None,
        safe_metadata: dict[str, Any] | None = None,
        max_attempts: int = DEFAULT_OUTBOX_MAX_ATTEMPTS,
    ) -> KnowledgeIngestionOutbox:
        """같은 idempotency key의 event는 새 row를 만들지 않고 기존 row를 재사용한다."""

        existing = (
            self.db.query(KnowledgeIngestionOutbox)
            .filter(
                KnowledgeIngestionOutbox.organization_id == organization_id,
                KnowledgeIngestionOutbox.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )
        if existing:
            return existing

        event = KnowledgeIngestionOutbox(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            document_version_id=document_version_id,
            source_identity_id=source_identity_id,
            event_type=event_type,
            idempotency_key=idempotency_key,
            status=OUTBOX_STATUS_PENDING,
            max_attempts=max_attempts,
            target_ref=target_ref or {},
            safe_metadata=safe_metadata or {},
        )
        self.db.add(event)
        self.db.flush()
        return event

    def lease_due_events(
        self,
        *,
        owner_token: str,
        limit: int = DEFAULT_OUTBOX_PROCESS_LIMIT,
        lease_seconds: int = DEFAULT_OUTBOX_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> list[KnowledgeIngestionOutbox]:
        now = now or self._now()
        limit = self.validate_limit(limit)
        rows = (
            self.db.query(KnowledgeIngestionOutbox)
            .filter(
                KnowledgeIngestionOutbox.status.in_(
                    [OUTBOX_STATUS_PENDING, OUTBOX_STATUS_RETRY_SCHEDULED]
                ),
                or_(
                    KnowledgeIngestionOutbox.next_retry_at.is_(None),
                    KnowledgeIngestionOutbox.next_retry_at <= now,
                ),
            )
            .order_by(KnowledgeIngestionOutbox.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        for row in rows:
            row.status = OUTBOX_STATUS_LEASED
            row.owner_token = owner_token
            row.fencing_token = self._new_token()
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            row.attempt_count += 1
            row.updated_at = now
        self.db.flush()
        return rows

    def recover_stale_leases(
        self,
        *,
        now: datetime | None = None,
        retry_after_seconds: int = DEFAULT_OUTBOX_RETRY_SECONDS,
    ) -> int:
        """만료된 lease를 retry/dead-letter 상태로 돌려 recovery scanner가 재처리하게 한다."""

        now = now or self._now()
        rows = (
            self.db.query(KnowledgeIngestionOutbox)
            .filter(
                KnowledgeIngestionOutbox.status == OUTBOX_STATUS_LEASED,
                KnowledgeIngestionOutbox.lease_expires_at.is_not(None),
                KnowledgeIngestionOutbox.lease_expires_at <= now,
            )
            .all()
        )
        for row in rows:
            self.mark_retry_or_dead_letter(
                row,
                safe_reason_code="outbox.lease_expired",
                now=now,
                retry_after_seconds=retry_after_seconds,
            )
        self.db.flush()
        return len(rows)

    @classmethod
    def validate_limit(cls, value: Any) -> int:
        try:
            limit = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_knowledge_outbox_limit") from exc
        if limit < 1 or limit > MAX_OUTBOX_PROCESS_LIMIT:
            raise ValueError("invalid_knowledge_outbox_limit")
        return limit

    def mark_succeeded(
        self,
        event: KnowledgeIngestionOutbox,
        *,
        safe_metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or self._now()
        event.status = OUTBOX_STATUS_SUCCEEDED
        event.owner_token = None
        event.fencing_token = None
        event.lease_expires_at = None
        event.next_retry_at = None
        event.safe_reason_code = None
        if safe_metadata:
            event.safe_metadata = {**(event.safe_metadata or {}), **safe_metadata}
        event.updated_at = now

    def mark_retry_or_dead_letter(
        self,
        event: KnowledgeIngestionOutbox,
        *,
        safe_reason_code: str,
        now: datetime | None = None,
        retry_after_seconds: int = DEFAULT_OUTBOX_RETRY_SECONDS,
    ) -> None:
        now = now or self._now()
        event.owner_token = None
        event.fencing_token = None
        event.lease_expires_at = None
        event.safe_reason_code = safe_reason_code
        if event.retryable and event.attempt_count < event.max_attempts:
            event.status = OUTBOX_STATUS_RETRY_SCHEDULED
            event.next_retry_at = now + timedelta(seconds=retry_after_seconds)
        else:
            event.status = OUTBOX_STATUS_DEAD_LETTERED
            event.next_retry_at = None
            event.dead_lettered_at = now
        event.updated_at = now

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _new_token(self) -> str:
        return str(uuid.uuid4())
