"""Add schedule WorkflowRun visibility signal state.

Revision ID: fc0d1e2f3a45
Revises: fb9c0d1e2f34
"""

from typing import Sequence, Union

import sqlalchemy as sa
from apps.shared.alembic.schedule_dispatch_downgrade import (
    assert_schedule_configuration_quarantine_downgrade_is_safe,
    assert_schedule_dispatch_downgrade_is_safe,
)

from alembic import op

revision: str = "fc0d1e2f3a45"
down_revision: Union[str, Sequence[str], None] = "fb9c0d1e2f34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schedule_dispatch_claims",
        sa.Column(
            "workflow_run_missing_reported_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_schedule_dispatch_claims_visibility_gap",
        "schedule_dispatch_claims",
        ["workflow_run_missing_reported_at", "status", "started_at"],
    )


def downgrade() -> None:
    assert_schedule_dispatch_downgrade_is_safe(op.get_bind())
    assert_schedule_configuration_quarantine_downgrade_is_safe(op.get_bind())
    op.drop_index(
        "ix_schedule_dispatch_claims_visibility_gap",
        table_name="schedule_dispatch_claims",
    )
    op.drop_column("schedule_dispatch_claims", "workflow_run_missing_reported_at")
