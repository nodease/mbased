"""Add organization-scoped Mail credentials and permissions.

Revision ID: fc1d2e3f4a5b
Revises: ff5c6d7e8f90
Create Date: 2026-07-11 20:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fc1d2e3f4a5b"
down_revision: Union[str, Sequence[str], None] = "ff5c6d7e8f90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mail_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_name", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("email_address", sa.String(length=320), nullable=False),
        sa.Column("auth_type", sa.String(length=32), nullable=False),
        sa.Column("imap_host", sa.String(length=255), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False),
        sa.Column("use_ssl", sa.Boolean(), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.String(length=64), nullable=False),
        sa.Column("encryption_algorithm", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "imap_port > 0 AND imap_port <= 65535",
            name="ck_mail_credentials_imap_port",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_mail_credentials_status",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_mail_credentials_id_organization_id",
        ),
    )
    op.create_index(
        op.f("ix_mail_credentials_created_by"),
        "mail_credentials",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mail_credentials_organization_id"),
        "mail_credentials",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mail_credentials_status"),
        "mail_credentials",
        ["status"],
        unique=False,
    )

    op.create_table(
        "team_mail_credential_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mail_credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auth_state", sa.String(length=50), nullable=False),
        sa.Column(
            "grantee_organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "flags", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.CheckConstraint(
            "auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')",
            name="ck_team_mail_credential_permissions_auth_state",
        ),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_team_mail_credential_permissions_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["mail_credential_id", "grantee_organization_id"],
            ["mail_credentials.id", "mail_credentials.organization_id"],
            name="fk_team_mail_credential_permissions_credential_org",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "grantee_organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_mail_credential_permissions_team_org",
        ),
        sa.ForeignKeyConstraint(["grantee_organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grantee_organization_id",
            "mail_credential_id",
            "team_id",
            name="uq_team_mail_credential_permissions_org_credential_team",
        ),
    )
    op.create_index(
        op.f("ix_team_mail_credential_permissions_assigned_by"),
        "team_mail_credential_permissions",
        ["assigned_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_team_mail_credential_permissions_grantee_organization_id"),
        "team_mail_credential_permissions",
        ["grantee_organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_team_mail_credential_permissions_mail_credential_id"),
        "team_mail_credential_permissions",
        ["mail_credential_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_team_mail_credential_permissions_team_id"),
        "team_mail_credential_permissions",
        ["team_id"],
        unique=False,
    )

    op.create_table(
        "user_mail_credential_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mail_credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "grantee_organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auth_state", sa.String(length=50), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "flags", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.CheckConstraint(
            "auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')",
            name="ck_user_mail_credential_permissions_auth_state",
        ),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_user_mail_credential_permissions_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["mail_credential_id", "grantee_organization_id"],
            ["mail_credentials.id", "mail_credentials.organization_id"],
            name="fk_user_mail_credential_permissions_credential_org",
        ),
        sa.ForeignKeyConstraint(["grantee_organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "mail_credential_id",
            name="uq_user_mail_credential_permissions_org_user_credential",
        ),
    )
    op.create_index(
        op.f("ix_user_mail_credential_permissions_assigned_by"),
        "user_mail_credential_permissions",
        ["assigned_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_mail_credential_permissions_grantee_organization_id"),
        "user_mail_credential_permissions",
        ["grantee_organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_mail_credential_permissions_mail_credential_id"),
        "user_mail_credential_permissions",
        ["mail_credential_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_mail_credential_permissions_user_id"),
        "user_mail_credential_permissions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("user_mail_credential_permissions")
    op.drop_table("team_mail_credential_permissions")
    op.drop_table("mail_credentials")
