from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from apps.log_system import audit_tasks
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.security_alert import SecurityAlertReconciliationWatermark
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import WorkflowNodeRun, WorkflowRun
from sqlalchemy.exc import IntegrityError


@pytest.fixture(autouse=True)
def _isolate_celery_broker(monkeypatch):
    monkeypatch.setattr(audit_tasks.celery_app, "send_task", lambda *args, **kwargs: None)


class _Session:
    def __init__(
        self,
        *,
        existing=None,
        commit_error=None,
        winner_after_rollback=None,
        events=None,
        workflow=None,
        workflow_run=None,
        workflow_node_run=None,
    ):
        self.existing = existing
        self.commit_error = commit_error
        self.winner_after_rollback = winner_after_rollback
        self.events = events
        self.workflow = workflow
        self.workflow_run = workflow_run
        self.workflow_node_run = workflow_node_run
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def get(self, model, identity):
        if model is AuditLog:
            if self.existing is not None and self.existing.id == identity:
                return self.existing
            return None
        if model is WorkflowRun:
            if self.workflow_run is not None and self.workflow_run.id == identity:
                return self.workflow_run
            return None
        if model is Workflow:
            if self.workflow is not None and self.workflow.id == identity:
                return self.workflow
            return None
        if model is WorkflowNodeRun:
            if (
                self.workflow_node_run is not None
                and self.workflow_node_run.id == identity
            ):
                return self.workflow_node_run
            return None
        raise AssertionError(model)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1
        if self.events is not None:
            self.events.append("commit")
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self):
        self.rollbacks += 1
        if self.winner_after_rollback is not None:
            self.existing = self.winner_after_rollback

    def close(self):
        self.closed += 1


def _data(audit_id):
    return {
        "id": str(audit_id),
        "occurred_at": "2026-07-12T00:00:00+00:00",
        "actor_id": str(uuid4()),
        "actor_type": "user",
        "category": "action",
        "action": "permission.denied",
        "target_type": "workflow",
        "target_id": str(uuid4()),
        "status": "failure",
        "audit_metadata": {"organization_id": str(uuid4())},
    }


def test_record_audit_uses_publisher_supplied_id(monkeypatch):
    session = _Session()
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    audit_id = uuid4()

    result = audit_tasks.record_audit_log.run(_data(audit_id))

    assert result == {
        "status": "success",
        "action": "permission.denied",
        "audit_id": str(audit_id),
    }
    assert session.added[0].id == audit_id
    assert session.commits == 1
    assert session.closed == 1


def test_legacy_record_audit_maps_workflow_correlation(monkeypatch):
    data = _data(uuid4())
    organization_id = data["audit_metadata"]["organization_id"]
    workflow_id = uuid4()
    workflow_run_id = uuid4()
    workflow_node_run_id = uuid4()
    session = _Session(
        workflow=SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
        ),
        workflow_run=SimpleNamespace(
            id=workflow_run_id,
            workflow_id=workflow_id,
        ),
        workflow_node_run=SimpleNamespace(
            id=workflow_node_run_id,
            workflow_run_id=workflow_run_id,
        ),
    )
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    data["workflow_run_id"] = str(workflow_run_id)
    data["audit_metadata"]["workflow_node_run_id"] = str(workflow_node_run_id)

    audit_tasks.record_audit_log.run(data)

    audit = session.added[0]
    assert audit.workflow_run_id == workflow_run_id
    assert audit.workflow_node_run_id == workflow_node_run_id


def test_record_audit_redelivery_is_idempotent(monkeypatch):
    audit_id = uuid4()
    existing = AuditLog(id=audit_id)
    session = _Session(existing=existing)
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)

    result = audit_tasks.record_audit_log.run(_data(audit_id))

    assert result == {
        "status": "duplicate",
        "action": "permission.denied",
        "audit_id": str(audit_id),
    }
    assert session.added == []
    assert session.commits == 0
    assert session.closed == 1


