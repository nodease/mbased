"""Add organization permission models

Revision ID: f6b2c9d8e1a4
Revises: 2a28cca99a72
Create Date: 2026-06-25 20:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6b2c9d8e1a4"
down_revision: Union[str, Sequence[str], None] = "2a28cca99a72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rename_tenant_id_to_organization_id(table_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                      AND column_name = 'tenant_id'
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                      AND column_name = 'organization_id'
                )
                THEN
                    ALTER TABLE {table_name}
                    RENAME COLUMN tenant_id TO organization_id;
                END IF;
            END $$;
            """
        )
    )


def _has_table(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        column["name"] == column_name
        for column in inspect(op.get_bind()).get_columns(table_name)
    )


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        index["name"] == index_name
        for index in inspect(op.get_bind()).get_indexes(table_name)
    )


def _has_fk(table_name: str, fk_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        fk["name"] == fk_name
        for fk in inspect(op.get_bind()).get_foreign_keys(table_name)
    )


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _create_fk_if_missing(
    constraint_name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
) -> None:
    if not _has_fk(source_table, constraint_name):
        op.create_foreign_key(
            constraint_name,
            source_table,
            referent_table,
            local_cols,
            remote_cols,
        )


def _backfill_user_organizations() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO organization (
                id,
                parent_id,
                name,
                created_by,
                managed_by,
                is_active,
                created_at,
                updated_at,
                deactivated_at
            )
            SELECT
                users.id,
                NULL,
                COALESCE(NULLIF(users.name, ''), users.email),
                users.id,
                users.id,
                TRUE,
                NOW(),
                NOW(),
                NULL
            FROM users
            ON CONFLICT (id) DO NOTHING;
            """
        )
    )
    if _has_column("users", "organization_id"):
        op.execute(
            sa.text(
                """
                UPDATE users
                SET organization_id = id
                WHERE organization_id IS NULL;
                """
            )
        )


def _clear_orphan_organization_refs(table_name: str) -> None:
    if not _has_column(table_name, "organization_id"):
        return
    op.execute(
        sa.text(
            f"""
            UPDATE {table_name}
            SET organization_id = NULL
            WHERE organization_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM organization
                  WHERE organization.id = {table_name}.organization_id
              );
            """
        )
    )


def upgrade() -> None:
    """Upgrade schema."""
    _rename_tenant_id_to_organization_id("apps")
    _rename_tenant_id_to_organization_id("workflows")
    _rename_tenant_id_to_organization_id("llm_credentials")
    _rename_tenant_id_to_organization_id("llm_usage_logs")

    if not _has_table("organization"):
        op.create_table(
            "organization",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("parent_id", sa.UUID(), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("created_by", sa.UUID(), nullable=False),
            sa.Column("managed_by", sa.UUID(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.ForeignKeyConstraint(["managed_by"], ["users.id"]),
            sa.ForeignKeyConstraint(["parent_id"], ["organization.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("parent_id", "name", name="uq_organization_parent_name"),
        )
    _create_index_if_missing("ix_organization_parent_id", "organization", ["parent_id"])
    _create_index_if_missing("ix_organization_created_by", "organization", ["created_by"])
    _create_index_if_missing("ix_organization_managed_by", "organization", ["managed_by"])

    if not _has_column("users", "organization_id"):
        op.add_column("users", sa.Column("organization_id", sa.UUID(), nullable=True))
    _create_index_if_missing("ix_users_organization_id", "users", ["organization_id"])
    _backfill_user_organizations()
    _create_fk_if_missing(
        "fk_users_organization_id_organization",
        "users",
        "organization",
        ["organization_id"],
        ["id"],
    )

    if not _has_table("team_permission"):
        op.create_table(
            "team_permission",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("organization_id", sa.UUID(), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_by", sa.UUID(), nullable=False),
            sa.Column("managed_by", sa.UUID(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("is_auto_add", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("can_read", sa.Boolean(), nullable=False),
            sa.Column("can_write", sa.Boolean(), nullable=False),
            sa.Column("can_execute", sa.Boolean(), nullable=False),
            sa.Column("can_delete", sa.Boolean(), nullable=False),
            sa.Column("can_manage_tag", sa.Boolean(), nullable=False),
            sa.Column("can_assign_tag", sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.ForeignKeyConstraint(["managed_by"], ["users.id"]),
            sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "organization_id",
                "name",
                name="uq_permission_team_organization_name",
            ),
        )
    _create_index_if_missing(
        "ix_team_permission_organization_id",
        "team_permission",
        ["organization_id"],
    )
    _create_index_if_missing("ix_team_permission_created_by", "team_permission", ["created_by"])
    _create_index_if_missing("ix_team_permission_managed_by", "team_permission", ["managed_by"])

    if not _has_table("user_team_permissions"):
        op.create_table(
            "user_team_permissions",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("organization_id", sa.UUID(), nullable=True),
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("team_permission_id", sa.UUID(), nullable=False),
            sa.Column("assigned_by", sa.UUID(), nullable=False),
            sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
            sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
            sa.ForeignKeyConstraint(["team_permission_id"], ["team_permission.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "organization_id",
                "user_id",
                "team_permission_id",
                name="uq_user_team_permissions_organization_user_permission",
            ),
        )
    _create_index_if_missing(
        "ix_user_team_permissions_organization_id",
        "user_team_permissions",
        ["organization_id"],
    )
    _create_index_if_missing(
        "ix_user_team_permissions_user_id",
        "user_team_permissions",
        ["user_id"],
    )
    _create_index_if_missing(
        "ix_user_team_permissions_team_permission_id",
        "user_team_permissions",
        ["team_permission_id"],
    )
    _create_index_if_missing(
        "ix_user_team_permissions_assigned_by",
        "user_team_permissions",
        ["assigned_by"],
    )

    for table_name in ("apps", "workflows", "llm_credentials", "llm_usage_logs"):
        _clear_orphan_organization_refs(table_name)

    for table_name in ("apps", "workflows", "llm_credentials", "llm_usage_logs"):
        _create_index_if_missing(
            f"ix_{table_name}_organization_id",
            table_name,
            ["organization_id"],
        )
        _create_fk_if_missing(
            f"fk_{table_name}_organization_id_organization",
            table_name,
            "organization",
            ["organization_id"],
            ["id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table_name in ("llm_usage_logs", "llm_credentials", "workflows", "apps"):
        op.drop_constraint(
            f"fk_{table_name}_organization_id_organization",
            table_name,
            type_="foreignkey",
        )
        op.drop_index(op.f(f"ix_{table_name}_organization_id"), table_name=table_name)

    op.drop_index(
        op.f("ix_user_team_permissions_assigned_by"),
        table_name="user_team_permissions",
    )
    op.drop_index(
        op.f("ix_user_team_permissions_team_permission_id"),
        table_name="user_team_permissions",
    )
    op.drop_index(
        op.f("ix_user_team_permissions_user_id"),
        table_name="user_team_permissions",
    )
    op.drop_index(
        op.f("ix_user_team_permissions_organization_id"),
        table_name="user_team_permissions",
    )
    op.drop_table("user_team_permissions")

    op.drop_index(op.f("ix_team_permission_managed_by"), table_name="team_permission")
    op.drop_index(op.f("ix_team_permission_created_by"), table_name="team_permission")
    op.drop_index(
        op.f("ix_team_permission_organization_id"),
        table_name="team_permission",
    )
    op.drop_table("team_permission")

    op.drop_constraint(
        "fk_users_organization_id_organization",
        "users",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_users_organization_id"), table_name="users")
    op.drop_column("users", "organization_id")

    op.drop_index(op.f("ix_organization_managed_by"), table_name="organization")
    op.drop_index(op.f("ix_organization_created_by"), table_name="organization")
    op.drop_index(op.f("ix_organization_parent_id"), table_name="organization")
    op.drop_table("organization")

    for table_name in ("apps", "workflows", "llm_credentials", "llm_usage_logs"):
        _rename_tenant_id_to_organization_id(table_name)
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = '{table_name}'
                          AND column_name = 'organization_id'
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = '{table_name}'
                          AND column_name = 'tenant_id'
                    )
                    THEN
                        ALTER TABLE {table_name}
                        RENAME COLUMN organization_id TO tenant_id;
                    END IF;
                END $$;
                """
            )
        )
