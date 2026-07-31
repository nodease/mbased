from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from apps.shared.alembic.schedule_dispatch_downgrade import (
    DESTRUCTIVE_DOWNGRADE_ENV,
    assert_schedule_configuration_quarantine_downgrade_is_safe,
    assert_schedule_dispatch_downgrade_is_safe,
)
from apps.shared.db.models.schedule import Schedule
from apps.shared.db.models.schedule_dispatch import ScheduleDispatchClaim
from apps.shared.db.models.workflow_run import WorkflowRun

EXPECTED_CLAIM_INDEXES = {
    "ix_schedule_dispatch_claims_completed_at",
    "ix_schedule_dispatch_claims_deployment_id",
    "ix_schedule_dispatch_claims_org_status_completed",
    "ix_schedule_dispatch_claims_status_execution_deadline",
    "ix_schedule_dispatch_claims_status_lease_expiry",
    "ix_schedule_dispatch_claims_status_next_attempt",
    "ix_schedule_dispatch_claims_visibility_gap",
}
EXPECTED_CLAIM_CONSTRAINTS = {
    "ck_schedule_dispatch_claims_attempt_count",
    "ck_schedule_dispatch_claims_next_attempt_status",
    "ck_schedule_dispatch_claims_outcome_review",
    "ck_schedule_dispatch_claims_safe_reason",
    "ck_schedule_dispatch_claims_status",
    "ck_schedule_dispatch_claims_status_fields",
    "ck_schedule_dispatch_claims_task_id",
    "ck_schedule_dispatch_claims_timestamp_order",
    "uq_schedule_dispatch_claims_idempotency_key",
    "uq_schedule_dispatch_claims_occurrence",
    "uq_schedule_dispatch_claims_workflow_run_id",
}


def test_claim_model_preserves_durable_references_without_foreign_keys():
    table = ScheduleDispatchClaim.__table__

    assert table.c.schedule_id.nullable is False
    assert table.c.organization_id.nullable is False
    assert table.c.deployment_id.nullable is False
    assert table.c.claimed_at.nullable is False
    assert not table.c.schedule_id.foreign_keys
    assert not table.c.organization_id.foreign_keys
    assert not table.c.deployment_id.foreign_keys
    assert not table.c.workflow_run_id.foreign_keys


def test_claim_model_declares_required_constraints_and_indexes():
    table = ScheduleDispatchClaim.__table__

    constraint_names = {
        constraint.name for constraint in table.constraints if constraint.name
    }
    index_names = {index.name for index in table.indexes}

    assert EXPECTED_CLAIM_CONSTRAINTS <= constraint_names
    assert EXPECTED_CLAIM_INDEXES == index_names

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.name and hasattr(constraint, "sqltext")
    }
    safe_reason = constraints["ck_schedule_dispatch_claims_safe_reason"]
    outcome_review = constraints["ck_schedule_dispatch_claims_outcome_review"]
    assert "status = 'canceled' AND safe_reason_code IS NOT NULL" in safe_reason
    assert "status = 'dead_lettered' AND safe_reason_code IS NOT NULL" in safe_reason
    assert "configuration_preflight_blocked" in safe_reason
    assert "outcome_resolution_code IS NOT NULL" in outcome_review


def test_workflow_run_allows_only_correlated_system_schedule_null_executor():
    table = WorkflowRun.__table__
    constraint = next(
        item
        for item in table.constraints
        if item.name == "ck_workflow_runs_system_schedule_executor"
    )

    assert table.c.user_id.nullable is True
    sql = str(constraint.sqltext)
    assert "user_id IS NOT NULL" in sql
    assert "trigger_mode = 'SCHEDULER'" in sql
    assert "workflow_task_id IS NOT NULL" in sql
    assert "workflow_task_id LIKE 'schedule:%'" in sql


