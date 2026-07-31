"""Add durable Security Alert notification outbox.

Revision ID: c05d6e7f8a90
Revises: fe4a5b6c7d89
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c05d6e7f8a90"
down_revision: Union[str, Sequence[str], None] = "fe4a5b6c7d89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "security_alert_notification_outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("owner_token", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False
        ),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retryable", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("safe_reason_code", sa.String(length=100), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
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
            "attempt_count >= 0",
            name="ck_security_alert_notification_outbox_attempt_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_security_alert_notification_outbox_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'succeeded', "
            "'retry_scheduled', 'dead_lettered')",
            name="ck_security_alert_notification_outbox_status",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_security_alert_notification_outbox_org_idempotency",
        ),
    )
    op.create_index(
        "ix_security_alert_notification_outbox_status_retry",
        "security_alert_notification_outbox",
        ["status", "next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_security_alert_notification_outbox_lease",
        "security_alert_notification_outbox",
        ["status", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_security_alert_notification_outbox_lease",
        table_name="security_alert_notification_outbox",
    )
    op.drop_index(
        "ix_security_alert_notification_outbox_status_retry",
        table_name="security_alert_notification_outbox",
    )
    op.drop_table("security_alert_notification_outbox")
