"""add schedule configuration preflight cancellation reason

Revision ID: 0f4a5b6c7d89
Revises: fd3e4f5a6b78
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0f4a5b6c7d89"
# fd3e4f5a6b78 is on the merged single-head lineage descending from the
# schedule constraint owner ff5c6d7e8f90.
down_revision: Union[str, Sequence[str], None] = "fd3e4f5a6b78"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_REASON = "configuration_preflight_blocked"
_PENDING_REASONS = ("broker_enqueue_failed", "budget_evaluation_failed")
_CANCELED_REASONS = (
    "app_not_found",
    "budget_blocked",
    "deployment_inactive",
    "deployment_not_current",
    "deployment_not_found",
    "deployment_type_not_allowed",
    "organization_scope_mismatch",
    "organization_scope_missing",
    "schedule_deployment_mismatch",
    "schedule_not_found",
)
_PRE_ADMISSION_DEAD_LETTER_REASONS = (
    "budget_evaluation_failed",
    "enqueue_attempts_exhausted",
)
_POST_ADMISSION_DEAD_LETTER_REASONS = (
    "execution_failed_after_admission",
    "execution_outcome_unknown",
)


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _safe_reason_constraint(*, include_configuration_preflight: bool) -> str:
    canceled_reasons = _CANCELED_REASONS + (
        (_NEW_REASON,) if include_configuration_preflight else ()
    )
    return " OR ".join(
        (
            "(status = 'pending' AND (safe_reason_code IS NULL OR "
            f"safe_reason_code IN ({_sql_values(_PENDING_REASONS)})))",
            "(status = 'canceled' AND safe_reason_code IS NOT NULL AND "
            f"safe_reason_code IN ({_sql_values(canceled_reasons)}))",
            "(status = 'dead_lettered' AND safe_reason_code IS NOT NULL AND "
            f"((safe_reason_code IN ({_sql_values(_PRE_ADMISSION_DEAD_LETTER_REASONS)}) "
            "AND workflow_run_id IS NULL AND started_at IS NULL) OR "
            f"(safe_reason_code IN ({_sql_values(_POST_ADMISSION_DEAD_LETTER_REASONS)}) "
            "AND workflow_run_id IS NOT NULL AND celery_task_id IS NOT NULL "
            "AND enqueued_at IS NOT NULL AND started_at IS NOT NULL)))",
            "(status IN ('dispatching', 'enqueued', 'running', 'succeeded') "
            "AND safe_reason_code IS NULL)",
        )
    )


def _replace_constraint(*, include_configuration_preflight: bool) -> None:
    op.drop_constraint(
        "ck_schedule_dispatch_claims_safe_reason",
        "schedule_dispatch_claims",
        type_="check",
    )
    op.create_check_constraint(
        "ck_schedule_dispatch_claims_safe_reason",
        "schedule_dispatch_claims",
        _safe_reason_constraint(
            include_configuration_preflight=include_configuration_preflight
        ),
    )


def upgrade() -> None:
    _replace_constraint(include_configuration_preflight=True)


def downgrade() -> None:
    bind = op.get_bind()
    blocking_row = bind.execute(
        sa.text(
            "SELECT 1 FROM schedule_dispatch_claims "
            "WHERE safe_reason_code = :reason LIMIT 1"
        ),
        {"reason": _NEW_REASON},
    ).scalar()
    if blocking_row is not None:
        raise RuntimeError(
            "Cannot downgrade while configuration preflight cancellation rows exist"
        )
    _replace_constraint(include_configuration_preflight=False)
