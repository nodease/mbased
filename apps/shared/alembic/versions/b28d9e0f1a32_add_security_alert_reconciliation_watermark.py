"""Add the durable Security Alert reconciliation watermark.

Revision ID: b28d9e0f1a32
Revises: a17c8d9e0f21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b28d9e0f1a32"
down_revision: Union[str, Sequence[str], None] = "a17c8d9e0f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "security_alert_reconciliation_watermarks",
        sa.Column("processor_name", sa.String(length=100), nullable=False),
        sa.Column("activation_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cursor_audit_log_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(cursor_occurred_at IS NULL AND cursor_audit_log_id IS NULL) OR "
            "(cursor_occurred_at IS NOT NULL AND cursor_audit_log_id IS NOT NULL)",
            name="ck_security_alert_reconciliation_cursor_pair",
        ),
        sa.PrimaryKeyConstraint("processor_name"),
    )
    op.execute(
        sa.text(
            "INSERT INTO security_alert_reconciliation_watermarks "
            "(processor_name, activation_started_at) "
            "VALUES ('security-alert-v1', now())"
        )
    )
    op.create_index(
        "ix_audit_logs_occurred_at_id",
        "audit_logs",
        ["occurred_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_occurred_at_id", table_name="audit_logs")
    op.drop_table("security_alert_reconciliation_watermarks")
