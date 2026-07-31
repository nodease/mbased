"""Fail-closed guards shared by schedule-dispatch downgrade revisions."""

from __future__ import annotations

import os

import sqlalchemy as sa

DESTRUCTIVE_DOWNGRADE_ENV = "NODEASE_ALLOW_DESTRUCTIVE_SCHEMA_DOWNGRADE"


def assert_schedule_dispatch_downgrade_is_safe(connection) -> None:
    """Protect execution provenance and nonterminal claim evidence."""
    if os.getenv(DESTRUCTIVE_DOWNGRADE_ENV) != "1":
        raise RuntimeError(
            "schedule dispatch schema downgrade is unsupported; "
            "use application rollback instead"
        )
    has_null_executor = connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM workflow_runs WHERE user_id IS NULL)")
    ).scalar()
    if has_null_executor:
        raise RuntimeError(
            "cannot downgrade schedule dispatch while system workflow runs exist"
        )

    has_blocking_claim = connection.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM schedule_dispatch_claims "
            "WHERE status IN ('pending', 'dispatching', 'enqueued', 'running') "
            "OR workflow_run_id IS NOT NULL "
            "OR (status = 'dead_lettered' "
            "AND safe_reason_code = 'execution_outcome_unknown' "
            "AND outcome_reviewed_at IS NULL)"
            ")"
        )
    ).scalar()
    if has_blocking_claim:
        raise RuntimeError(
            "cannot downgrade schedule dispatch while active, admitted, or unreviewed claims exist"
        )


def assert_schedule_configuration_quarantine_downgrade_is_safe(connection) -> None:
    """Do not discard a durable invalid-configuration quarantine state."""
    has_quarantined_schedule = connection.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM schedules "
            "WHERE configuration_error_code IS NOT NULL"
            ")"
        )
    ).scalar()
    if has_quarantined_schedule:
        raise RuntimeError(
            "cannot downgrade schedule configuration quarantine while invalid schedules exist"
        )
