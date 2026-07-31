"""Backfill Cost Optimizer retention column

Revision ID: fa1b2c3d4e5f
Revises: f9b3c4d5e6a7
Create Date: 2026-07-06 14:44:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "fa1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "f9b3c4d5e6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE cost_optimizer_experiments
        ADD COLUMN IF NOT EXISTS retention_expires_at TIMESTAMP WITH TIME ZONE
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_cost_optimizer_experiments_retention
        ON cost_optimizer_experiments (retention_expires_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cost_optimizer_experiments_retention")
    op.execute(
        """
        ALTER TABLE cost_optimizer_experiments
        DROP COLUMN IF EXISTS retention_expires_at
        """
    )