def test_concurrent_duplicate_insert_treats_committed_winner_as_success(monkeypatch):
    audit_id = uuid4()
    winner = AuditLog(id=audit_id)
    session = _Session(
        commit_error=IntegrityError("insert", {}, RuntimeError("duplicate")),
        winner_after_rollback=winner,
    )
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)

    result = audit_tasks.record_audit_log.run(_data(audit_id))

    assert result["status"] == "duplicate"
    assert result["audit_id"] == str(audit_id)
    assert session.commits == 1
    assert session.rollbacks == 1
    assert session.closed == 1


def test_record_audit_dispatches_realtime_security_alert_after_commit(monkeypatch):
    events = []
    audit_id = uuid4()
    session = _Session(events=events)
    dispatched = []
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        audit_tasks.celery_app,
        "send_task",
        lambda name, *, args: (
            events.append("dispatch"),
            dispatched.append((name, args)),
        ),
    )

    audit_tasks.record_audit_log.run(_data(audit_id))

    assert events == ["commit", "dispatch"]
    assert dispatched == [("security_alert.detect", [str(audit_id)])]


def test_record_audit_redelivery_redispatches_realtime_security_alert(monkeypatch):
    audit_id = uuid4()
    session = _Session(existing=AuditLog(id=audit_id))
    dispatched = []
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        audit_tasks.celery_app,
        "send_task",
        lambda name, *, args: dispatched.append((name, args)),
    )

    audit_tasks.record_audit_log.run(_data(audit_id))

    assert dispatched == [("security_alert.detect", [str(audit_id)])]


def test_realtime_security_alert_consumer_task_is_registered():
    assert "security_alert.detect" in audit_tasks.celery_app.tasks


def test_security_alert_detect_returns_missing_for_unknown_audit(monkeypatch):
    audit_id = uuid4()
    session = _Session()
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)

    result = audit_tasks.detect_security_alert.run(str(audit_id))

    assert result == {"status": "missing", "audit_id": str(audit_id)}
    assert session.closed == 1


def test_sal_tc_w001_fifth_denial_is_evaluated_and_aggregated(monkeypatch):
    now = datetime(2026, 7, 12, 0, 10, tzinfo=timezone.utc)
    activation_started_at = now - timedelta(minutes=10)
    actor_id = uuid4()
    organization_id = uuid4()
    audits = [
        AuditLog(
            id=uuid4(),
            occurred_at=now - timedelta(seconds=30 * (4 - index)),
            actor_id=actor_id,
            actor_type="user",
            category="action",
            action="permission.denied",
            target_type="workflow",
            target_id=str(uuid4()),
            status="failure",
            audit_metadata={"organization_id": str(organization_id)},
        )
        for index in range(5)
    ]
    current = audits[-1]
    candidate = SimpleNamespace(
        rule_id="repeated_permission_denied",
        detection_key="threshold-key",
    )
    alert = SimpleNamespace(id=uuid4())
    session = _Session(existing=current)
    evaluated = []
    aggregated = []
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        audit_tasks,
        "_load_security_alert_detection_context",
        lambda db, audit_id: (current, audits, activation_started_at),
        raising=False,
    )
    monkeypatch.setattr(
        audit_tasks,
        "evaluate_security_alert_rules",
        lambda **kwargs: (evaluated.append(kwargs), (candidate,))[1],
        raising=False,
    )
    monkeypatch.setattr(
        audit_tasks,
        "build_security_alert_cooldown_candidates",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        audit_tasks,
        "aggregate_security_alert_detection",
        lambda db, **kwargs: (aggregated.append((db, kwargs)), alert)[1],
        raising=False,
    )

    result = audit_tasks.detect_security_alert.run(str(current.id))

    assert evaluated == [
        {
            "current_event": current,
            "window_events": audits,
            "activation_started_at": activation_started_at,
        }
    ]
    assert aggregated == [
        (
            session,
            {
                "candidate": candidate,
                "audit_logs": audits,
                "detected_at": current.occurred_at,
            },
        )
    ]
    assert result == {
        "status": "processed",
        "audit_id": str(current.id),
        "candidate_count": 1,
    }
    assert session.commits == 1
    assert session.closed == 1


