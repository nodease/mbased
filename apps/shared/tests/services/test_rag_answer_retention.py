import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from apps.shared.audit.actions import AuditAction
from apps.shared.services import rag_answer_retention as retention_module
from apps.shared.services.rag_answer_retention import RAGAnswerRetentionService


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.limit_value = None
        self.filters = []

    def filter(self, *args, **kwargs):
        self.filters.extend(args)
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        return self.rows[: self.limit_value]


class FakeDb:
    def __init__(self, rows, *, fail_on_delete=False):
        self.rows = rows
        self.deleted = []
        self.committed = False
        self.rolled_back = False
        self.fail_on_delete = fail_on_delete

    def query(self, model):
        self.last_query = FakeQuery(self.rows)
        return self.last_query

    def delete(self, row):
        if self.fail_on_delete:
            raise RuntimeError("delete failed")
        self.deleted.append(row)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_rag_answer_retention_purge_deletes_expired_rows_and_records_aggregate_audit(
    monkeypatch,
):
    rows = [SimpleNamespace(id="run-1"), SimpleNamespace(id="run-2")]
    db = FakeDb(rows)
    audit_calls = []
    monkeypatch.setattr(
        retention_module,
        "record_audit",
        lambda **event: audit_calls.append(event),
    )

    result = RAGAnswerRetentionService.purge(
        db,
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        limit=10,
    )

    assert db.deleted == rows
    assert db.committed is True
    assert result["would_purge_count"] == 2
    assert result["purged_count"] == 2
    assert audit_calls == [
        {
            "action": AuditAction.RAG_ANSWER_PURGE,
            "category": "system",
            "actor_type": "system",
            "target_type": "rag_answer_runs",
            "target_id": None,
            "status": "success",
            "metadata": {
                "cutoff": "2026-07-01T00:00:00+00:00",
                "purged_count": 2,
                "failed_count": 0,
                "retryable": False,
                "status": "success",
            },
        }
    ]


def test_rag_answer_retention_dry_run_does_not_delete_or_record_purge_audit(
    monkeypatch,
):
    rows = [SimpleNamespace(id="run-1"), SimpleNamespace(id="run-2")]
    db = FakeDb(rows)
    audit_calls = []
    monkeypatch.setattr(
        retention_module,
        "record_audit",
        lambda **event: audit_calls.append(event),
    )

    result = RAGAnswerRetentionService.purge(
        db,
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        limit=10,
        dry_run=True,
    )

    assert db.deleted == []
    assert db.committed is False
    assert result["would_purge_count"] == 2
    assert result["purged_count"] == 0
    assert result["dry_run"] is True
    assert audit_calls == []


def test_rag_answer_retention_purge_respects_limit(monkeypatch):
    rows = [
        SimpleNamespace(id="run-1"),
        SimpleNamespace(id="run-2"),
        SimpleNamespace(id="run-3"),
    ]
    db = FakeDb(rows)
    monkeypatch.setattr(retention_module, "record_audit", lambda **event: None)

    result = RAGAnswerRetentionService.purge(
        db,
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        limit=2,
    )

    assert db.deleted == rows[:2]
    assert result["would_purge_count"] == 2
    assert result["purged_count"] == 2


@pytest.mark.parametrize("limit", [0, -1, 5001, "not-a-number", True])
def test_rag_answer_retention_purge_rejects_invalid_limit(limit):
    db = FakeDb([])

    with pytest.raises(ValueError):
        RAGAnswerRetentionService.purge(
            db,
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
            limit=limit,
        )

    assert not hasattr(db, "last_query")


def test_rag_answer_retention_purge_records_organization_scoped_audit_metadata(
    monkeypatch,
):
    organization_id = uuid.uuid4()
    rows = [SimpleNamespace(id="run-1")]
    db = FakeDb(rows)
    audit_calls = []
    monkeypatch.setattr(
        retention_module,
        "record_audit",
        lambda **event: audit_calls.append(event),
    )

    result = RAGAnswerRetentionService.purge(
        db,
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        organization_id=organization_id,
        limit=10,
    )

    assert len(db.last_query.filters) == 2
    assert result["purged_count"] == 1
    assert audit_calls[0]["metadata"]["organization_id"] == str(organization_id)
    assert audit_calls[0]["metadata"]["purged_count"] == 1


def test_rag_answer_retention_purge_zero_rows_records_success_audit(monkeypatch):
    db = FakeDb([])
    audit_calls = []
    monkeypatch.setattr(
        retention_module,
        "record_audit",
        lambda **event: audit_calls.append(event),
    )

    result = RAGAnswerRetentionService.purge(
        db,
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        limit=10,
    )

    assert db.deleted == []
    assert db.committed is True
    assert result["would_purge_count"] == 0
    assert result["purged_count"] == 0
    assert audit_calls[0]["status"] == "success"
    assert audit_calls[0]["metadata"]["purged_count"] == 0
    assert audit_calls[0]["metadata"]["failed_count"] == 0


def test_rag_answer_retention_purge_rolls_back_and_audits_failure(monkeypatch):
    rows = [SimpleNamespace(id="run-1"), SimpleNamespace(id="run-2")]
    db = FakeDb(rows, fail_on_delete=True)
    audit_calls = []
    monkeypatch.setattr(
        retention_module,
        "record_audit",
        lambda **event: audit_calls.append(event),
    )

    try:
        RAGAnswerRetentionService.purge(
            db,
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
            limit=10,
        )
    except RuntimeError as exc:
        assert str(exc) == "delete failed"
    else:
        raise AssertionError("purge should re-raise delete failures")

    assert db.rolled_back is True
    assert db.committed is False
    assert db.deleted == []
    assert audit_calls == [
        {
            "action": AuditAction.RAG_ANSWER_PURGE,
            "category": "system",
            "actor_type": "system",
            "target_type": "rag_answer_runs",
            "target_id": None,
            "status": "failure",
            "metadata": {
                "cutoff": "2026-07-01T00:00:00+00:00",
                "purged_count": 0,
                "failed_count": 2,
                "retryable": True,
                "status": "failure",
            },
        }
    ]
