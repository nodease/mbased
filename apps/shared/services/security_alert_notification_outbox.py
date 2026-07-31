from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from apps.shared.celery_app import celery_app
from apps.shared.db.models.security_alert import SecurityAlertNotificationOutbox
from apps.shared.services.notification_pubsub import (
    deliver_notifications_changed_to_organization_managers,
)
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

OUTBOX_TASK_NAME = "security_alert.notification_outbox.deliver"
OUTBOX_EVENT_NOTIFICATIONS_CHANGED = "notifications.changed"
OUTBOX_STATUS_PENDING = "pending"
OUTBOX_STATUS_LEASED = "leased"
OUTBOX_STATUS_SUCCEEDED = "succeeded"
OUTBOX_STATUS_RETRY_SCHEDULED = "retry_scheduled"
OUTBOX_STATUS_DEAD_LETTERED = "dead_lettered"
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LEASE_SECONDS = 300
DEFAULT_RETRY_SECONDS = 60
DEFAULT_PROCESS_LIMIT = 100
NotificationDeliverer = Callable[[Any, uuid.UUID], None]


@dataclass(frozen=True)
class SecurityAlertNotificationProcessResult:
    processed_count: int
    recovered_count: int


class SecurityAlertNotificationOutboxService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def enqueue(
        self,
        *,
        organization_id: uuid.UUID,
        idempotency_key: str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> SecurityAlertNotificationOutbox:
        existing = self._find_existing(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing
        event = SecurityAlertNotificationOutbox(
            organization_id=organization_id,
            event_type=OUTBOX_EVENT_NOTIFICATIONS_CHANGED,
            idempotency_key=idempotency_key,
            status=OUTBOX_STATUS_PENDING,
            attempt_count=0,
            max_attempts=max_attempts,
            retryable=True,
        )
        try:
            with self.db.begin_nested():
                self.db.add(event)
                self.db.flush()
        except IntegrityError:
            # A concurrent realtime/reconciliation transaction may have won the
            # unique key race. Roll back only the savepoint, not the alert UoW.
            existing = self._find_existing(
                organization_id=organization_id,
                idempotency_key=idempotency_key,
            )
            if existing is None:
                raise
            return existing
        return event

    def _find_existing(
        self,
        *,
        organization_id: uuid.UUID,
        idempotency_key: str,
    ) -> SecurityAlertNotificationOutbox | None:
        return (
            self.db.query(SecurityAlertNotificationOutbox)
            .filter(
                SecurityAlertNotificationOutbox.organization_id == organization_id,
                SecurityAlertNotificationOutbox.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )

    def lease_due_events(
        self,
        *,
        owner_token: str,
        limit: int = DEFAULT_PROCESS_LIMIT,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> list[SecurityAlertNotificationOutbox]:
        now = now or datetime.now(timezone.utc)
        events = (
            self.db.query(SecurityAlertNotificationOutbox)
            .filter(
                SecurityAlertNotificationOutbox.status.in_(
                    [OUTBOX_STATUS_PENDING, OUTBOX_STATUS_RETRY_SCHEDULED]
                ),
                or_(
                    SecurityAlertNotificationOutbox.next_retry_at.is_(None),
                    SecurityAlertNotificationOutbox.next_retry_at <= now,
                ),
            )
            .order_by(SecurityAlertNotificationOutbox.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        for event in events:
            event.status = OUTBOX_STATUS_LEASED
            event.owner_token = owner_token
            event.lease_expires_at = now + timedelta(seconds=lease_seconds)
            event.attempt_count += 1
            event.updated_at = now
        self.db.flush()
        return events

    def recover_stale_leases(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        events = (
            self.db.query(SecurityAlertNotificationOutbox)
            .filter(
                SecurityAlertNotificationOutbox.status == OUTBOX_STATUS_LEASED,
                SecurityAlertNotificationOutbox.lease_expires_at.is_not(None),
                SecurityAlertNotificationOutbox.lease_expires_at <= now,
            )
            .with_for_update(skip_locked=True)
            .all()
        )
        for event in events:
            self._apply_retry_or_dead_letter(
                event,
                safe_reason_code="notification.lease_expired",
                now=now,
            )
        self.db.flush()
        return len(events)

    def mark_succeeded(
        self,
        event: SecurityAlertNotificationOutbox,
        *,
        owner_token: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        owned_event = self._lock_owned_lease(
            event_id=event.id,
            owner_token=owner_token,
        )
        if owned_event is None:
            return False
        owned_event.status = OUTBOX_STATUS_SUCCEEDED
        owned_event.owner_token = None
        owned_event.lease_expires_at = None
        owned_event.next_retry_at = None
        owned_event.safe_reason_code = None
        owned_event.delivered_at = now
        owned_event.updated_at = now
        return True

    def mark_retry_or_dead_letter(
        self,
        event: SecurityAlertNotificationOutbox,
        *,
        owner_token: str,
        safe_reason_code: str,
        now: datetime | None = None,
        retry_after_seconds: int = DEFAULT_RETRY_SECONDS,
    ) -> bool:
        owned_event = self._lock_owned_lease(
            event_id=event.id,
            owner_token=owner_token,
        )
        if owned_event is None:
            return False
        self._apply_retry_or_dead_letter(
            owned_event,
            safe_reason_code=safe_reason_code,
            now=now,
            retry_after_seconds=retry_after_seconds,
        )
        return True

    def _lock_owned_lease(
        self,
        *,
        event_id: uuid.UUID,
        owner_token: str,
    ) -> SecurityAlertNotificationOutbox | None:
        return (
            self.db.query(SecurityAlertNotificationOutbox)
            .filter(
                SecurityAlertNotificationOutbox.id == event_id,
                SecurityAlertNotificationOutbox.status == OUTBOX_STATUS_LEASED,
                SecurityAlertNotificationOutbox.owner_token == owner_token,
            )
            .with_for_update()
            .one_or_none()
        )

    def _apply_retry_or_dead_letter(
        self,
        event: SecurityAlertNotificationOutbox,
        *,
        safe_reason_code: str,
        now: datetime | None = None,
        retry_after_seconds: int = DEFAULT_RETRY_SECONDS,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        event.owner_token = None
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


class SecurityAlertNotificationOutboxProcessor:
    def __init__(
        self,
        db: Session,
        *,
        deliver: NotificationDeliverer = (
            deliver_notifications_changed_to_organization_managers
        ),
    ) -> None:
        self.db = db
        self.outbox = SecurityAlertNotificationOutboxService(db)
        self.deliver = deliver

    def process_due_events(
        self,
        *,
        owner_token: str,
        limit: int = DEFAULT_PROCESS_LIMIT,
    ) -> SecurityAlertNotificationProcessResult:
        recovered_count = self.outbox.recover_stale_leases()
        events = self.outbox.lease_due_events(owner_token=owner_token, limit=limit)
        # The lease and attempt counter must survive a worker crash or hard timeout
        # while the external Redis delivery is in progress.
        self.db.commit()
        processed_count = 0
        for event in events:
            try:
                if event.event_type != OUTBOX_EVENT_NOTIFICATIONS_CHANGED:
                    raise ValueError("unsupported_event_type")
                self.deliver(self.db, event.organization_id)
            except Exception:
                # A manager lookup can leave the delivery transaction aborted.
                # The durable lease is already committed, so reset only this
                # delivery transaction before recording the retry outcome.
                self.db.rollback()
                self.outbox.mark_retry_or_dead_letter(
                    event,
                    owner_token=owner_token,
                    safe_reason_code="notification.delivery_failed",
                )
                self.db.commit()
                continue
            if self.outbox.mark_succeeded(event, owner_token=owner_token):
                processed_count += 1
            self.db.commit()
        return SecurityAlertNotificationProcessResult(
            processed_count=processed_count,
            recovered_count=recovered_count,
        )


def enqueue_security_alert_notification(
    db: Session,
    *,
    scoped_organization_id: uuid.UUID,
    idempotency_key: str,
) -> SecurityAlertNotificationOutbox:
    return SecurityAlertNotificationOutboxService(db).enqueue(
        organization_id=scoped_organization_id,
        idempotency_key=idempotency_key,
    )


def detection_notification_idempotency_key(audit_id: uuid.UUID) -> str:
    return f"audit:{audit_id}:notifications.changed"


def lifecycle_notification_idempotency_key(
    alert_id: uuid.UUID,
    lifecycle_version: int,
) -> str:
    return f"security-alert:{alert_id}:lifecycle:{lifecycle_version}"


def dispatch_security_alert_notification_outbox() -> None:
    celery_app.send_task(OUTBOX_TASK_NAME, kwargs={})
