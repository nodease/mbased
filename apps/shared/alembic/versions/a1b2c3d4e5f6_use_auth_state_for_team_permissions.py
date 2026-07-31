"""Use auth_state for team permissions

Revision ID: a1b2c3d4e5f6
Revises: f6b2c9d8e1a4
Create Date: 2026-06-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f6b2c9d8e1a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "team_permission",
        sa.Column(
            "auth_state",
            sa.String(length=50),
            nullable=False,
            server_default="none",
        ),
    )
    op.alter_column("team_permission", "auth_state", server_default=None)

    op.drop_column("team_permission", "can_assign_tag")
    op.drop_column("team_permission", "can_manage_tag")
    op.drop_column("team_permission", "can_delete")
    op.drop_column("team_permission", "can_execute")
    op.drop_column("team_permission", "can_write")
    op.drop_column("team_permission", "can_read")

    op.create_table(
        "workflow_team_permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("team_permission_id", sa.UUID(), nullable=False),
        sa.Column("assigned_by", sa.UUID(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["team_permission_id"], ["team_permission.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "workflow_id",
            "team_permission_id",
            name="uq_workflow_team_permissions_organization_workflow_team",
        ),
    )
    op.create_index(
        "ix_workflow_team_permissions_assigned_by",
        "workflow_team_permissions",
        ["assigned_by"],
    )
    op.create_index(
        "ix_workflow_team_permissions_organization_id",
        "workflow_team_permissions",
        ["organization_id"],
    )
    op.create_index(
        "ix_workflow_team_permissions_team_permission_id",
        "workflow_team_permissions",
        ["team_permission_id"],
    )
    op.create_index(
        "ix_workflow_team_permissions_workflow_id",
        "workflow_team_permissions",
        ["workflow_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_team_permissions_workflow_id",
        table_name="workflow_team_permissions",
    )
    op.drop_index(
        "ix_workflow_team_permissions_team_permission_id",
        table_name="workflow_team_permissions",
    )
    op.drop_index(
        "ix_workflow_team_permissions_organization_id",
        table_name="workflow_team_permissions",
    )
    op.drop_index(
        "ix_workflow_team_permissions_assigned_by",
        table_name="workflow_team_permissions",
    )
    op.drop_table("workflow_team_permissions")

    op.add_column(
        "team_permission",
        sa.Column("can_read", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "team_permission",
        sa.Column("can_write", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "team_permission",
        sa.Column("can_execute", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "team_permission",
        sa.Column("can_delete", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "team_permission",
        sa.Column(
            "can_manage_tag",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "team_permission",
        sa.Column(
            "can_assign_tag",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.drop_column("team_permission", "auth_state")
