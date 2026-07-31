import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from apps.shared.services.cost_optimizer_retention import (
    CostOptimizerRetentionService,
)


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
        if self.limit_value is None:
            return self.rows
        return self.rows[: self.limit_value]


class FakeDb:
    def __init__(self, rows, *, fail_on_delete=False):
        self.rows = rows
        self.deleted = []
        self.committed = False
        self.rolled_back = False
        self.fail_on_delete = fail_on_delete

    def query(self, _model):
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


def test_cost_optimizer_retention_expires_at_uses_trace_metadata_policy(monkeypatch):
    db = FakeDb([])
    created_at = datetime(2026, 7, 5, tzinfo=timezone.utc)
    app_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    calls = []

    def fake_resolve_retention_policy(db_arg, app_id=None, organization_id=None):
        calls.append(
            {
                "db": db_arg,
                "app_id": app_id,
                "organization_id": organization_id,
            }
        )
        return SimpleNamespace(metadata_retention_days=45)

    monkeypatch.setattr(
        "apps.shared.services.cost_optimizer_retention.TracePolicyService.resolve_retention_policy",
        fake_resolve_retention_policy,
    )

    result = CostOptimizerRetentionService.expires_at(
        db,
        app_id=app_id,
        organization_id=organization_id,
        created_at=created_at,
    )

    assert result.isoformat() == "2026-08-19T00:00:00+00:00"
    assert calls == [
        {
            "db": db,
            "app_id": app_id,
            "organization_id": organization_id,
        }
    ]


def test_cost_optimizer_retention_purge_deletes_expired_experiments():
    rows = [SimpleNamespace(id="experiment-1"), SimpleNamespace(id="experiment-2")]
    db = FakeDb(rows)

    result = CostOptimizerRetentionService.purge(
        db,
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
        limit=10,
    )

    assert db.deleted == rows
    assert db.committed is True
    assert result == {
        "cutoff": "2026-08-01T00:00:00+00:00",
        "would_purge_count": 2,
        "purged_count": 2,
        "failed_count": 0,
        "retryable": False,
        "dry_run": False,
    }


def test_cost_optimizer_retention_purge_dry_run_does_not_delete():
    rows = [SimpleNamespace(id="experiment-1")]
    db = FakeDb(rows)

    result = CostOptimizerRetentionService.purge(
        db,
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
        dry_run=True,
    )

    assert db.deleted == []
    assert db.committed is False
    assert result["would_purge_count"] == 1
    assert result["purged_count"] == 0
    assert result["dry_run"] is True


def test_cost_optimizer_retention_purge_respects_limit():
    rows = [
        SimpleNamespace(id="experiment-1"),
        SimpleNamespace(id="experiment-2"),
        SimpleNamespace(id="experiment-3"),
    ]
    db = FakeDb(rows)

    result = CostOptimizerRetentionService.purge(
        db,
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
        limit=2,
    )

    assert db.deleted == rows[:2]
    assert result["would_purge_count"] == 2
    assert result["purged_count"] == 2


@pytest.mark.parametrize("limit", [0, -1, 5001, "not-a-number", True])
def test_cost_optimizer_retention_purge_rejects_invalid_limit(limit):
    db = FakeDb([])

    with pytest.raises(ValueError):
        CostOptimizerRetentionService.purge(
            db,
            now=datetime(2026, 8, 1, tzinfo=timezone.utc),
            limit=limit,
        )

    assert not hasattr(db, "last_query")


def test_cost_optimizer_retention_purge_rolls_back_on_delete_failure():
    rows = [SimpleNamespace(id="experiment-1")]
    db = FakeDb(rows, fail_on_delete=True)

    with pytest.raises(RuntimeError):
        CostOptimizerRetentionService.purge(
            db,
            now=datetime(2026, 8, 1, tzinfo=timezone.utc),
            limit=10,
        )

    assert db.rolled_back is True
    assert db.committed is False
