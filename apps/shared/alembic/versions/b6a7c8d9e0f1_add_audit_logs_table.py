"""add audit_logs table

Revision ID: b6a7c8d9e0f1
Revises: 2a28cca99a72
Create Date: 2026-06-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6a7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "2a28cca99a72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "actor_type",
            sa.Enum("user", "admin", "system", name="audit_actor_type"),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Enum("action", "data_change", name="audit_category"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=True),
        sa.Column("target_id", sa.String(length=255), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            sa.Enum("success", "failure", name="audit_status"),
            nullable=False,
        ),
        sa.Column(
            "audit_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_occurred_at", "audit_logs", ["occurred_at"], unique=False
    )
    op.create_index(
        "ix_audit_logs_actor_id", "audit_logs", ["actor_id"], unique=False
    )
    op.create_index(
        "ix_audit_logs_category", "audit_logs", ["category"], unique=False
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index(
        "ix_audit_logs_target",
        "audit_logs",
        ["target_type", "target_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_audit_logs_target", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_category", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_occurred_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    # Enum 타입 정리 (PostgreSQL)
    sa.Enum(name="audit_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="audit_category").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="audit_actor_type").drop(op.get_bind(), checkfirst=True)