def test_security_alert_detection_enqueues_before_commit_and_dispatches_after(monkeypatch):
    now = datetime(2026, 7, 12, 0, 10, tzinfo=timezone.utc)
    organization_id = uuid4()
    current = AuditLog(
        id=uuid4(),
        occurred_at=now,
        actor_id=uuid4(),
        actor_type="user",
        category="action",
        action="permission.denied",
        target_type="workflow",
        target_id=str(uuid4()),
        status="failure",
        audit_metadata={"organization_id": str(organization_id)},
    )
    candidate = SimpleNamespace(
        rule_id="repeated_permission_denied",
        detection_key="threshold-key",
        organization_id=organization_id,
    )
    alert = SimpleNamespace(id=uuid4(), organization_id=organization_id)
    events = []
    session = _Session(existing=current, events=events)
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        audit_tasks,
        "_load_security_alert_detection_context",
        lambda db, audit_id: (current, [current], now - timedelta(minutes=10)),
    )
    monkeypatch.setattr(
        audit_tasks,
        "evaluate_security_alert_rules",
        lambda **kwargs: (candidate,),
    )
    monkeypatch.setattr(
        audit_tasks,
        "build_security_alert_cooldown_candidates",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        audit_tasks,
        "aggregate_security_alert_detection",
        lambda *args, **kwargs: alert,
    )
    monkeypatch.setattr(
        audit_tasks,
        "enqueue_security_alert_notification",
        lambda db, *, scoped_organization_id, idempotency_key: events.append(
            ("enqueue", scoped_organization_id, idempotency_key)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        audit_tasks,
        "dispatch_security_alert_notification_outbox",
        lambda: events.append("dispatch"),
        raising=False,
    )

    audit_tasks.detect_security_alert.run(str(current.id))

    assert events == [
        (
            "enqueue",
            organization_id,
            f"audit:{current.id}:notifications.changed",
        ),
        "commit",
        "dispatch",
    ]


def test_security_alert_detection_skips_refresh_without_alert_change(monkeypatch):
    now = datetime(2026, 7, 12, 0, 10, tzinfo=timezone.utc)
    organization_id = uuid4()
    current = AuditLog(
        id=uuid4(),
        occurred_at=now,
        actor_id=uuid4(),
        actor_type="user",
        category="action",
        action="permission.denied",
        target_type="workflow",
        target_id=str(uuid4()),
        status="failure",
        audit_metadata={"organization_id": str(organization_id)},
    )
    candidate = SimpleNamespace(
        rule_id="repeated_permission_denied",
        detection_key="below-threshold-key",
        organization_id=organization_id,
    )
    events = []
    session = _Session(existing=current, events=events)
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        audit_tasks,
        "_load_security_alert_detection_context",
        lambda db, audit_id: (current, [current], now - timedelta(minutes=10)),
    )
    monkeypatch.setattr(
        audit_tasks,
        "evaluate_security_alert_rules",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        audit_tasks,
        "build_security_alert_cooldown_candidates",
        lambda **kwargs: (candidate,),
    )
    monkeypatch.setattr(
        audit_tasks,
        "aggregate_security_alert_detection",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        audit_tasks,
        "enqueue_security_alert_notification",
        lambda *args, **kwargs: events.append("enqueue"),
        raising=False,
    )
    monkeypatch.setattr(
        audit_tasks,
        "dispatch_security_alert_notification_outbox",
        lambda: events.append("dispatch"),
        raising=False,
    )

    result = audit_tasks.detect_security_alert.run(str(current.id))

    assert result["candidate_count"] == 0
    assert events == ["commit"]