def test_schedule_dispatch_migration_extends_pre_schedule_base_revision():
    migration = importlib.import_module(
        "apps.shared.alembic.versions.fa8b9c0d1e23_add_schedule_dispatch_claims"
    )

    assert migration.revision == "fa8b9c0d1e23"
    assert migration.down_revision == "fa7b8c9d0e12"
    assert (
        "status = 'canceled' AND safe_reason_code IS NOT NULL" in migration._SAFE_REASON
    )
    assert (
        "status = 'dead_lettered' AND safe_reason_code IS NOT NULL"
        in migration._SAFE_REASON
    )
    assert "outcome_resolution_code IS NOT NULL" in migration._OUTCOME_REVIEW
    assert "workflow_task_id IS NOT NULL" in inspect.getsource(migration.upgrade)


class _DowngradeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _DowngradeConnection:
    def __init__(
        self,
        has_null_executor,
        has_blocking_claim=False,
        has_quarantined_schedule=False,
    ):
        self.has_null_executor = has_null_executor
        self.has_blocking_claim = has_blocking_claim
        self.has_quarantined_schedule = has_quarantined_schedule

    def execute(self, statement):
        sql = str(statement)
        if "workflow_runs" in sql:
            return _DowngradeResult(self.has_null_executor)
        if "configuration_error_code" in sql:
            return _DowngradeResult(self.has_quarantined_schedule)
        assert "schedule_dispatch_claims" in sql
        return _DowngradeResult(self.has_blocking_claim)


def test_claim_migration_downgrade_requires_explicit_destructive_opt_in(monkeypatch):
    monkeypatch.delenv(DESTRUCTIVE_DOWNGRADE_ENV, raising=False)
    with pytest.raises(RuntimeError, match="schema downgrade is unsupported"):
        assert_schedule_dispatch_downgrade_is_safe(_DowngradeConnection(False))

    monkeypatch.setenv(DESTRUCTIVE_DOWNGRADE_ENV, "1")
    assert_schedule_dispatch_downgrade_is_safe(_DowngradeConnection(False))

    with pytest.raises(RuntimeError, match="system workflow runs"):
        assert_schedule_dispatch_downgrade_is_safe(_DowngradeConnection(True))

    with pytest.raises(RuntimeError, match="active, admitted, or unreviewed claims"):
        assert_schedule_dispatch_downgrade_is_safe(
            _DowngradeConnection(False, has_blocking_claim=True)
        )


def test_schedule_dispatch_downgrade_guard_blocks_admitted_claim_without_run_row():
    source = inspect.getsource(assert_schedule_dispatch_downgrade_is_safe)

    assert "workflow_run_id IS NOT NULL" in source


def test_quarantine_downgrade_only_blocks_quarantined_schedule_rows():
    assert_schedule_configuration_quarantine_downgrade_is_safe(
        _DowngradeConnection(False)
    )

    with pytest.raises(RuntimeError, match="invalid schedules"):
        assert_schedule_configuration_quarantine_downgrade_is_safe(
            _DowngradeConnection(False, has_quarantined_schedule=True)
        )


def test_schedule_model_declares_invalid_configuration_quarantine_field():
    table = Schedule.__table__

    assert table.c.configuration_error_code.nullable is True
    assert "ix_schedules_configuration_error_code" in {
        index.name for index in table.indexes
    }
    assert "ck_schedules_configuration_error_code" in {
        constraint.name for constraint in table.constraints
    }


def test_schedule_quarantine_migration_extends_claim_migration():
    migration = importlib.import_module(
        "apps.shared.alembic.versions.fb9c0d1e2f34_quarantine_invalid_schedule_configuration"
    )

    assert migration.revision == "fb9c0d1e2f34"
    assert migration.down_revision == "fa8b9c0d1e23"


def test_workflow_run_visibility_migration_extends_quarantine_migration():
    migration = importlib.import_module(
        "apps.shared.alembic.versions.fc0d1e2f3a45_add_schedule_workflow_run_visibility"
    )

    assert migration.revision == "fc0d1e2f3a45"
    assert migration.down_revision == "fb9c0d1e2f34"


