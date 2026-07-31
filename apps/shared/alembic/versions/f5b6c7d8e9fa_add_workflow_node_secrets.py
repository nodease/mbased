"""Add encrypted workflow node secret revisions.

Revision ID: f5b6c7d8e9fa
Revises: f4a5b6c7d8e9
Create Date: 2026-07-19 18:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f5b6c7d8e9fa"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_node_secrets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("parameter_key", sa.String(length=64), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.String(length=64), nullable=False),
        sa.Column("encryption_algorithm", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="active"
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_workflow_node_secrets_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "organization_id"],
            ["workflows.id", "workflows.organization_id"],
            name="fk_workflow_node_secrets_workflow_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_node_secrets_organization_id",
        "workflow_node_secrets",
        ["organization_id"],
    )
    op.create_index(
        "ix_workflow_node_secrets_workflow_id",
        "workflow_node_secrets",
        ["workflow_id"],
    )
    op.create_index(
        "ix_workflow_node_secrets_status",
        "workflow_node_secrets",
        ["status"],
    )
    op.create_index(
        "ix_workflow_node_secrets_created_by",
        "workflow_node_secrets",
        ["created_by"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_node_secrets_created_by",
        table_name="workflow_node_secrets",
    )
    op.drop_index(
        "ix_workflow_node_secrets_status",
        table_name="workflow_node_secrets",
    )
    op.drop_index(
        "ix_workflow_node_secrets_workflow_id",
        table_name="workflow_node_secrets",
    )
    op.drop_index(
        "ix_workflow_node_secrets_organization_id",
        table_name="workflow_node_secrets",
    )
    op.drop_table("workflow_node_secrets")