def test_reconciliation_dispatches_persisted_refresh_after_batch_commit(monkeypatch):
    organization_id = uuid4()
    audit = AuditLog(id=uuid4())
    events = []
    reconcile_kwargs = []
    session = _Session(events=events)
    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)

    def reconcile(repository, **kwargs):
        reconcile_kwargs.append(kwargs)
        repository.process_security_alert_audit(audit)
        repository.commit()
        return SimpleNamespace(processed_count=1)

    monkeypatch.setattr(audit_tasks, "reconcile_security_alert_batch", reconcile)
    def process(db, audit_id, *, changed_organization_ids):
        changed_organization_ids.add(organization_id)
        events.append(("enqueue", organization_id))

    monkeypatch.setattr(audit_tasks, "_process_security_alert_audit", process)
    monkeypatch.setattr(
        audit_tasks,
        "dispatch_security_alert_notification_outbox",
        lambda: events.append("dispatch"),
        raising=False,
    )

    result = audit_tasks.reconcile_security_alerts.run()

    assert result == {"status": "processed", "processed_count": 1}
    assert events == [("enqueue", organization_id), "commit", "dispatch"]
    assert reconcile_kwargs == [
        {
            "processor_name": "security-alert-v1",
            "replay_horizon": audit_tasks.SECURITY_ALERT_MAX_WINDOW,
            "batch_size": 100,
        }
    ]


def test_cooldown_candidate_is_aggregated_without_threshold_candidate(monkeypatch):
    now = datetime(2026, 7, 12, 0, 10, tzinfo=timezone.utc)
    current = AuditLog(
        id=uuid4(),
        occurred_at=now,
        actor_id=uuid4(),
        actor_type="user",
        category="action",
        action="permission.denied",
        target_type="workflow",
        target_id=str(uuid4()),
        status="failure",
        audit_metadata={"organization_id": str(uuid4())},
    )
    cooldown_candidate = SimpleNamespace(
        rule_id="repeated_permission_denied",
        detection_key="cooldown-key",
    )
    aggregated = []
    monkeypatch.setattr(
        audit_tasks,
        "evaluate_security_alert_rules",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        audit_tasks,
        "build_security_alert_cooldown_candidates",
        lambda **kwargs: (cooldown_candidate,),
        raising=False,
    )
    monkeypatch.setattr(
        audit_tasks,
        "aggregate_security_alert_detection",
        lambda db, **kwargs: aggregated.append((db, kwargs)),
    )
    db = object()

    candidate_count = audit_tasks._evaluate_and_aggregate_security_alerts(
        db,
        current_event=current,
        window_events=[current],
        activation_started_at=now - timedelta(minutes=10),
    )

    assert candidate_count == 0
    assert aggregated == [
        (
            db,
            {
                "candidate": cooldown_candidate,
                "audit_logs": [current],
                "detected_at": now,
            },
        )
    ]


def test_detection_context_loads_ten_minute_window_from_activation_watermark():
    now = datetime(2026, 7, 12, 0, 10, tzinfo=timezone.utc)
    activation_started_at = now - timedelta(minutes=7)
    actor_id = uuid4()
    organization_id = uuid4()
    audits = [
        AuditLog(
            id=uuid4(),
            occurred_at=now - timedelta(minutes=4 - index),
            actor_id=actor_id,
            actor_type="user",
            category="action",
            action="permission.denied",
            target_type="workflow",
            target_id=str(uuid4()),
            status="failure",
            audit_metadata={"organization_id": str(organization_id)},
        )
        for index in range(5)
    ]
    current = audits[-1]
    watermark = SecurityAlertReconciliationWatermark(
        processor_name="security-alert-v1",
        activation_started_at=activation_started_at,
    )

    class _WindowQuery:
        def __init__(self):
            self.filters = []
            self.ordering = ()

        def filter(self, *conditions):
            self.filters.extend(conditions)
            return self

        def order_by(self, *columns):
            self.ordering = columns
            return self

        def all(self):
            return audits

    class _WindowSession:
        def __init__(self):
            self.query_value = _WindowQuery()

        def get(self, model, identity):
            if model is AuditLog and identity == current.id:
                return current
            if model is SecurityAlertReconciliationWatermark:
                return watermark
            return None

        def query(self, model):
            assert model is AuditLog
            return self.query_value

    session = _WindowSession()

    loaded = audit_tasks._load_security_alert_detection_context(session, current.id)

    assert loaded == (current, audits, activation_started_at)
    assert session.query_value.filters
    assert session.query_value.ordering == (AuditLog.occurred_at, AuditLog.id)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "success"},
        {"actor_type": "system"},
        {"action": "workflow.executed"},
    ],
)
def test_ineligible_current_event_skips_window_query(overrides):
    now = datetime(2026, 7, 12, 0, 10, tzinfo=timezone.utc)
    organization_id = uuid4()
    values = {
        "actor_type": "user",
        "action": "permission.denied",
        "status": "failure",
        **overrides,
    }
    current = AuditLog(
        id=uuid4(),
        occurred_at=now,
        actor_id=uuid4(),
        actor_type=values["actor_type"],
        category="action",
        action=values["action"],
        target_type="workflow",
        target_id=str(uuid4()),
        status=values["status"],
        audit_metadata={"organization_id": str(organization_id)},
    )
    watermark = SecurityAlertReconciliationWatermark(
        processor_name="security-alert-v1",
        activation_started_at=now - timedelta(minutes=10),
    )

    class _UnexpectedWindowQuery:
        def filter(self, *conditions):
            return self

        def order_by(self, *columns):
            return self

        def all(self):
            return []

    class _EligibilitySession:
        def __init__(self):
            self.window_queries = 0

        def get(self, model, identity):
            if model is AuditLog and identity == current.id:
                return current
            if model is SecurityAlertReconciliationWatermark:
                return watermark
            return None

        def query(self, model):
            assert model is AuditLog
            self.window_queries += 1
            return _UnexpectedWindowQuery()

    session = _EligibilitySession()

    result = audit_tasks._process_security_alert_audit(session, current.id)

    assert result == 0
    assert session.window_queries == 0