def test_schedule_configuration_code_migration_extends_visibility_migration():
    migration = importlib.import_module(
        "apps.shared.alembic.versions.fd1e2f3a4b56_enforce_schedule_configuration_error_codes"
    )

    assert migration.revision == "fd1e2f3a4b56"
    assert migration.down_revision == "fc0d1e2f3a45"


def test_schedule_dispatch_merge_migration_joins_rebased_model_routing_head():
    migration = importlib.import_module(
        "apps.shared.alembic.versions."
        "fe2f3a4b5c67_merge_schedule_dispatch_and_model_routing_heads"
    )

    assert migration.revision == "fe2f3a4b5c67"
    assert set(migration.down_revision) == {"fd1e2f3a4b56", "fb8c9d0e1f23"}


def test_schedule_admission_correlation_migration_extends_merge_head():
    migration = importlib.import_module(
        "apps.shared.alembic.versions."
        "ff3a4b5c6d78_enforce_schedule_admission_correlation"
    )

    assert migration.revision == "ff3a4b5c6d78"
    assert migration.down_revision == "fe2f3a4b5c67"
    assert "workflow_run_id IS NOT NULL" in migration._STATUS_FIELDS
    assert "execution_outcome_unknown" in migration._SAFE_REASON
    assert (
        "status = 'canceled' AND safe_reason_code IS NOT NULL" in migration._SAFE_REASON
    )
    assert (
        "status = 'dead_lettered' AND safe_reason_code IS NOT NULL"
        in migration._SAFE_REASON
    )
    assert "safe_reason_code IS NOT NULL" in migration._LEGACY_SAFE_REASON
    assert "workflow_run_id IS NOT NULL" in str(migration._INVALID_EXISTING_ROWS)
    assert "ck_schedule_dispatch_claims_preadmission_run" in inspect.getsource(
        migration.upgrade
    )
    assert "correlation cleanup is required" in inspect.getsource(migration.upgrade)


def test_schedule_and_knowledge_metadata_heads_are_merged_without_ddl():
    migration = importlib.import_module(
        "apps.shared.alembic.versions."
        "ff4b5c6d7e89_merge_schedule_and_knowledge_metadata_heads"
    )

    assert migration.revision == "ff4b5c6d7e89"
    assert set(migration.down_revision) == {"ff3a4b5c6d78", "fa7c8d9e0f12"}


def test_schedule_null_constraint_hardening_extends_the_single_merge_head():
    migration = importlib.import_module(
        "apps.shared.alembic.versions.ff5c6d7e8f90_harden_schedule_null_constraints"
    )

    assert migration.revision == "ff5c6d7e8f90"
    assert migration.down_revision == "ff4b5c6d7e89"
    assert "workflow_task_id IS NOT NULL" in migration._WORKFLOW_RUN_EXECUTOR
    assert (
        "status = 'canceled' AND safe_reason_code IS NOT NULL" in migration._SAFE_REASON
    )
    assert (
        "status = 'dead_lettered' AND safe_reason_code IS NOT NULL"
        in migration._SAFE_REASON
    )
    assert "outcome_resolution_code IS NOT NULL" in migration._OUTCOME_REVIEW
    assert "assert_schedule_dispatch_downgrade_is_safe" in inspect.getsource(
        migration.downgrade
    )


class _MigrationOperations:
    def __init__(self, calls: list[str], *, connection=None):
        self.calls = calls
        self.connection = connection

    def get_bind(self):
        self.calls.append("get_bind")
        return self.connection if self.connection is not None else object()

    def __getattr__(self, name):
        def operation(*_args, **_kwargs):
            self.calls.append(name)

        return operation


