import logging
from uuid import UUID

from apps.shared.audit import logger as audit_logger
from apps.shared.db.models.audit_log import AuditEventOutbox


class _FakeSession:
    def __init__(
        self,
        *,
        add_error: Exception | None = None,
        commit_error: Exception | None = None,
    ):
        self.rows = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.add_error = add_error
        self.commit_error = commit_error

    def add(self, row):
        if self.add_error is not None:
            raise self.add_error
        self.rows.append(row)

    def commit(self):
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


def test_audit_id_is_stored_in_outbox_without_celery_publish(monkeypatch):
    db = _FakeSession()
    monkeypatch.setattr(audit_logger, "SessionLocal", lambda: db)

    audit_id = audit_logger.record_audit("schedule.execute", "action")

    assert isinstance(audit_id, UUID)
    assert db.commits == 1
    assert db.rollbacks == 0
    assert db.closes == 1
    assert len(db.rows) == 1
    outbox = db.rows[0]
    assert isinstance(outbox, AuditEventOutbox)
    assert outbox.status == "pending"
    assert outbox.idempotency_key == str(audit_id)
    assert outbox.payload["id"] == str(audit_id)
    assert not hasattr(audit_logger, "celery_app")


def test_caller_session_adds_outbox_without_committing(monkeypatch):
    db = _FakeSession()

    audit_id = audit_logger.record_audit(
        "schedule.execute",
        "action",
        db_session=db,
    )

    assert isinstance(audit_id, UUID)
    assert len(db.rows) == 1
    assert db.rows[0].idempotency_key == str(audit_id)
    assert db.commits == 0
    assert db.rollbacks == 0
    assert db.closes == 0
    assert not hasattr(audit_logger, "celery_app")


def test_audit_outbox_lifts_workflow_correlation_from_metadata():
    db = _FakeSession()
    workflow_run_id = UUID("10000000-0000-0000-0000-000000000001")
    workflow_node_run_id = UUID("20000000-0000-0000-0000-000000000001")

    audit_logger.record_audit(
        "workflow.execute",
        "action",
        metadata={
            "workflow_run_id": str(workflow_run_id),
            "workflow_node_run_id": str(workflow_node_run_id),
        },
        db_session=db,
    )

    payload = db.rows[0].payload
    assert payload["workflow_run_id"] == str(workflow_run_id)
    assert payload["workflow_node_run_id"] == str(workflow_node_run_id)


def test_audit_logger_does_not_import_celery_app(
    monkeypatch,
    caplog,
):
    db = _FakeSession()
    monkeypatch.setattr(audit_logger, "SessionLocal", lambda: db)

    with caplog.at_level(logging.ERROR):
        audit_id = audit_logger.record_audit("schedule.execute", "action")

    assert isinstance(audit_id, UUID)
    assert db.commits == 1
    assert not hasattr(audit_logger, "celery_app")
    assert caplog.text == ""


def test_outbox_failure_returns_none_without_legacy_publish(
    monkeypatch,
    caplog,
):
    raw_detail = "private database endpoint must not escape"
    db = _FakeSession(commit_error=RuntimeError(raw_detail))
    monkeypatch.setattr(audit_logger, "SessionLocal", lambda: db)

    with caplog.at_level(logging.ERROR):
        audit_id = audit_logger.record_audit("schedule.execute", "action")

    assert audit_id is None
    assert db.commits == 1
    assert db.rollbacks == 1
    assert db.closes == 1
    assert not hasattr(audit_logger, "celery_app")
    assert raw_detail not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_caller_session_enqueue_failure_does_not_rollback_business_session(
    monkeypatch,
    caplog,
):
    raw_detail = "private caller transaction detail"
    db = _FakeSession(add_error=RuntimeError(raw_detail))

    with caplog.at_level(logging.ERROR):
        audit_id = audit_logger.record_audit(
            "schedule.execute",
            "action",
            db_session=db,
        )

    assert audit_id is None
    assert db.rows == []
    assert db.commits == 0
    assert db.rollbacks == 0
    assert db.closes == 0
    assert not hasattr(audit_logger, "celery_app")
    assert raw_detail not in caplog.text


def test_audit_returns_none_when_outbox_persistence_fails(
    monkeypatch,
    caplog,
):
    db = _FakeSession(commit_error=RuntimeError("private database detail"))
    monkeypatch.setattr(audit_logger, "SessionLocal", lambda: db)

    with caplog.at_level(logging.ERROR):
        audit_id = audit_logger.record_audit("schedule.execute", "action")

    assert audit_id is None
    assert "private database detail" not in caplog.text
