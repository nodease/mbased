from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import WorkflowNodeRun, WorkflowRun
from sqlalchemy.exc import IntegrityError


def _module():
    return importlib.import_module("apps.shared.services.audit_event_outbox")


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
    def __init__(self, rows=None):
        self.rows = rows or []
        self.flushes = 0

    def query(self, model):
        return _Query(self.rows)

    def flush(self):
        self.flushes += 1


class _ProcessorDb:
    def __init__(self, events):
        self.events = events

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")


class _CorrelationDb:
    def __init__(self, rows):
        self.rows = rows
        self.added = []

    def get(self, model, identity):
        if model is AuditLog:
            return None
        return self.rows.get((model, identity))

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


class _ProcessorCorrelationDb(_CorrelationDb):
    def __init__(self, rows, events):
        super().__init__(rows)
        self.events = events

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")


def _leased_event(*, attempt_count=1):
    now = datetime(2026, 7, 16, 3, 0, tzinfo=timezone.utc)
    audit_id = uuid4()
    return SimpleNamespace(
        id=uuid4(),
        payload={
            "id": str(audit_id),
            "occurred_at": now.isoformat(),
            "actor_id": str(uuid4()),
            "actor_type": "user",
            "category": "action",
            "action": "permission.denied",
            "target_type": "workflow",
            "target_id": str(uuid4()),
            "status": "failure",
            "audit_metadata": {"organization_id": str(uuid4())},
        },
        status="leased",
        owner_token="worker-1",
        lease_expires_at=now + timedelta(minutes=5),
        attempt_count=attempt_count,
        max_attempts=5,
        retryable=True,
        next_retry_at=None,
        safe_reason_code=None,
        delivered_at=None,
        dead_lettered_at=None,
        updated_at=None,
    )


def test_retry_is_scheduled_then_fifth_failure_is_dead_lettered():
    module = _module()
    now = datetime(2026, 7, 16, 3, 0, tzinfo=timezone.utc)
    event = _leased_event()
    original_payload = event.payload
    service = module.AuditEventOutboxService(_Db([event]))

    assert service.mark_retry_or_dead_letter(
        event,
        owner_token="worker-1",
        safe_reason_code="audit.persistence_failed",
        now=now,
        retry_after_seconds=60,
    ) is True
    assert event.status == "retry_scheduled"
    assert event.payload is original_payload
    assert event.next_retry_at == now + timedelta(seconds=60)
    assert event.owner_token is None
    assert event.lease_expires_at is None

    event.status = "leased"
    event.owner_token = "worker-1"
    event.attempt_count = 5
    assert service.mark_retry_or_dead_letter(
        event,
        owner_token="worker-1",
        safe_reason_code="audit.persistence_failed",
        now=now,
    ) is True
    assert event.status == "dead_lettered"
    assert event.payload is original_payload
    assert event.next_retry_at is None
    assert event.dead_lettered_at == now


def test_success_clears_delivered_payload_but_keeps_idempotency_tombstone():
    module = _module()
    now = datetime(2026, 7, 16, 3, 0, tzinfo=timezone.utc)
    event = _leased_event()
    event.idempotency_key = event.payload["id"]
    event_id = event.id
    idempotency_key = event.idempotency_key
    service = module.AuditEventOutboxService(_Db([event]))

    assert service.mark_succeeded(
        event,
        owner_token="worker-1",
        now=now,
    ) is True

    assert event.payload == {}
    assert event.id == event_id
    assert event.idempotency_key == idempotency_key
    assert event.status == "succeeded"
    assert event.delivered_at == now


def test_stale_owner_cannot_overwrite_terminal_state():
    module = _module()
    now = datetime(2026, 7, 16, 3, 0, tzinfo=timezone.utc)
    event = _leased_event(attempt_count=2)
    event.status = "succeeded"
    event.owner_token = None
    event.delivered_at = now
    service = module.AuditEventOutboxService(_Db())

    assert service.mark_succeeded(
        event,
        owner_token="stale-worker",
        now=now + timedelta(minutes=1),
    ) is False
    assert service.mark_retry_or_dead_letter(
        event,
        owner_token="stale-worker",
        safe_reason_code="audit.persistence_failed",
        now=now + timedelta(minutes=1),
    ) is False
    assert event.status == "succeeded"
    assert event.delivered_at == now


