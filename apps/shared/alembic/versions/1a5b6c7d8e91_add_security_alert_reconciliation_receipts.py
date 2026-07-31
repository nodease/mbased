"""Add durable Security Alert reconciliation receipts.

Revision ID: 1a5b6c7d8e91
Revises: 0f4a5b6c7d89
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "1a5b6c7d8e91"
down_revision: Union[str, Sequence[str], None] = "0f4a5b6c7d89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "security_alert_reconciliation_receipts",
        sa.Column("processor_name", sa.String(length=100), nullable=False),
        sa.Column(
            "audit_log_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("processor_name", "audit_log_id"),
    )


def downgrade() -> None:
    op.drop_table("security_alert_reconciliation_receipts")
