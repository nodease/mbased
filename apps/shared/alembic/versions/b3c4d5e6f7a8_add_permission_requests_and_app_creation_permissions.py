"""Add permission requests and user app creation permissions

Revision ID: b3c4d5e6f7a8
Revises: d0e1f2a3b4c5
Create Date: 2026-07-04 00:00:00.000000

ADR-0016: 권한 신청과 App 생성 권한 모델.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_app_creation_permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grantee_organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("assigned_by", sa.UUID(), nullable=False),
        sa.Column(
            "assigned_at",
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
            "flags >= 0",
            name="ck_user_app_creation_permissions_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["grantee_organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            name="uq_user_app_creation_permissions_org_user",
        ),
    )
    op.create_index(
        op.f("ix_user_app_creation_permissions_user_id"),
        "user_app_creation_permissions",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "permission_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "requested_permission",
            sa.String(length=100),
            server_default=sa.text("'app.create'"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=50),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_permission_requests_status",
        ),
        sa.CheckConstraint(
            "requested_permission IN ('app.create')",
            name="ck_permission_requests_requested_permission",
        ),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_permission_requests_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_permission_requests_organization_status",
        "permission_requests",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_permission_requests_pending",
        "permission_requests",
        ["organization_id", "user_id", "requested_permission"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_permission_requests_pending", table_name="permission_requests")
    op.drop_index(
        "ix_permission_requests_organization_status",
        table_name="permission_requests",
    )
    op.drop_table("permission_requests")
    op.drop_index(
        op.f("ix_user_app_creation_permissions_user_id"),
        table_name="user_app_creation_permissions",
    )
    op.drop_table("user_app_creation_permissions")