def test_processor_commits_lease_then_audit_and_success_together(monkeypatch):
    module = _module()
    event = _leased_event()
    events = []

    class Outbox:
        def __init__(self, db):
            pass

        def recover_stale_leases(self):
            return 1

        def lease_due_events(self, *, owner_token, limit):
            events.append("lease")
            return [event]

        def mark_succeeded(self, row, *, owner_token):
            assert row is event
            events.append("succeeded")
            return True

        def mark_retry_or_dead_letter(self, *args, **kwargs):
            raise AssertionError("successful persistence must not retry")

    monkeypatch.setattr(module, "AuditEventOutboxService", Outbox)
    audit_id = uuid4()
    processor = module.AuditEventOutboxProcessor(
        _ProcessorDb(events),
        persist_audit=lambda db, payload: (
            events.append("persist"),
            audit_id,
        )[1],
        after_commit=lambda persisted_id: events.append(("dispatch", persisted_id)),
    )

    result = processor.process_due_events(owner_token="worker-1", limit=10)

    assert result.processed_count == 1
    assert result.recovered_count == 1
    assert events == [
        "lease",
        "commit",
        "persist",
        "succeeded",
        "commit",
        ("dispatch", audit_id),
    ]


def test_processor_converts_persistence_error_to_safe_retry(monkeypatch):
    module = _module()
    event = _leased_event()
    events = []

    class Outbox:
        def __init__(self, db):
            pass

        def recover_stale_leases(self):
            return 0

        def lease_due_events(self, *, owner_token, limit):
            events.append("lease")
            return [event]

        def mark_succeeded(self, *args, **kwargs):
            raise AssertionError("failed persistence must not succeed")

        def mark_retry_or_dead_letter(
            self, row, *, owner_token, safe_reason_code
        ):
            assert row is event
            assert owner_token == "worker-1"
            events.append(("retry", safe_reason_code))
            return True

    monkeypatch.setattr(module, "AuditEventOutboxService", Outbox)
    secret = "raw-database-secret"

    def fail(db, payload):
        events.append("persist")
        raise RuntimeError(secret)

    result = module.AuditEventOutboxProcessor(
        _ProcessorDb(events),
        persist_audit=fail,
    ).process_due_events(owner_token="worker-1")

    assert result.processed_count == 0
    assert events == [
        "lease",
        "commit",
        "persist",
        "rollback",
        ("retry", "audit.persistence_failed"),
        "commit",
    ]
    assert secret not in events[4][1]


def test_processor_retries_while_workflow_run_correlation_is_pending(monkeypatch):
    module = _module()
    event = _leased_event(attempt_count=1)
    workflow_run_id = uuid4()
    event.payload["workflow_run_id"] = str(workflow_run_id)
    events = []
    db = _ProcessorCorrelationDb({}, events)

    class Outbox:
        def __init__(self, processor_db):
            assert processor_db is db

        def recover_stale_leases(self):
            return 0

        def lease_due_events(self, *, owner_token, limit):
            return [event]

        def mark_succeeded(self, *args, **kwargs):
            raise AssertionError("pending correlation must not succeed")

        def mark_retry_or_dead_letter(
            self, row, *, owner_token, safe_reason_code
        ):
            assert row is event
            assert owner_token == "worker-1"
            events.append(("retry", safe_reason_code))
            return True

    monkeypatch.setattr(module, "AuditEventOutboxService", Outbox)

    result = module.AuditEventOutboxProcessor(db).process_due_events(
        owner_token="worker-1"
    )

    assert result.processed_count == 0
    assert db.added == []
    assert events == [
        "commit",
        "rollback",
        ("retry", "audit.workflow_run_pending"),
        "commit",
    ]


def test_processor_final_attempt_persists_audit_without_missing_run_correlation(
    monkeypatch,
):
    module = _module()
    event = _leased_event(attempt_count=5)
    workflow_run_id = uuid4()
    event.payload["workflow_run_id"] = str(workflow_run_id)
    events = []
    db = _ProcessorCorrelationDb({}, events)

    class Outbox:
        def __init__(self, processor_db):
            assert processor_db is db

        def recover_stale_leases(self):
            return 0

        def lease_due_events(self, *, owner_token, limit):
            return [event]

        def mark_succeeded(self, row, *, owner_token):
            assert row is event
            assert owner_token == "worker-1"
            events.append("succeeded")
            return True

        def mark_retry_or_dead_letter(self, *args, **kwargs):
            raise AssertionError("final pending correlation must preserve the audit")

    monkeypatch.setattr(module, "AuditEventOutboxService", Outbox)

    result = module.AuditEventOutboxProcessor(db).process_due_events(
        owner_token="worker-1"
    )

    assert result.processed_count == 1
    assert len(db.added) == 1
    assert db.added[0].workflow_run_id is None
    assert events == ["commit", "succeeded", "commit"]


