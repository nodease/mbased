from __future__ import annotations

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from apps.shared.domain.app_auth_secret import app_auth_secret_verifier

MIGRATION_MODULE = (
    "apps.shared.alembic.versions.b0c1d2e3f4a5_add_app_auth_secret_verifiers"
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Bind:
    def __init__(self, rows):
        self.rows = rows
        self.update_batches = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if sql.startswith("SELECT"):
            last_id = (parameters or {}).get("last_id")
            remaining = [
                row for row in self.rows if last_id is None or row.id > last_id
            ]
            return _Result(remaining[: (parameters or {})["batch_size"]])
        if sql.startswith("UPDATE"):
            self.update_batches.append(parameters)
            return _Result([])
        raise AssertionError(f"Unexpected SQL in migration test: {sql}")


def _legacy_row(index: int, secret: str = "legacy-test-credential"):
    return SimpleNamespace(
        id=UUID(int=index + 1),
        auth_secret=secret,
        rotated_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )


def test_app_auth_secret_migration_extends_current_head():
    migration = importlib.import_module(MIGRATION_MODULE)

    assert migration.down_revision == "ac2d3e4f5061"


def test_legacy_backfill_uses_bounded_keyset_batches(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    bind = _Bind([_legacy_row(index) for index in range(501)])
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    migration._backfill_legacy_secrets()

    assert [len(batch) for batch in bind.update_batches] == [500, 1]
    assert bind.update_batches[0][0]["generation"] == 1
    assert bind.update_batches[0][0]["verifier"] == app_auth_secret_verifier(
        "legacy-test-credential"
    )


def test_legacy_backfill_rejects_non_ascii_secret_before_update(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    bind = _Bind([_legacy_row(0, secret="비 ASCII")])
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    with pytest.raises(RuntimeError, match="non-ASCII"):
        migration._backfill_legacy_secrets()

    assert bind.update_batches == []


def test_expand_constraint_allows_old_gateway_raw_only_writes(monkeypatch):
    migration = importlib.import_module(MIGRATION_MODULE)
    bind = _Bind([])
    constraints: dict[str, str] = {}
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.op, "add_column", MagicMock())
    monkeypatch.setattr(migration.op, "alter_column", MagicMock())
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda name, table, condition: constraints.setdefault(name, condition),
    )

    migration.upgrade()

    current_state = constraints["ck_apps_auth_secret_current_state"]
    assert (
        "auth_secret_generation = 0 AND auth_secret_verifier IS NULL" in current_state
    )
    assert "auth_secret_generation = 0 AND auth_secret IS NULL" not in current_state
