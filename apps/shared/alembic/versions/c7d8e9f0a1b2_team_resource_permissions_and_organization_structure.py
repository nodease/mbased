"""Add team resource permissions and organization structure

Revision ID: c7d8e9f0a1b2
Revises: a63aa1d5a656
Create Date: 2026-06-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "a63aa1d5a656"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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


def _has_unique(table_name: str, constraint_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        constraint["name"] == constraint_name
        for constraint in inspect(op.get_bind()).get_unique_constraints(table_name)
    )


def _has_fk(table_name: str, constraint_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        constraint["name"] == constraint_name
        for constraint in inspect(op.get_bind()).get_foreign_keys(table_name)
    )


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _create_index_if_missing(
    index_name: str, table_name: str, columns: list[str]
) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_unique_if_exists(constraint_name: str, table_name: str) -> None:
    if _has_unique(table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, type_="unique")


def _create_unique_if_missing(
    constraint_name: str, table_name: str, columns: list[str]
) -> None:
    if not _has_unique(table_name, constraint_name):
        op.create_unique_constraint(constraint_name, table_name, columns)


def _drop_fk_if_exists(constraint_name: str, table_name: str) -> None:
    if _has_fk(table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")


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


def _rename_column_if_exists(table_name: str, old_name: str, new_name: str) -> None:
    if _has_column(table_name, old_name) and not _has_column(table_name, new_name):
        op.alter_column(table_name, old_name, new_column_name=new_name)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _has_column(table_name, column_name):
        op.drop_column(table_name, column_name)


def _add_auth_state_if_missing(table_name: str) -> None:
    if not _has_column(table_name, "auth_state"):
        op.add_column(
            table_name,
            sa.Column(
                "auth_state",
                sa.String(length=50),
                nullable=False,
                server_default="none",
            ),
        )
        op.alter_column(table_name, "auth_state", server_default=None)


def _delete_nulls_and_set_not_null(table_name: str, column_name: str) -> None:
    if not _has_column(table_name, column_name):
        return
    op.execute(sa.text(f"DELETE FROM {table_name} WHERE {column_name} IS NULL"))
    op.alter_column(table_name, column_name, nullable=False)


def _normalize_team_organization_id() -> None:
    if not _has_column("team_permission", "organization_id"):
        return

    op.execute(
        sa.text(
            """
            UPDATE team_permission
            SET organization_id = created_by
            WHERE organization_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM organization
                  WHERE organization.id = team_permission.created_by
              );
            """
        )
    )
    if _has_column("user_team_permissions", "team_permission_id"):
        op.execute(
            sa.text(
                """
                DELETE FROM user_team_permissions
                WHERE team_permission_id IN (
                    SELECT id
                    FROM team_permission
                    WHERE organization_id IS NULL
                );
                """
            )
        )
    if _has_column("workflow_team_permissions", "team_permission_id"):
        op.execute(
            sa.text(
                """
                DELETE FROM workflow_team_permissions
                WHERE team_permission_id IN (
                    SELECT id
                    FROM team_permission
                    WHERE organization_id IS NULL
                );
                """
            )
        )
    _delete_nulls_and_set_not_null("team_permission", "organization_id")


def _align_assignment_team_organization(table_name: str) -> None:
    if not (
        _has_table(table_name)
        and _has_column(table_name, "team_id")
        and _has_column(table_name, "grantee_organization_id")
    ):
        return

    op.execute(
        sa.text(
            f"""
            DELETE FROM {table_name}
            WHERE NOT EXISTS (
                SELECT 1
                FROM team_permission
                WHERE team_permission.id = {table_name}.team_id
            );
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {table_name}
            SET grantee_organization_id = team_permission.organization_id
            FROM team_permission
            WHERE {table_name}.team_id = team_permission.id
              AND {table_name}.grantee_organization_id
                  IS DISTINCT FROM team_permission.organization_id;
            """
        )
    )


def _create_team_composite_fk(table_name: str, constraint_name: str) -> None:
    _align_assignment_team_organization(table_name)
    _create_fk_if_missing(
        constraint_name,
        table_name,
        "team_permission",
        ["team_id", "grantee_organization_id"],
        ["id", "organization_id"],
    )


def _create_resource_permission_table(
    table_name: str,
    resource_column: str,
    resource_table: str,
    unique_name: str,
    team_org_fk_name: str,
) -> None:
    if _has_table(table_name):
        _create_team_composite_fk(table_name, team_org_fk_name)
        return

    op.create_table(
        table_name,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grantee_organization_id", sa.UUID(), nullable=False),
        sa.Column(resource_column, sa.UUID(), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("auth_state", sa.String(length=50), nullable=False),
        sa.Column("assigned_by", sa.UUID(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["grantee_organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint([resource_column], [f"{resource_table}.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["team_permission.id"]),
        sa.ForeignKeyConstraint(
            ["team_id", "grantee_organization_id"],
            ["team_permission.id", "team_permission.organization_id"],
            name=team_org_fk_name,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grantee_organization_id",
            resource_column,
            "team_id",
            name=unique_name,
        ),
    )
    _create_index_if_missing(
        f"ix_{table_name}_grantee_organization_id",
        table_name,
        ["grantee_organization_id"],
    )
    _create_index_if_missing(
        f"ix_{table_name}_{resource_column}",
        table_name,
        [resource_column],
    )
    _create_index_if_missing(f"ix_{table_name}_team_id", table_name, ["team_id"])
    _create_index_if_missing(
        f"ix_{table_name}_assigned_by",
        table_name,
        ["assigned_by"],
    )


def _create_organization_structure() -> None:
    if not _has_table("organization_structure"):
        op.create_table(
            "organization_structure",
            sa.Column("ancestor_id", sa.UUID(), nullable=False),
            sa.Column("descendant_id", sa.UUID(), nullable=False),
            sa.Column("depth", sa.Integer(), nullable=False),
            sa.CheckConstraint(
                "depth >= 0",
                name="ck_organization_structure_depth_nonnegative",
            ),
            sa.ForeignKeyConstraint(["ancestor_id"], ["organization.id"]),
            sa.ForeignKeyConstraint(["descendant_id"], ["organization.id"]),
            sa.PrimaryKeyConstraint("ancestor_id", "descendant_id"),
        )
    _create_index_if_missing(
        "ix_organization_structure_descendant_id",
        "organization_structure",
        ["descendant_id"],
    )

    op.execute(
        sa.text(
            """
            WITH RECURSIVE org_tree AS (
                SELECT
                    id AS ancestor_id,
                    id AS descendant_id,
                    0 AS depth
                FROM organization
                UNION ALL
                SELECT
                    org_tree.ancestor_id,
                    child.id AS descendant_id,
                    org_tree.depth + 1 AS depth
                FROM org_tree
                JOIN organization AS child
                  ON child.parent_id = org_tree.descendant_id
                WHERE org_tree.depth < 100
            )
            INSERT INTO organization_structure (
                ancestor_id,
                descendant_id,
                depth
            )
            SELECT ancestor_id, descendant_id, MIN(depth) AS depth
            FROM org_tree
            GROUP BY ancestor_id, descendant_id
            ON CONFLICT (ancestor_id, descendant_id) DO NOTHING;
            """
        )
    )


def _upgrade_user_team_permissions() -> None:
    if not _has_table("user_team_permissions"):
        return

    _drop_unique_if_exists(
        "uq_user_team_permissions_organization_user_permission",
        "user_team_permissions",
    )
    _drop_index_if_exists(
        "ix_user_team_permissions_organization_id",
        "user_team_permissions",
    )
    _drop_index_if_exists(
        "ix_user_team_permissions_team_permission_id",
        "user_team_permissions",
    )

    _rename_column_if_exists(
        "user_team_permissions",
        "organization_id",
        "grantee_organization_id",
    )
    _rename_column_if_exists(
        "user_team_permissions",
        "team_permission_id",
        "team_id",
    )

    _delete_nulls_and_set_not_null(
        "user_team_permissions",
        "grantee_organization_id",
    )
    _create_index_if_missing(
        "ix_user_team_permissions_grantee_organization_id",
        "user_team_permissions",
        ["grantee_organization_id"],
    )
    _create_index_if_missing(
        "ix_user_team_permissions_team_id",
        "user_team_permissions",
        ["team_id"],
    )
    _align_assignment_team_organization("user_team_permissions")
    _create_unique_if_missing(
        "uq_user_team_permissions_org_user_team",
        "user_team_permissions",
        ["grantee_organization_id", "user_id", "team_id"],
    )
    _create_team_composite_fk(
        "user_team_permissions",
        "fk_user_team_permissions_team_org",
    )


def _upgrade_workflow_team_permissions() -> None:
    if not _has_table("workflow_team_permissions"):
        return

    _drop_unique_if_exists(
        "uq_workflow_team_permissions_organization_workflow_team",
        "workflow_team_permissions",
    )
    _drop_index_if_exists(
        "ix_workflow_team_permissions_organization_id",
        "workflow_team_permissions",
    )
    _drop_index_if_exists(
        "ix_workflow_team_permissions_team_permission_id",
        "workflow_team_permissions",
    )

    _rename_column_if_exists(
        "workflow_team_permissions",
        "organization_id",
        "grantee_organization_id",
    )
    _rename_column_if_exists(
        "workflow_team_permissions",
        "team_permission_id",
        "team_id",
    )
    _add_auth_state_if_missing("workflow_team_permissions")

    _create_index_if_missing(
        "ix_workflow_team_permissions_grantee_organization_id",
        "workflow_team_permissions",
        ["grantee_organization_id"],
    )
    _create_index_if_missing(
        "ix_workflow_team_permissions_team_id",
        "workflow_team_permissions",
        ["team_id"],
    )
    _align_assignment_team_organization("workflow_team_permissions")
    _create_unique_if_missing(
        "uq_workflow_team_permissions_org_workflow_team",
        "workflow_team_permissions",
        ["grantee_organization_id", "workflow_id", "team_id"],
    )
    _create_team_composite_fk(
        "workflow_team_permissions",
        "fk_workflow_team_permissions_team_org",
    )


def upgrade() -> None:
    _drop_column_if_exists("team_permission", "auth_state")
    _normalize_team_organization_id()
    _create_unique_if_missing(
        "uq_team_permission_id_organization_id",
        "team_permission",
        ["id", "organization_id"],
    )

    _upgrade_user_team_permissions()
    _upgrade_workflow_team_permissions()
    _create_organization_structure()
    _create_resource_permission_table(
        "team_knowledge_permissions",
        "knowledge_base_id",
        "knowledge_bases",
        "uq_team_knowledge_permissions_org_knowledge_team",
        "fk_team_knowledge_permissions_team_org",
    )
    _create_resource_permission_table(
        "team_llm_permissions",
        "llm_credential_id",
        "llm_credentials",
        "uq_team_llm_permissions_org_credential_team",
        "fk_team_llm_permissions_team_org",
    )
    _create_resource_permission_table(
        "team_audit_permissions",
        "target_organization_id",
        "organization",
        "uq_team_audit_permissions_org_target_team",
        "fk_team_audit_permissions_team_org",
    )


def _downgrade_user_team_permissions() -> None:
    if not _has_table("user_team_permissions"):
        return

    _drop_fk_if_exists(
        "fk_user_team_permissions_team_org",
        "user_team_permissions",
    )
    _drop_unique_if_exists(
        "uq_user_team_permissions_org_user_team",
        "user_team_permissions",
    )
    _drop_index_if_exists(
        "ix_user_team_permissions_grantee_organization_id",
        "user_team_permissions",
    )
    _drop_index_if_exists(
        "ix_user_team_permissions_team_id",
        "user_team_permissions",
    )

    _rename_column_if_exists(
        "user_team_permissions",
        "grantee_organization_id",
        "organization_id",
    )
    _rename_column_if_exists(
        "user_team_permissions",
        "team_id",
        "team_permission_id",
    )

    _create_index_if_missing(
        "ix_user_team_permissions_organization_id",
        "user_team_permissions",
        ["organization_id"],
    )
    _create_index_if_missing(
        "ix_user_team_permissions_team_permission_id",
        "user_team_permissions",
        ["team_permission_id"],
    )
    _create_unique_if_missing(
        "uq_user_team_permissions_organization_user_permission",
        "user_team_permissions",
        ["organization_id", "user_id", "team_permission_id"],
    )


def _downgrade_workflow_team_permissions() -> None:
    if not _has_table("workflow_team_permissions"):
        return

    _drop_fk_if_exists(
        "fk_workflow_team_permissions_team_org",
        "workflow_team_permissions",
    )
    _drop_unique_if_exists(
        "uq_workflow_team_permissions_org_workflow_team",
        "workflow_team_permissions",
    )
    _drop_index_if_exists(
        "ix_workflow_team_permissions_grantee_organization_id",
        "workflow_team_permissions",
    )
    _drop_index_if_exists(
        "ix_workflow_team_permissions_team_id",
        "workflow_team_permissions",
    )
    _drop_column_if_exists("workflow_team_permissions", "auth_state")

    _rename_column_if_exists(
        "workflow_team_permissions",
        "grantee_organization_id",
        "organization_id",
    )
    _rename_column_if_exists(
        "workflow_team_permissions",
        "team_id",
        "team_permission_id",
    )

    _create_index_if_missing(
        "ix_workflow_team_permissions_organization_id",
        "workflow_team_permissions",
        ["organization_id"],
    )
    _create_index_if_missing(
        "ix_workflow_team_permissions_team_permission_id",
        "workflow_team_permissions",
        ["team_permission_id"],
    )
    _create_unique_if_missing(
        "uq_workflow_team_permissions_organization_workflow_team",
        "workflow_team_permissions",
        ["organization_id", "workflow_id", "team_permission_id"],
    )


def downgrade() -> None:
    for table_name in (
        "team_audit_permissions",
        "team_llm_permissions",
        "team_knowledge_permissions",
    ):
        if _has_table(table_name):
            op.drop_table(table_name)

    if _has_table("organization_structure"):
        _drop_index_if_exists(
            "ix_organization_structure_descendant_id",
            "organization_structure",
        )
        op.drop_table("organization_structure")

    _downgrade_workflow_team_permissions()
    _downgrade_user_team_permissions()

    if not _has_column("team_permission", "auth_state"):
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
    if _has_column("team_permission", "organization_id"):
        _drop_unique_if_exists(
            "uq_team_permission_id_organization_id",
            "team_permission",
        )
        op.alter_column("team_permission", "organization_id", nullable=True)
