"""Add organization memberships

Revision ID: 9c1d2e3f4a66
Revises: f8a9b0c1d2e3
Create Date: 2026-06-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c1d2e3f4a66"
down_revision: Union[str, Sequence[str], None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "membership_state",
            sa.String(length=50),
            server_default=sa.text("'invited'"),
            nullable=False,
        ),
        sa.Column(
            "organization_auth_state",
            sa.String(length=50),
            server_default=sa.text("'member'"),
            nullable=False,
        ),
        sa.Column("invited_by", sa.UUID(), nullable=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
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
            "membership_state IN ('invited', 'active', 'suspended', 'removed')",
            name="ck_organization_memberships_membership_state",
        ),
        sa.CheckConstraint(
            "organization_auth_state IN ('member', 'manager')",
            name="ck_organization_memberships_organization_auth_state",
        ),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_organization_memberships_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_organization_memberships_organization_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_organization_memberships_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name="fk_organization_memberships_invited_by",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_memberships"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_memberships_organization_user",
        ),
    )
    op.create_index(
        "ix_organization_memberships_organization_id",
        "organization_memberships",
        ["organization_id"],
    )
    op.create_index(
        "ix_organization_memberships_user_id",
        "organization_memberships",
        ["user_id"],
    )
    op.create_index(
        "ix_organization_memberships_membership_state",
        "organization_memberships",
        ["membership_state"],
    )
    op.create_index(
        "ix_organization_memberships_org_state",
        "organization_memberships",
        ["organization_id", "membership_state"],
    )
    op.create_index(
        "ix_organization_memberships_user_state",
        "organization_memberships",
        ["user_id", "membership_state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organization_memberships_user_state",
        table_name="organization_memberships",
    )
    op.drop_index(
        "ix_organization_memberships_org_state",
        table_name="organization_memberships",
    )
    op.drop_index(
        "ix_organization_memberships_membership_state",
        table_name="organization_memberships",
    )
    op.drop_index(
        "ix_organization_memberships_user_id",
        table_name="organization_memberships",
    )
    op.drop_index(
        "ix_organization_memberships_organization_id",
        table_name="organization_memberships",
    )
    op.drop_table("organization_memberships")