def test_security_alert_detection_is_routed_to_log_queue():
    route = audit_tasks.celery_app.amqp.router.route(
        {},
        "security_alert.detect",
        args=[],
        kwargs={},
    )

    assert route["queue"].name == "log"


def test_security_alert_detection_retries_without_exposing_failure_details(
    monkeypatch,
    caplog,
):
    current = AuditLog(
        id=uuid4(),
        occurred_at=datetime(2026, 7, 12, 0, 10, tzinfo=timezone.utc),
    )
    session = _Session(existing=current)
    secret_marker = "secret-marker-detection-123"
    failure = RuntimeError(secret_marker)
    retries = []

    class _RetryRequested(Exception):
        pass

    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        audit_tasks,
        "_load_security_alert_detection_context",
        lambda db, audit_id: (
            current,
            [current],
            current.occurred_at - timedelta(minutes=10),
        ),
    )
    monkeypatch.setattr(
        audit_tasks,
        "evaluate_security_alert_rules",
        lambda **kwargs: (SimpleNamespace(rule_id="repeated_permission_denied"),),
    )
    monkeypatch.setattr(
        audit_tasks,
        "aggregate_security_alert_detection",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )

    def request_retry(**kwargs):
        retries.append(kwargs)
        raise _RetryRequested

    monkeypatch.setattr(audit_tasks.detect_security_alert, "retry", request_retry)

    with pytest.raises(_RetryRequested):
        audit_tasks.detect_security_alert.run(str(current.id))

    assert len(retries) == 1
    assert retries[0]["countdown"] == 1
    assert retries[0]["exc"] is not failure
    assert secret_marker not in str(retries[0]["exc"])
    assert secret_marker not in caplog.text
    assert session.rollbacks == 1
    assert session.closed == 1


def test_security_alert_reconciliation_retries_without_exposing_failure_details(
    monkeypatch,
    caplog,
):
    session = _Session()
    secret_marker = "secret-marker-reconciliation-456"
    failure = RuntimeError(secret_marker)
    retries = []

    class _RetryRequested(Exception):
        pass

    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        audit_tasks,
        "reconcile_security_alert_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )

    def request_retry(**kwargs):
        retries.append(kwargs)
        raise _RetryRequested

    monkeypatch.setattr(
        audit_tasks.reconcile_security_alerts,
        "retry",
        request_retry,
    )

    with pytest.raises(_RetryRequested):
        audit_tasks.reconcile_security_alerts.run()

    assert len(retries) == 1
    assert retries[0]["countdown"] == 1
    assert retries[0]["exc"] is not failure
    assert secret_marker not in str(retries[0]["exc"])
    assert secret_marker not in caplog.text
    assert session.rollbacks == 1
    assert session.closed == 1