@pytest.mark.parametrize(
    ("allow_destructive", "has_blocking_claim", "expected_error"),
    (
        (False, False, "schema downgrade is unsupported"),
        (True, True, "active, admitted, or unreviewed claims"),
        (True, False, None),
    ),
)
def test_initial_schedule_claim_downgrade_is_fail_closed_before_ddl(
    monkeypatch,
    allow_destructive,
    has_blocking_claim,
    expected_error,
):
    migration = importlib.import_module(
        "apps.shared.alembic.versions.fa8b9c0d1e23_add_schedule_dispatch_claims"
    )
    calls: list[str] = []
    connection = _DowngradeConnection(
        False,
        has_blocking_claim=has_blocking_claim,
    )
    monkeypatch.setattr(
        migration,
        "op",
        _MigrationOperations(calls, connection=connection),
    )
    if allow_destructive:
        monkeypatch.setenv(DESTRUCTIVE_DOWNGRADE_ENV, "1")
    else:
        monkeypatch.delenv(DESTRUCTIVE_DOWNGRADE_ENV, raising=False)

    if expected_error is not None:
        with pytest.raises(RuntimeError, match=expected_error):
            migration.downgrade()
        assert calls == ["get_bind"]
        return

    migration.downgrade()

    assert calls[0] == "get_bind"
    assert calls[1] == "drop_index"
    assert "drop_table" in calls
    assert calls[-1] == "alter_column"


def test_schedule_head_downgrade_guards_noop_graph_move(monkeypatch):
    migration = importlib.import_module(
        "apps.shared.alembic.versions.ff5c6d7e8f90_harden_schedule_null_constraints"
    )
    calls: list[str] = []

    monkeypatch.setattr(migration, "op", _MigrationOperations(calls))
    monkeypatch.setattr(
        migration,
        "assert_schedule_dispatch_downgrade_is_safe",
        lambda _connection: calls.append("assert_schedule_dispatch_downgrade_is_safe"),
    )
    monkeypatch.setattr(
        migration,
        "assert_schedule_configuration_quarantine_downgrade_is_safe",
        lambda _connection: calls.append(
            "assert_schedule_configuration_quarantine_downgrade_is_safe"
        ),
    )

    migration.downgrade()

    assert calls == [
        "get_bind",
        "assert_schedule_dispatch_downgrade_is_safe",
        "get_bind",
        "assert_schedule_configuration_quarantine_downgrade_is_safe",
    ]


class _ConfigurationReasonConnection:
    def __init__(self, blocking_row):
        self.blocking_row = blocking_row

    def execute(self, statement, params=None):
        assert "schedule_dispatch_claims" in str(statement)
        assert params == {"reason": "configuration_preflight_blocked"}
        return _DowngradeResult(self.blocking_row)


def test_configuration_preflight_reason_migration_extends_current_head():
    migration = importlib.import_module(
        "apps.shared.alembic.versions."
        "0f4a5b6c7d89_add_schedule_configuration_preflight_reason"
    )

    assert migration.revision == "0f4a5b6c7d89"
    assert migration.down_revision == "fd3e4f5a6b78"
    assert "configuration_preflight_blocked" in migration._safe_reason_constraint(
        include_configuration_preflight=True
    )
    assert "configuration_preflight_blocked" not in migration._safe_reason_constraint(
        include_configuration_preflight=False
    )


