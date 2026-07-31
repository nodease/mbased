"""Add user Knowledge Base permissions

Revision ID: fa7b8c9d0e12
Revises: fa6b7c8d9e01
Create Date: 2026-07-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fa7b8c9d0e12"
down_revision: Union[str, Sequence[str], None] = "fa6b7c8d9e01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_knowledge_bases_id_organization_id",
        "knowledge_bases",
        ["id", "organization_id"],
    )
    op.create_table(
        "user_knowledge_permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grantee_organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("knowledge_base_id", sa.UUID(), nullable=False),
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
            name="ck_user_knowledge_permissions_auth_state",
        ),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_user_knowledge_permissions_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["grantee_organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "grantee_organization_id"],
            ["knowledge_bases.id", "knowledge_bases.organization_id"],
            name="fk_user_knowledge_permissions_knowledge_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "knowledge_base_id",
            name="uq_user_knowledge_permissions_org_user_knowledge",
        ),
    )
    op.create_index(
        "ix_user_knowledge_permissions_assigned_by",
        "user_knowledge_permissions",
        ["assigned_by"],
    )
    op.create_index(
        "ix_user_knowledge_permissions_grantee_organization_id",
        "user_knowledge_permissions",
        ["grantee_organization_id"],
    )
    op.create_index(
        "ix_user_knowledge_permissions_knowledge_base_id",
        "user_knowledge_permissions",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_user_knowledge_permissions_user_id",
        "user_knowledge_permissions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_knowledge_permissions_user_id",
        table_name="user_knowledge_permissions",
    )
    op.drop_index(
        "ix_user_knowledge_permissions_knowledge_base_id",
        table_name="user_knowledge_permissions",
    )
    op.drop_index(
        "ix_user_knowledge_permissions_grantee_organization_id",
        table_name="user_knowledge_permissions",
    )
    op.drop_index(
        "ix_user_knowledge_permissions_assigned_by",
        table_name="user_knowledge_permissions",
    )
    op.drop_table("user_knowledge_permissions")
    op.drop_constraint(
        "uq_knowledge_bases_id_organization_id",
        "knowledge_bases",
        type_="unique",
    )