def test_security_alert_reconciliation_task_is_registered_on_log_queue():
    task_name = "security_alert.reconcile"

    assert task_name in audit_tasks.celery_app.tasks
    route = audit_tasks.celery_app.amqp.router.route(
        {},
        task_name,
        args=[],
        kwargs={},
    )
    assert route["queue"].name == "log"


def test_security_alert_reconciliation_has_one_minute_beat_schedule():
    entries = [
        entry
        for entry in (audit_tasks.celery_app.conf.beat_schedule or {}).values()
        if entry.get("task") == "security_alert.reconcile"
    ]

    assert entries == [
        {
            "task": "security_alert.reconcile",
            "schedule": 60.0,
            "options": {"queue": "log"},
        }
    ]


def test_security_alert_notification_outbox_task_is_registered_on_log_queue():
    task_name = "security_alert.notification_outbox.deliver"

    assert task_name in audit_tasks.celery_app.tasks
    route = audit_tasks.celery_app.amqp.router.route(
        {},
        task_name,
        args=[],
        kwargs={},
    )
    assert route["queue"].name == "log"


def test_security_alert_notification_outbox_has_recovery_beat_schedule():
    entries = [
        entry
        for entry in (audit_tasks.celery_app.conf.beat_schedule or {}).values()
        if entry.get("task") == "security_alert.notification_outbox.deliver"
    ]

    assert entries == [
        {
            "task": "security_alert.notification_outbox.deliver",
            "schedule": 30.0,
            "options": {"queue": "log"},
        }
    ]


def test_security_alert_notification_outbox_worker_delegates_transaction_control(
    monkeypatch,
):
    session = _Session()
    processed = []

    class Processor:
        def __init__(self, db):
            assert db is session

        def process_due_events(self, *, owner_token, limit):
            processed.append((owner_token, limit))
            return SimpleNamespace(processed_count=2, recovered_count=1)

    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        audit_tasks,
        "SecurityAlertNotificationOutboxProcessor",
        Processor,
        raising=False,
    )

    result = audit_tasks.deliver_security_alert_notification_outbox.run(limit=25)

    assert result == {"processed_count": 2, "recovered_count": 1}
    assert len(processed) == 1
    assert processed[0][1] == 25
    assert processed[0][0]
    assert session.commits == 0
    assert session.rollbacks == 0
    assert session.closed == 1


def test_audit_event_outbox_task_is_registered_on_log_queue():
    task_name = "audit.event_outbox.process"

    assert task_name in audit_tasks.celery_app.tasks
    route = audit_tasks.celery_app.amqp.router.route(
        {},
        task_name,
        args=[],
        kwargs={},
    )
    assert route["queue"].name == "log"


def test_audit_event_outbox_has_recovery_beat_schedule():
    entries = [
        entry
        for entry in (audit_tasks.celery_app.conf.beat_schedule or {}).values()
        if entry.get("task") == "audit.event_outbox.process"
    ]

    assert entries == [
        {
            "task": "audit.event_outbox.process",
            "schedule": 30.0,
            "options": {"queue": "log"},
        }
    ]


def test_audit_event_outbox_worker_delegates_transaction_control(monkeypatch):
    session = _Session()
    processed = []

    class Processor:
        def __init__(self, db, *, after_commit):
            assert db is session
            assert after_commit is audit_tasks._dispatch_security_alert_detection

        def process_due_events(self, *, owner_token, limit):
            processed.append((owner_token, limit))
            return SimpleNamespace(processed_count=3, recovered_count=1)

    monkeypatch.setattr(audit_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        audit_tasks,
        "AuditEventOutboxProcessor",
        Processor,
        raising=False,
    )

    result = audit_tasks.process_audit_event_outbox.run(limit=25)

    assert result == {"processed_count": 3, "recovered_count": 1}
    assert len(processed) == 1
    assert processed[0][1] == 25
    assert processed[0][0]
    assert session.commits == 0
    assert session.rollbacks == 0
    assert session.closed == 1
