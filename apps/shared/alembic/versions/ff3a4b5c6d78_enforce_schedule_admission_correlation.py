"""Enforce schedule claim admission correlation invariants.

Revision ID: ff3a4b5c6d78
Revises: fe2f3a4b5c67
"""

from typing import Sequence, Union

import sqlalchemy as sa
from apps.shared.alembic.schedule_dispatch_downgrade import (
    assert_schedule_configuration_quarantine_downgrade_is_safe,
    assert_schedule_dispatch_downgrade_is_safe,
)

from alembic import op

revision: str = "ff3a4b5c6d78"
down_revision: Union[str, Sequence[str], None] = "fe2f3a4b5c67"
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
        "AND execution_deadline_at IS NULL AND "
        "((workflow_run_id IS NULL AND started_at IS NULL) OR "
        "(workflow_run_id IS NOT NULL AND celery_task_id IS NOT NULL "
        "AND enqueued_at IS NOT NULL AND started_at IS NOT NULL)))",
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

_LEGACY_STATUS_FIELDS = _STATUS_FIELDS.replace(
    "((workflow_run_id IS NULL AND started_at IS NULL) OR "
    "(workflow_run_id IS NOT NULL AND celery_task_id IS NOT NULL "
    "AND enqueued_at IS NOT NULL AND started_at IS NOT NULL))",
    "(started_at IS NULL OR "
    "(workflow_run_id IS NOT NULL AND enqueued_at IS NOT NULL))",
)

_LEGACY_SAFE_REASON = " OR ".join(
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

_INVALID_EXISTING_ROWS = sa.text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM schedule_dispatch_claims
        WHERE
            (status IN ('pending', 'dispatching', 'enqueued')
                AND workflow_run_id IS NOT NULL)
            OR
            (workflow_run_id IS NOT NULL AND (
                celery_task_id IS NULL OR enqueued_at IS NULL OR started_at IS NULL
            ))
            OR (
                safe_reason_code IN (
                    'execution_failed_after_admission', 'execution_outcome_unknown'
                )
                AND (
                    workflow_run_id IS NULL OR celery_task_id IS NULL
                    OR enqueued_at IS NULL OR started_at IS NULL
                )
            )
            OR (
                safe_reason_code IN (
                    'budget_evaluation_failed', 'enqueue_attempts_exhausted'
                )
                AND (workflow_run_id IS NOT NULL OR started_at IS NOT NULL)
            )
    )
    """
)


def upgrade() -> None:
    if op.get_bind().execute(_INVALID_EXISTING_ROWS).scalar():
        raise RuntimeError(
            "schedule dispatch claim correlation cleanup is required before migration"
        )

    op.drop_constraint(
        "ck_schedule_dispatch_claims_status_fields",
        "schedule_dispatch_claims",
        type_="check",
    )
    op.drop_constraint(
        "ck_schedule_dispatch_claims_safe_reason",
        "schedule_dispatch_claims",
        type_="check",
    )
    op.create_check_constraint(
        "ck_schedule_dispatch_claims_status_fields",
        "schedule_dispatch_claims",
        _STATUS_FIELDS,
    )
    op.create_check_constraint(
        "ck_schedule_dispatch_claims_safe_reason",
        "schedule_dispatch_claims",
        _SAFE_REASON,
    )
    op.create_check_constraint(
        "ck_schedule_dispatch_claims_preadmission_run",
        "schedule_dispatch_claims",
        "status NOT IN ('pending', 'dispatching', 'enqueued') "
        "OR workflow_run_id IS NULL",
    )


def downgrade() -> None:
    assert_schedule_dispatch_downgrade_is_safe(op.get_bind())
    assert_schedule_configuration_quarantine_downgrade_is_safe(op.get_bind())

    op.drop_constraint(
        "ck_schedule_dispatch_claims_preadmission_run",
        "schedule_dispatch_claims",
        type_="check",
    )
    op.drop_constraint(
        "ck_schedule_dispatch_claims_status_fields",
        "schedule_dispatch_claims",
        type_="check",
    )
    op.drop_constraint(
        "ck_schedule_dispatch_claims_safe_reason",
        "schedule_dispatch_claims",
        type_="check",
    )
    op.create_check_constraint(
        "ck_schedule_dispatch_claims_status_fields",
        "schedule_dispatch_claims",
        _LEGACY_STATUS_FIELDS,
    )
    op.create_check_constraint(
        "ck_schedule_dispatch_claims_safe_reason",
        "schedule_dispatch_claims",
        _LEGACY_SAFE_REASON,
    )
