"""Add user workflow permissions

Revision ID: e1f2a3b4c5d6
Revises: c2d3e4f5a6b7
Create Date: 2026-06-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workflows_id_organization_id",
        "workflows",
        ["id", "organization_id"],
    )
    op.create_table(
        "user_workflow_permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grantee_organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("auth_state", sa.String(length=50), nullable=False),
        sa.Column("assigned_by", sa.UUID(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "flags",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')",
            name="ck_user_workflow_permissions_auth_state",
        ),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_user_workflow_permissions_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["grantee_organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["workflow_id", "grantee_organization_id"],
            ["workflows.id", "workflows.organization_id"],
            name="fk_user_workflow_permissions_workflow_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "workflow_id",
            name="uq_user_workflow_permissions_org_user_workflow",
        ),
    )
    op.create_index(
        "ix_user_workflow_permissions_assigned_by",
        "user_workflow_permissions",
        ["assigned_by"],
    )
    op.create_index(
        "ix_user_workflow_permissions_user_id",
        "user_workflow_permissions",
        ["user_id"],
    )
    op.create_index(
        "ix_user_workflow_permissions_workflow_id",
        "user_workflow_permissions",
        ["workflow_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_workflow_permissions_workflow_id",
        table_name="user_workflow_permissions",
    )
    op.drop_index(
        "ix_user_workflow_permissions_user_id",
        table_name="user_workflow_permissions",
    )
    op.drop_index(
        "ix_user_workflow_permissions_assigned_by",
        table_name="user_workflow_permissions",
    )
    op.drop_table("user_workflow_permissions")
    op.drop_constraint(
        "uq_workflows_id_organization_id",
        "workflows",
        type_="unique",
    )
