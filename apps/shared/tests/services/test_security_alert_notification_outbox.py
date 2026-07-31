from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.exc import IntegrityError


def _module():
    return importlib.import_module(
        "apps.shared.services.security_alert_notification_outbox"
    )


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def with_for_update(self, **kwargs):
        return self

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class _Db:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def query(self, model):
        return _Query(self.rows)

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        self.flushes += 1

    def begin_nested(self):
        return _Savepoint(self)


class _Savepoint:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            self.db.savepoint_rollbacks += 1
        return False


class _ConcurrentEnqueueDb(_Db):
    def __init__(self, winner):
        super().__init__()
        self.winner = winner
        self.savepoint_rollbacks = 0
        self.outer_state = ["alert-change"]
        self._conflict_pending = True

    def flush(self):
        super().flush()
        if self._conflict_pending:
            self._conflict_pending = False
            self.rows = [self.winner]
            raise IntegrityError("insert", {}, RuntimeError("duplicate"))


class _ProcessorDb:
    def __init__(self, events):
        self.events = events

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")


def test_enqueue_is_idempotent_and_stores_no_notification_payload():
    module = _module()
    db = _Db()
    service = module.SecurityAlertNotificationOutboxService(db)
    organization_id = uuid4()

    first = service.enqueue(
        organization_id=organization_id,
        idempotency_key="audit:123:notifications.changed",
    )
    second = service.enqueue(
        organization_id=organization_id,
        idempotency_key="audit:123:notifications.changed",
    )

    assert second is first
    assert len(db.rows) == 1
    assert first.organization_id == organization_id
    assert first.event_type == "notifications.changed"
    assert first.status == "pending"
    assert first.max_attempts == 5
    assert not hasattr(first, "payload")
    assert not hasattr(first, "recipient_ids")


def test_concurrent_enqueue_returns_winner_without_rolling_back_outer_work():
    module = _module()
    organization_id = uuid4()
    winner = SimpleNamespace(
        organization_id=organization_id,
        idempotency_key="audit:123:notifications.changed",
    )
    db = _ConcurrentEnqueueDb(winner)

    result = module.SecurityAlertNotificationOutboxService(db).enqueue(
        organization_id=organization_id,
        idempotency_key="audit:123:notifications.changed",
    )

    assert result is winner
    assert db.savepoint_rollbacks == 1
    assert db.outer_state == ["alert-change"]


def test_retry_is_scheduled_then_fifth_failure_is_dead_lettered():
    module = _module()
    now = datetime(2026, 7, 13, 5, 0, tzinfo=timezone.utc)
    db = _Db()
    service = module.SecurityAlertNotificationOutboxService(db)
    event = SimpleNamespace(
        id=uuid4(),
        status="leased",
        owner_token="worker-1",
        lease_expires_at=now + timedelta(minutes=5),
        attempt_count=1,
        max_attempts=5,
        retryable=True,
        next_retry_at=None,
        safe_reason_code=None,
        delivered_at=None,
        dead_lettered_at=None,
        updated_at=None,
    )
    db.rows = [event]

    assert service.mark_retry_or_dead_letter(
        event,
        owner_token="worker-1",
        safe_reason_code="notification.delivery_failed",
        now=now,
        retry_after_seconds=60,
    ) is True

    assert event.status == "retry_scheduled"
    assert event.next_retry_at == now + timedelta(seconds=60)
    assert event.safe_reason_code == "notification.delivery_failed"
    assert event.owner_token is None
    assert event.lease_expires_at is None

    event.status = "leased"
    event.owner_token = "worker-1"
    event.attempt_count = 5
    assert service.mark_retry_or_dead_letter(
        event,
        owner_token="worker-1",
        safe_reason_code="notification.delivery_failed",
        now=now,
    ) is True

    assert event.status == "dead_lettered"
    assert event.next_retry_at is None
    assert event.dead_lettered_at == now


def test_stale_lease_owner_cannot_overwrite_terminal_outbox_state():
    module = _module()
    now = datetime(2026, 7, 13, 5, 0, tzinfo=timezone.utc)
    event = SimpleNamespace(
        id=uuid4(),
        status="succeeded",
        owner_token=None,
        lease_expires_at=None,
        attempt_count=2,
        max_attempts=5,
        retryable=True,
        next_retry_at=None,
        safe_reason_code=None,
        delivered_at=now,
        dead_lettered_at=None,
        updated_at=now,
    )
    service = module.SecurityAlertNotificationOutboxService(_Db())

    assert service.mark_succeeded(
        event,
        owner_token="stale-worker",
        now=now + timedelta(minutes=1),
    ) is False
    assert service.mark_retry_or_dead_letter(
        event,
        owner_token="stale-worker",
        safe_reason_code="notification.delivery_failed",
        now=now + timedelta(minutes=1),
    ) is False

    assert event.status == "succeeded"
    assert event.delivered_at == now
    assert event.safe_reason_code is None


def test_processor_delivers_current_manager_refresh_and_marks_success(monkeypatch):
    module = _module()
    organization_id = uuid4()
    event = SimpleNamespace(
        organization_id=organization_id,
        event_type="notifications.changed",
    )
    events = []

    class Outbox:
        def __init__(self, db):
            self.db = db

        def recover_stale_leases(self):
            return 0

        def lease_due_events(self, *, owner_token, limit):
            assert owner_token == "worker-1"
            events.append("lease")
            return [event]

        def mark_succeeded(self, row, *, owner_token):
            assert row is event
            assert owner_token == "worker-1"
            events.append("succeeded")
            return True

        def mark_retry_or_dead_letter(
            self, row, *, owner_token, safe_reason_code
        ):
            raise AssertionError(safe_reason_code)

    monkeypatch.setattr(module, "SecurityAlertNotificationOutboxService", Outbox)
    db = _ProcessorDb(events)
    processor = module.SecurityAlertNotificationOutboxProcessor(
        db,
        deliver=lambda scoped_db, scoped_org_id: events.append("deliver"),
    )

    result = processor.process_due_events(owner_token="worker-1", limit=10)

    assert result.processed_count == 1
    assert result.recovered_count == 0
    assert events == ["lease", "commit", "deliver", "succeeded", "commit"]


def test_processor_converts_delivery_exception_to_safe_retry_reason(monkeypatch):
    module = _module()
    event = SimpleNamespace(
        organization_id=uuid4(),
        event_type="notifications.changed",
    )
    events = []

    class Outbox:
        def __init__(self, db):
            pass

        def recover_stale_leases(self):
            return 0

        def lease_due_events(self, *, owner_token, limit):
            events.append("lease")
            return [event]

        def mark_succeeded(self, row, *, owner_token):
            raise AssertionError("failure must not be marked as succeeded")

        def mark_retry_or_dead_letter(
            self, row, *, owner_token, safe_reason_code
        ):
            assert row is event
            assert owner_token == "worker-1"
            events.append(("retry", safe_reason_code))
            return True

    monkeypatch.setattr(module, "SecurityAlertNotificationOutboxService", Outbox)
    secret = "raw-redis-secret"

    def fail_delivery(db, organization_id):
        events.append("deliver")
        raise RuntimeError(secret)

    result = module.SecurityAlertNotificationOutboxProcessor(
        _ProcessorDb(events),
        deliver=fail_delivery,
    ).process_due_events(owner_token="worker-1")

    assert result.processed_count == 0
    assert events == [
        "lease",
        "commit",
        "deliver",
        "rollback",
        ("retry", "notification.delivery_failed"),
        "commit",
    ]
    assert secret not in events[4][1]
