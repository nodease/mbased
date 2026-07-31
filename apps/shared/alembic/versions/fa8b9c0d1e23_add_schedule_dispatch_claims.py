"""Add durable schedule dispatch claims

Revision ID: fa8b9c0d1e23
Revises: fa7b8c9d0e12
Create Date: 2026-07-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from apps.shared.alembic.schedule_dispatch_downgrade import (
    assert_schedule_dispatch_downgrade_is_safe,
)
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fa8b9c0d1e23"
down_revision: Union[str, Sequence[str], None] = "fa7b8c9d0e12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_STATUS_FIELDS = " OR ".join(
    (
        "(status = 'pending' AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND execution_deadline_at IS NULL AND started_at IS NULL AND completed_at IS NULL)",
        "(status = 'dispatching' AND lease_owner IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND celery_task_id IS NOT NULL "
        "AND execution_deadline_at IS NULL AND started_at IS NULL "
        "AND completed_at IS NULL)",
        "(status = 'enqueued' AND celery_task_id IS NOT NULL "
        "AND enqueued_at IS NOT NULL AND lease_expires_at IS NOT NULL "
        "AND lease_owner IS NULL AND execution_deadline_at IS NULL "
        "AND started_at IS NULL AND completed_at IS NULL)",
        "(status = 'running' AND lease_owner IS NOT NULL "
        "AND celery_task_id IS NOT NULL AND workflow_run_id IS NOT NULL "
        "AND enqueued_at IS NOT NULL AND started_at IS NOT NULL "
        "AND execution_deadline_at IS NOT NULL AND lease_expires_at IS NULL "
        "AND completed_at IS NULL)",
        "(status = 'succeeded' AND completed_at IS NOT NULL AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL AND execution_deadline_at IS NULL "
        "AND workflow_run_id IS NOT NULL AND enqueued_at IS NOT NULL "
        "AND started_at IS NOT NULL)",
        "(status = 'canceled' AND completed_at IS NOT NULL AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL AND execution_deadline_at IS NULL "
        "AND workflow_run_id IS NULL AND started_at IS NULL)",
        "(status = 'dead_lettered' AND completed_at IS NOT NULL "
        "AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND execution_deadline_at IS NULL AND (started_at IS NULL OR "
        "(workflow_run_id IS NOT NULL AND enqueued_at IS NOT NULL)))",
    )
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
        "AND safe_reason_code IN "
        "('budget_evaluation_failed', 'enqueue_attempts_exhausted', "
        "'execution_failed_after_admission', 'execution_outcome_unknown'))",
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
    op.alter_column(
        "workflow_runs",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_workflow_runs_system_schedule_executor",
        "workflow_runs",
        "user_id IS NOT NULL OR "
        "(trigger_mode = 'SCHEDULER' AND workflow_task_id IS NOT NULL "
        "AND workflow_task_id LIKE 'schedule:%')",
    )

    op.create_table(
        "schedule_dispatch_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "execution_deadline_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("safe_reason_code", sa.String(length=64), nullable=True),
        sa.Column("outcome_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome_review_audit_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("outcome_resolution_code", sa.String(length=64), nullable=True),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('canceled', 'dead_lettered', 'dispatching', 'enqueued', "
            "'pending', 'running', 'succeeded')",
            name="ck_schedule_dispatch_claims_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_schedule_dispatch_claims_attempt_count",
        ),
        sa.CheckConstraint(
            "celery_task_id IS NULL OR celery_task_id = idempotency_key",
            name="ck_schedule_dispatch_claims_task_id",
        ),
        sa.CheckConstraint(
            "next_attempt_at IS NULL OR status = 'pending'",
            name="ck_schedule_dispatch_claims_next_attempt_status",
        ),
        sa.CheckConstraint(
            _STATUS_FIELDS,
            name="ck_schedule_dispatch_claims_status_fields",
        ),
        sa.CheckConstraint(
            _SAFE_REASON,
            name="ck_schedule_dispatch_claims_safe_reason",
        ),
        sa.CheckConstraint(
            _OUTCOME_REVIEW,
            name="ck_schedule_dispatch_claims_outcome_review",
        ),
        sa.CheckConstraint(
            "(enqueued_at IS NULL OR claimed_at <= enqueued_at) AND "
            "(started_at IS NULL OR "
            "(enqueued_at IS NOT NULL AND enqueued_at <= started_at)) AND "
            "(completed_at IS NULL OR started_at IS NULL OR started_at <= completed_at)",
            name="ck_schedule_dispatch_claims_timestamp_order",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schedule_id",
            "scheduled_for",
            name="uq_schedule_dispatch_claims_occurrence",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_schedule_dispatch_claims_idempotency_key",
        ),
        sa.UniqueConstraint(
            "workflow_run_id",
            name="uq_schedule_dispatch_claims_workflow_run_id",
        ),
    )
    op.create_index(
        "ix_schedule_dispatch_claims_status_next_attempt",
        "schedule_dispatch_claims",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_schedule_dispatch_claims_status_lease_expiry",
        "schedule_dispatch_claims",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_schedule_dispatch_claims_status_execution_deadline",
        "schedule_dispatch_claims",
        ["status", "execution_deadline_at"],
    )
    op.create_index(
        "ix_schedule_dispatch_claims_deployment_id",
        "schedule_dispatch_claims",
        ["deployment_id"],
    )
    op.create_index(
        "ix_schedule_dispatch_claims_completed_at",
        "schedule_dispatch_claims",
        ["completed_at"],
    )
    op.create_index(
        "ix_schedule_dispatch_claims_org_status_completed",
        "schedule_dispatch_claims",
        ["organization_id", "status", "completed_at"],
    )


def downgrade() -> None:
    assert_schedule_dispatch_downgrade_is_safe(op.get_bind())

    op.drop_index(
        "ix_schedule_dispatch_claims_org_status_completed",
        table_name="schedule_dispatch_claims",
    )
    op.drop_index(
        "ix_schedule_dispatch_claims_completed_at",
        table_name="schedule_dispatch_claims",
    )
    op.drop_index(
        "ix_schedule_dispatch_claims_deployment_id",
        table_name="schedule_dispatch_claims",
    )
    op.drop_index(
        "ix_schedule_dispatch_claims_status_execution_deadline",
        table_name="schedule_dispatch_claims",
    )
    op.drop_index(
        "ix_schedule_dispatch_claims_status_lease_expiry",
        table_name="schedule_dispatch_claims",
    )
    op.drop_index(
        "ix_schedule_dispatch_claims_status_next_attempt",
        table_name="schedule_dispatch_claims",
    )
    op.drop_table("schedule_dispatch_claims")

    op.drop_constraint(
        "ck_workflow_runs_system_schedule_executor",
        "workflow_runs",
        type_="check",
    )
    op.alter_column(
        "workflow_runs",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
