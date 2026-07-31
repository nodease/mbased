"""Add user LLM credential permissions

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_llm_credentials_id_organization_id",
        "llm_credentials",
        ["id", "organization_id"],
    )
    op.create_table(
        "user_llm_permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grantee_organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("llm_credential_id", sa.UUID(), nullable=False),
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
            name="ck_user_llm_permissions_auth_state",
        ),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_user_llm_permissions_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["grantee_organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["llm_credential_id", "grantee_organization_id"],
            ["llm_credentials.id", "llm_credentials.organization_id"],
            name="fk_user_llm_permissions_credential_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "llm_credential_id",
            name="uq_user_llm_permissions_org_user_credential",
        ),
    )
    op.create_index(
        "ix_user_llm_permissions_assigned_by",
        "user_llm_permissions",
        ["assigned_by"],
    )
    op.create_index(
        "ix_user_llm_permissions_grantee_organization_id",
        "user_llm_permissions",
        ["grantee_organization_id"],
    )
    op.create_index(
        "ix_user_llm_permissions_llm_credential_id",
        "user_llm_permissions",
        ["llm_credential_id"],
    )
    op.create_index(
        "ix_user_llm_permissions_user_id",
        "user_llm_permissions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_llm_permissions_user_id",
        table_name="user_llm_permissions",
    )
    op.drop_index(
        "ix_user_llm_permissions_llm_credential_id",
        table_name="user_llm_permissions",
    )
    op.drop_index(
        "ix_user_llm_permissions_grantee_organization_id",
        table_name="user_llm_permissions",
    )
    op.drop_index(
        "ix_user_llm_permissions_assigned_by",
        table_name="user_llm_permissions",
    )
    op.drop_table("user_llm_permissions")
    op.drop_constraint(
        "uq_llm_credentials_id_organization_id",
        "llm_credentials",
        type_="unique",
    )