def test_processor_treats_concurrent_legacy_insert_as_idempotent_success(
    monkeypatch,
):
    module = _module()
    event = _leased_event()
    events = []

    class Db(_ProcessorDb):
        def get(self, model, identity):
            assert model is AuditLog
            assert identity == uuid4_value
            return SimpleNamespace(id=identity)

    class Outbox:
        def __init__(self, db):
            pass

        def recover_stale_leases(self):
            return 0

        def lease_due_events(self, *, owner_token, limit):
            return [event]

        def mark_succeeded(self, row, *, owner_token):
            assert owner_token == "worker-1"
            events.append("succeeded")
            return True

    uuid4_value = module.audit_id_from_payload(event.payload)
    monkeypatch.setattr(module, "AuditEventOutboxService", Outbox)

    def lose_insert_race(db, payload):
        events.append("persist")
        raise IntegrityError("insert", {}, RuntimeError("duplicate"))

    result = module.AuditEventOutboxProcessor(
        Db(events),
        persist_audit=lose_insert_race,
        after_commit=lambda persisted_id: events.append(("dispatch", persisted_id)),
    ).process_due_events(owner_token="worker-1")

    assert result.processed_count == 1
    assert events == [
        "commit",
        "persist",
        "rollback",
        "succeeded",
        "commit",
        ("dispatch", uuid4_value),
    ]


def test_after_commit_failure_does_not_undo_persisted_audit(monkeypatch):
    module = _module()
    event = _leased_event()
    events = []

    class Outbox:
        def __init__(self, db):
            pass

        def recover_stale_leases(self):
            return 0

        def lease_due_events(self, *, owner_token, limit):
            return [event]

        def mark_succeeded(self, row, *, owner_token):
            return True

    monkeypatch.setattr(module, "AuditEventOutboxService", Outbox)

    def fail_dispatch(audit_id):
        events.append("dispatch")
        raise RuntimeError("raw-redis-secret")

    result = module.AuditEventOutboxProcessor(
        _ProcessorDb(events),
        persist_audit=lambda db, payload: uuid4(),
        after_commit=fail_dispatch,
    ).process_due_events(owner_token="worker-1")

    assert result.processed_count == 1
    assert events == ["commit", "commit", "dispatch"]


def test_payload_is_mapped_to_audit_log_with_fixed_id():
    module = _module()
    event = _leased_event()

    audit = module.build_audit_log(event.payload)

    assert str(audit.id) == event.payload["id"]
    assert audit.action == "permission.denied"
    assert audit.actor_type == "user"
    assert audit.category == "action"
    assert audit.status == "failure"
    assert audit.audit_metadata == event.payload["audit_metadata"]


def test_payload_maps_workflow_correlation_from_top_level_and_metadata():
    module = _module()
    event = _leased_event()
    workflow_run_id = uuid4()
    workflow_node_run_id = uuid4()
    event.payload["workflow_run_id"] = str(workflow_run_id)
    event.payload["audit_metadata"]["workflow_node_run_id"] = str(
        workflow_node_run_id
    )

    audit = module.build_audit_log(event.payload)

    assert audit.workflow_run_id == workflow_run_id
    assert audit.workflow_node_run_id == workflow_node_run_id