def test_configuration_preflight_reason_descends_from_schedule_constraint_owner():
    root = Path(__file__).resolve().parents[4]
    config = Config(str(root / "apps" / "shared" / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(root / "apps" / "shared" / "alembic"),
    )
    script = ScriptDirectory.from_config(config)

    ancestry = {
        revision.revision
        for revision in script.iterate_revisions("0f4a5b6c7d89", "base")
    }

    assert "fd3e4f5a6b78" in ancestry
    assert "ff5c6d7e8f90" in ancestry
    assert "fa8b9c0d1e23" in ancestry


@pytest.mark.parametrize("blocking_row", (None, 1))
def test_configuration_preflight_reason_downgrade_is_fail_closed_before_ddl(
    monkeypatch,
    blocking_row,
):
    migration = importlib.import_module(
        "apps.shared.alembic.versions."
        "0f4a5b6c7d89_add_schedule_configuration_preflight_reason"
    )
    calls: list[str] = []
    monkeypatch.setattr(
        migration,
        "op",
        _MigrationOperations(
            calls,
            connection=_ConfigurationReasonConnection(blocking_row),
        ),
    )

    if blocking_row is not None:
        with pytest.raises(RuntimeError, match="Cannot downgrade"):
            migration.downgrade()
        assert calls == ["get_bind"]
        return

    migration.downgrade()

    assert calls == ["get_bind", "drop_constraint", "create_check_constraint"]


@pytest.mark.parametrize(
    "module_name",
    (
        "apps.shared.alembic.versions."
        "ff4b5c6d7e89_merge_schedule_and_knowledge_metadata_heads",
        "apps.shared.alembic.versions."
        "fe2f3a4b5c67_merge_schedule_dispatch_and_model_routing_heads",
    ),
)
def test_schedule_merge_downgrade_guards_graph_split(monkeypatch, module_name):
    migration = importlib.import_module(module_name)
    calls: list[str] = []

    monkeypatch.setattr(migration, "op", _MigrationOperations(calls))
    monkeypatch.setattr(
        migration,
        "assert_schedule_dispatch_downgrade_is_safe",
        lambda _connection: calls.append("assert_schedule_dispatch_downgrade_is_safe"),
    )
    monkeypatch.setattr(
        migration,
        "assert_schedule_configuration_quarantine_downgrade_is_safe",
        lambda _connection: calls.append(
            "assert_schedule_configuration_quarantine_downgrade_is_safe"
        ),
    )

    migration.downgrade()

    assert calls == [
        "get_bind",
        "assert_schedule_dispatch_downgrade_is_safe",
        "get_bind",
        "assert_schedule_configuration_quarantine_downgrade_is_safe",
    ]


@pytest.mark.parametrize(
    ("module_name", "expected_guards"),
    (
        (
            "apps.shared.alembic.versions.fa8b9c0d1e23_add_schedule_dispatch_claims",
            ("assert_schedule_dispatch_downgrade_is_safe",),
        ),
        (
            "apps.shared.alembic.versions."
            "fb9c0d1e2f34_quarantine_invalid_schedule_configuration",
            (
                "assert_schedule_dispatch_downgrade_is_safe",
                "assert_schedule_configuration_quarantine_downgrade_is_safe",
            ),
        ),
        (
            "apps.shared.alembic.versions."
            "fc0d1e2f3a45_add_schedule_workflow_run_visibility",
            (
                "assert_schedule_dispatch_downgrade_is_safe",
                "assert_schedule_configuration_quarantine_downgrade_is_safe",
            ),
        ),
        (
            "apps.shared.alembic.versions."
            "fd1e2f3a4b56_enforce_schedule_configuration_error_codes",
            (
                "assert_schedule_dispatch_downgrade_is_safe",
                "assert_schedule_configuration_quarantine_downgrade_is_safe",
            ),
        ),
        (
            "apps.shared.alembic.versions."
            "ff3a4b5c6d78_enforce_schedule_admission_correlation",
            (
                "assert_schedule_dispatch_downgrade_is_safe",
                "assert_schedule_configuration_quarantine_downgrade_is_safe",
            ),
        ),
    ),
)
def test_every_schedule_downgrade_runs_guards_before_schema_mutation(
    monkeypatch, module_name, expected_guards
):
    migration = importlib.import_module(module_name)
    calls: list[str] = []

    monkeypatch.setattr(migration, "op", _MigrationOperations(calls))
    for guard_name in expected_guards:
        monkeypatch.setattr(
            migration,
            guard_name,
            lambda _connection, name=guard_name: calls.append(name),
        )

    migration.downgrade()

    first_mutation = next(
        index
        for index, call in enumerate(calls)
        if call not in {"get_bind", *expected_guards}
    )
    assert calls[:first_mutation] == [
        item for guard in expected_guards for item in ("get_bind", guard)
    ]
