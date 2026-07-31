from types import SimpleNamespace

import pytest

from apps.shared.services import schedule_dispatch_schema_readiness as migration_readiness
from apps.shared.services.schedule_dispatch_schema_readiness import (
    REQUIRED_SCHEDULE_DISPATCH_CHECKS,
    REQUIRED_SCHEDULE_DISPATCH_SCHEMA,
    REQUIRED_SCHEDULE_DISPATCH_UNIQUES,
    ScheduleDispatchMigrationNotReadyError,
    require_schedule_dispatch_migration_ready,
)
from apps.shared.domain.schedule_dispatch import ScheduleDispatchSettings


def test_disabled_mode_does_not_introspect_or_mutate_schema(monkeypatch):
    monkeypatch.setattr(
        migration_readiness,
        "inspect",
        lambda _engine: (_ for _ in ()).throw(AssertionError("must not inspect")),
    )

    require_schedule_dispatch_migration_ready(
        object(),
        settings=ScheduleDispatchSettings(mode="disabled"),
    )


def test_claim_mode_fails_startup_when_migration_is_not_ready(monkeypatch):
    monkeypatch.setattr(migration_readiness, "inspect", lambda _engine: object())
    monkeypatch.setattr(
        migration_readiness,
        "schedule_dispatch_alembic_readiness",
        lambda _inspector: SimpleNamespace(ready=False),
    )

    with pytest.raises(ScheduleDispatchMigrationNotReadyError):
        require_schedule_dispatch_migration_ready(
            object(),
            settings=ScheduleDispatchSettings(mode="claim"),
        )


def test_claim_mode_fails_when_claim_schema_is_missing(monkeypatch):
    class _Inspector:
        def has_table(self, table_name):
            return table_name != "schedule_dispatch_claims"

    monkeypatch.setattr(migration_readiness, "inspect", lambda _engine: _Inspector())
    monkeypatch.setattr(
        migration_readiness,
        "schedule_dispatch_alembic_readiness",
        lambda _inspector: SimpleNamespace(ready=True),
    )

    with pytest.raises(ScheduleDispatchMigrationNotReadyError):
        require_schedule_dispatch_migration_ready(
            object(),
            settings=ScheduleDispatchSettings(mode="claim"),
        )


def test_claim_mode_fails_when_required_column_is_missing(monkeypatch):
    class _Inspector:
        def has_table(self, _table_name):
            return True

        def get_columns(self, table_name):
            columns = {
                "schedule_dispatch_claims": ["id", "schedule_id"],
                "workflow_runs": ["user_id", "trigger_mode", "workflow_task_id"],
            }
            return [{"name": name} for name in columns[table_name]]

    monkeypatch.setattr(migration_readiness, "inspect", lambda _engine: _Inspector())
    monkeypatch.setattr(
        migration_readiness,
        "schedule_dispatch_alembic_readiness",
        lambda _inspector: SimpleNamespace(ready=True),
    )

    with pytest.raises(ScheduleDispatchMigrationNotReadyError):
        require_schedule_dispatch_migration_ready(
            object(),
            settings=ScheduleDispatchSettings(mode="claim"),
        )


def test_claim_mode_fails_closed_when_schema_introspection_raises(monkeypatch):
    class _Inspector:
        def has_table(self, _table_name):
            raise RuntimeError("database details must not escape")

    monkeypatch.setattr(migration_readiness, "inspect", lambda _engine: _Inspector())
    monkeypatch.setattr(
        migration_readiness,
        "schedule_dispatch_alembic_readiness",
        lambda _inspector: SimpleNamespace(ready=True),
    )

    with pytest.raises(ScheduleDispatchMigrationNotReadyError):
        require_schedule_dispatch_migration_ready(
            object(),
            settings=ScheduleDispatchSettings(mode="claim"),
        )


def test_claim_mode_accepts_current_single_head(monkeypatch):
    class _Inspector:
        def has_table(self, _table_name):
            return True

        def get_columns(self, table_name):
            return [
                {"name": name}
                for name in REQUIRED_SCHEDULE_DISPATCH_SCHEMA[table_name]
            ]

        def get_check_constraints(self, _table_name):
            return [{"name": name} for name in REQUIRED_SCHEDULE_DISPATCH_CHECKS]

        def get_unique_constraints(self, _table_name):
            return [{"name": name} for name in REQUIRED_SCHEDULE_DISPATCH_UNIQUES]

    monkeypatch.setattr(migration_readiness, "inspect", lambda _engine: _Inspector())
    monkeypatch.setattr(
        migration_readiness,
        "schedule_dispatch_alembic_readiness",
        lambda _inspector: SimpleNamespace(ready=True),
    )

    require_schedule_dispatch_migration_ready(
        object(),
        settings=ScheduleDispatchSettings(mode="claim"),
    )