def test_persistence_drops_orphan_and_mismatched_workflow_correlation():
    module = _module()
    event = _leased_event()
    workflow_run_id = uuid4()
    workflow_id = uuid4()
    organization_id = uuid4()
    other_workflow_run_id = uuid4()
    workflow_node_run_id = uuid4()
    event.payload["audit_metadata"]["organization_id"] = str(organization_id)
    event.payload["workflow_run_id"] = str(workflow_run_id)
    event.payload["workflow_node_run_id"] = str(workflow_node_run_id)

    db = _CorrelationDb(
        {
            (WorkflowRun, workflow_run_id): SimpleNamespace(
                id=workflow_run_id,
                workflow_id=workflow_id,
            ),
            (Workflow, workflow_id): SimpleNamespace(
                id=workflow_id,
                organization_id=organization_id,
            ),
            (WorkflowNodeRun, workflow_node_run_id): SimpleNamespace(
                id=workflow_node_run_id,
                workflow_run_id=other_workflow_run_id,
            ),
        }
    )

    module.persist_audit_payload(db, event.payload)

    assert db.added[0].workflow_run_id == workflow_run_id
    assert db.added[0].workflow_node_run_id is None


def test_persistence_drops_cross_organization_run_and_node_correlation():
    module = _module()
    event = _leased_event()
    audit_organization_id = uuid4()
    workflow_organization_id = uuid4()
    workflow_id = uuid4()
    workflow_run_id = uuid4()
    workflow_node_run_id = uuid4()
    event.payload["audit_metadata"]["organization_id"] = str(
        audit_organization_id
    )
    event.payload["workflow_run_id"] = str(workflow_run_id)
    event.payload["workflow_node_run_id"] = str(workflow_node_run_id)
    db = _CorrelationDb(
        {
            (WorkflowRun, workflow_run_id): SimpleNamespace(
                id=workflow_run_id,
                workflow_id=workflow_id,
            ),
            (Workflow, workflow_id): SimpleNamespace(
                id=workflow_id,
                organization_id=workflow_organization_id,
            ),
            (WorkflowNodeRun, workflow_node_run_id): SimpleNamespace(
                id=workflow_node_run_id,
                workflow_run_id=workflow_run_id,
            ),
        }
    )

    module.persist_audit_payload(db, event.payload)

    assert db.added[0].workflow_run_id is None
    assert db.added[0].workflow_node_run_id is None


def test_persistence_drops_same_organization_run_from_unexpected_workflow():
    module = _module()
    event = _leased_event()
    organization_id = uuid4()
    expected_workflow_id = uuid4()
    actual_workflow_id = uuid4()
    workflow_run_id = uuid4()
    workflow_node_run_id = uuid4()
    event.payload["audit_metadata"].update(
        {
            "organization_id": str(organization_id),
            "workflow_id": str(expected_workflow_id),
        }
    )
    event.payload["workflow_run_id"] = str(workflow_run_id)
    event.payload["workflow_node_run_id"] = str(workflow_node_run_id)
    db = _CorrelationDb(
        {
            (WorkflowRun, workflow_run_id): SimpleNamespace(
                id=workflow_run_id,
                workflow_id=actual_workflow_id,
            ),
            (Workflow, actual_workflow_id): SimpleNamespace(
                id=actual_workflow_id,
                organization_id=organization_id,
            ),
            (WorkflowNodeRun, workflow_node_run_id): SimpleNamespace(
                id=workflow_node_run_id,
                workflow_run_id=workflow_run_id,
            ),
        }
    )

    module.persist_audit_payload(db, event.payload)

    assert db.added[0].workflow_run_id is None
    assert db.added[0].workflow_node_run_id is None


def test_persistence_drops_cross_organization_node_only_correlation():
    module = _module()
    event = _leased_event()
    audit_organization_id = uuid4()
    workflow_organization_id = uuid4()
    workflow_id = uuid4()
    workflow_run_id = uuid4()
    workflow_node_run_id = uuid4()
    event.payload["audit_metadata"]["organization_id"] = str(
        audit_organization_id
    )
    event.payload["workflow_node_run_id"] = str(workflow_node_run_id)
    db = _CorrelationDb(
        {
            (WorkflowNodeRun, workflow_node_run_id): SimpleNamespace(
                id=workflow_node_run_id,
                workflow_run_id=workflow_run_id,
            ),
            (WorkflowRun, workflow_run_id): SimpleNamespace(
                id=workflow_run_id,
                workflow_id=workflow_id,
            ),
            (Workflow, workflow_id): SimpleNamespace(
                id=workflow_id,
                organization_id=workflow_organization_id,
            ),
        }
    )

    module.persist_audit_payload(db, event.payload)

    assert db.added[0].workflow_run_id is None
    assert db.added[0].workflow_node_run_id is None
