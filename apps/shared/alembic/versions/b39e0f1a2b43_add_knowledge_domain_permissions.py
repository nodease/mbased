"""Add Knowledge domain delegation permissions and bootstrap legacy KB managers.

Revision ID: b39e0f1a2b43
Revises: b28d9e0f1a32
"""

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b39e0f1a2b43"
down_revision: Union[str, Sequence[str], None] = "b28d9e0f1a32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIONS = "'catalog_manage', 'permission_delegate', 'lifecycle_manage', 'sync_manage'"


def upgrade() -> None:
    _create_team_domain_permissions()
    _create_user_domain_permissions()
    _backfill_active_same_organization_kb_owners()


def _create_team_domain_permissions() -> None:
    op.create_table(
        "team_knowledge_domain_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_action", sa.String(length=32), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flags", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            f"permission_action IN ({_ACTIONS})",
            name="ck_team_knowledge_domain_permissions_action",
        ),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_team_knowledge_domain_permissions_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["team_id", "organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_knowledge_domain_permissions_team_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "team_id",
            "permission_action",
            name="uq_team_knowledge_domain_permissions_action",
        ),
    )
    for column in ("organization_id", "team_id", "assigned_by", "expires_at"):
        op.create_index(
            f"ix_team_knowledge_domain_permissions_{column}",
            "team_knowledge_domain_permissions",
            [column],
        )
    op.create_index(
        "ix_team_knowledge_domain_permissions_effective",
        "team_knowledge_domain_permissions",
        ["organization_id", "team_id", "permission_action", "expires_at"],
    )


def _create_user_domain_permissions() -> None:
    op.create_table(
        "user_knowledge_domain_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_action", sa.String(length=32), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flags", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            f"permission_action IN ({_ACTIONS})",
            name="ck_user_knowledge_domain_permissions_action",
        ),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_user_knowledge_domain_permissions_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            "permission_action",
            name="uq_user_knowledge_domain_permissions_action",
        ),
    )
    for column in ("organization_id", "user_id", "assigned_by", "expires_at"):
        op.create_index(
            f"ix_user_knowledge_domain_permissions_{column}",
            "user_knowledge_domain_permissions",
            [column],
        )
    op.create_index(
        "ix_user_knowledge_domain_permissions_effective",
        "user_knowledge_domain_permissions",
        ["organization_id", "user_id", "permission_action", "expires_at"],
    )


def _backfill_active_same_organization_kb_owners() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT kb.id AS knowledge_base_id, kb.organization_id, kb.user_id
            FROM knowledge_bases AS kb
            JOIN users AS u ON u.id = kb.user_id AND u.deactivated_at IS NULL
            JOIN organization_memberships AS om
              ON om.organization_id = kb.organization_id
             AND om.user_id = kb.user_id
             AND om.membership_state = 'active'
             AND om.organization_auth_state IN ('member', 'manager')
            WHERE kb.organization_id IS NOT NULL
            """
        )
    ).mappings()
    assigned_at = datetime.now(timezone.utc)
    statement = sa.text(
        """
        INSERT INTO user_knowledge_permissions (
            id, grantee_organization_id, user_id, knowledge_base_id,
            auth_state, assigned_by, assigned_at, options, flags
        ) VALUES (
            :id, :organization_id, :user_id, :knowledge_base_id,
            'manager', :user_id, :assigned_at, '{}'::jsonb, 0
        )
        ON CONFLICT (grantee_organization_id, user_id, knowledge_base_id)
        DO UPDATE SET auth_state = 'manager'
        WHERE user_knowledge_permissions.auth_state <> 'manager'
        """
    )
    for row in rows:
        bind.execute(
            statement,
            {
                "id": uuid.uuid4(),
                "organization_id": row["organization_id"],
                "user_id": row["user_id"],
                "knowledge_base_id": row["knowledge_base_id"],
                "assigned_at": assigned_at,
            },
        )


def downgrade() -> None:
    op.drop_index(
        "ix_user_knowledge_domain_permissions_effective",
        table_name="user_knowledge_domain_permissions",
    )
    for column in ("expires_at", "assigned_by", "user_id", "organization_id"):
        op.drop_index(
            f"ix_user_knowledge_domain_permissions_{column}",
            table_name="user_knowledge_domain_permissions",
        )
    op.drop_table("user_knowledge_domain_permissions")

    op.drop_index(
        "ix_team_knowledge_domain_permissions_effective",
        table_name="team_knowledge_domain_permissions",
    )
    for column in ("expires_at", "assigned_by", "team_id", "organization_id"):
        op.drop_index(
            f"ix_team_knowledge_domain_permissions_{column}",
            table_name="team_knowledge_domain_permissions",
        )
    op.drop_table("team_knowledge_domain_permissions")
