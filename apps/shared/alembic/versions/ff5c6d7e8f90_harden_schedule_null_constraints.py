"""Harden nullable schedule correlation and terminal-state constraints.

Revision ID: ff5c6d7e8f90
Revises: ff4b5c6d7e89
"""

from typing import Sequence, Union

from apps.shared.alembic.schedule_dispatch_downgrade import (
    assert_schedule_configuration_quarantine_downgrade_is_safe,
    assert_schedule_dispatch_downgrade_is_safe,
)

from alembic import op

revision: str = "ff5c6d7e8f90"
down_revision: Union[str, Sequence[str], None] = "ff4b5c6d7e89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WORKFLOW_RUN_EXECUTOR = (
    "user_id IS NOT NULL OR "
    "(trigger_mode = 'SCHEDULER' AND workflow_task_id IS NOT NULL "
    "AND workflow_task_id LIKE 'schedule:%')"
)

_SAFE_REASON = " OR ".join(
    (
        "(status = 'pending' AND (safe_reason_code IS NULL OR safe_reason_code IN "
        "('broker_enqueue_failed', 'budget_evaluation_failed')))",
        "(status = 'canceled' AND safe_reason_code IS NOT NULL "
        "AND safe_reason_code IN "
        "('app_not_found', 'budget_blocked', 'deployment_inactive', "
        "'deployment_not_current', 'deployment_not_found', "
        "'deployment_type_not_allowed', 'organization_scope_mismatch', "
        "'organization_scope_missing', 'schedule_deployment_mismatch', "
        "'schedule_not_found'))",
        "(status = 'dead_lettered' AND safe_reason_code IS NOT NULL "
        "AND ((safe_reason_code IN "
        "('budget_evaluation_failed', 'enqueue_attempts_exhausted') "
        "AND workflow_run_id IS NULL AND started_at IS NULL) OR "
        "(safe_reason_code IN "
        "('execution_failed_after_admission', 'execution_outcome_unknown') "
        "AND workflow_run_id IS NOT NULL AND celery_task_id IS NOT NULL "
        "AND enqueued_at IS NOT NULL AND started_at IS NOT NULL)))",
        "(status IN ('dispatching', 'enqueued', 'running', 'succeeded') "
        "AND safe_reason_code IS NULL)",
    )
)

_OUTCOME_REVIEW = (
    "(outcome_reviewed_at IS NULL AND outcome_review_audit_id IS NULL "
    "AND outcome_resolution_code IS NULL) OR "
    "(status = 'dead_lettered' AND safe_reason_code = 'execution_outcome_unknown' "
    "AND outcome_reviewed_at IS NOT NULL AND outcome_review_audit_id IS NOT NULL "
    "AND outcome_resolution_code IS NOT NULL "
    "AND outcome_resolution_code IN ('accepted_unknown_no_replay', "
    "'confirmed_completed', 'confirmed_failed_no_replay'))"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_workflow_runs_system_schedule_executor",
        "workflow_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workflow_runs_system_schedule_executor",
        "workflow_runs",
        _WORKFLOW_RUN_EXECUTOR,
    )
    op.drop_constraint(
        "ck_schedule_dispatch_claims_safe_reason",
        "schedule_dispatch_claims",
        type_="check",
    )
    op.create_check_constraint(
        "ck_schedule_dispatch_claims_safe_reason",
        "schedule_dispatch_claims",
        _SAFE_REASON,
    )
    op.drop_constraint(
        "ck_schedule_dispatch_claims_outcome_review",
        "schedule_dispatch_claims",
        type_="check",
    )
    op.create_check_constraint(
        "ck_schedule_dispatch_claims_outcome_review",
        "schedule_dispatch_claims",
        _OUTCOME_REVIEW,
    )


def downgrade() -> None:
    """Keep the strict definitions while guarding the Alembic graph move."""
    assert_schedule_dispatch_downgrade_is_safe(op.get_bind())
    assert_schedule_configuration_quarantine_downgrade_is_safe(op.get_bind())
