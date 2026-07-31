"""Add Cost Optimizer downstream snapshot

Revision ID: fa2b3c4d5e6f
Revises: fa1b2c3d4e5f
Create Date: 2026-07-06 20:20:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "fa2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "fa1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE cost_optimizer_experiments
        ADD COLUMN IF NOT EXISTS baseline_downstream_snapshot JSONB NOT NULL DEFAULT '{}'
        """
    )
    op.execute(
        """
        ALTER TABLE cost_optimizer_experiments
        ALTER COLUMN baseline_downstream_snapshot DROP DEFAULT
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE cost_optimizer_experiments
        DROP COLUMN IF EXISTS baseline_downstream_snapshot
        """
    )
