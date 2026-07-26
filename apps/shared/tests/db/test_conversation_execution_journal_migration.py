from __future__ import annotations

from unittest.mock import Mock

import pytest
from apps.shared.alembic.versions import (
    b20e1f2a3b45_add_conversation_execution_journal as migration,
)


class _Result:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar(self) -> bool:
        return self._value


class _Bind:
    def __init__(self, *, has_rows: bool) -> None:
        self.has_rows = has_rows
        self.statements: list[str] = []

    def execute(self, statement) -> _Result:
        self.statements.append(str(statement))
        return _Result(self.has_rows)


def test_journal_downgrade_refuses_to_drop_durable_events(monkeypatch) -> None:
    bind = _Bind(has_rows=True)
    drop_table = Mock()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.op, "drop_index", Mock())
    monkeypatch.setattr(migration.op, "drop_table", drop_table)

    with pytest.raises(RuntimeError, match="journal rows must be removed"):
        migration.downgrade()

    assert "LOCK TABLE conversation_workflow_execution_events" in bind.statements[0]
    assert "SELECT EXISTS" in bind.statements[1]
    drop_table.assert_not_called()
