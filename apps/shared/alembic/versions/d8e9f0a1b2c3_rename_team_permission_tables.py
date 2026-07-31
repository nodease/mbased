"""Rename team permission tables

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-06-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _constraint_names(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()

    inspector = inspect(op.get_bind())
    names: set[str] = set()
    names.update(
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint.get("name")
    )
    names.update(
        constraint["name"]
        for constraint in inspector.get_foreign_keys(table_name)
        if constraint.get("name")
    )
    names.update(
        constraint["name"]
        for constraint in inspector.get_check_constraints(table_name)
        if constraint.get("name")
    )
    primary_key = inspector.get_pk_constraint(table_name)
    if primary_key.get("name"):
        names.add(primary_key["name"])
    return names


def _index_names(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {
        index["name"]
        for index in inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name")
    }


def _rename_table_if_exists(old_name: str, new_name: str) -> None:
    if _has_table(old_name) and not _has_table(new_name):
        op.rename_table(old_name, new_name)


def _rename_constraint_if_exists(
    table_name: str,
    old_name: str,
    new_name: str,
) -> None:
    names = _constraint_names(table_name)
    if old_name in names and new_name not in names:
        op.execute(
            sa.text(
                f'ALTER TABLE "{table_name}" '
                f'RENAME CONSTRAINT "{old_name}" TO "{new_name}"'
            )
        )


def _rename_index_if_exists(old_name: str, new_name: str) -> None:
    names = set()
    for table_name in (
        "teams",
        "team_memberships",
        "team_workflow_permissions",
        "team_permission",
        "user_team_permissions",
        "workflow_team_permissions",
    ):
        names.update(_index_names(table_name))

    if old_name in names and new_name not in names:
        op.execute(sa.text(f'ALTER INDEX "{old_name}" RENAME TO "{new_name}"'))


def _rename_team_constraints() -> None:
    _rename_constraint_if_exists("teams", "team_permission_pkey", "teams_pkey")
    _rename_constraint_if_exists(
        "teams",
        "uq_permission_team_organization_name",
        "uq_teams_organization_name",
    )
    _rename_constraint_if_exists(
        "teams",
        "uq_team_permission_id_organization_id",
        "uq_teams_id_organization_id",
    )
    _rename_constraint_if_exists(
        "teams",
        "team_permission_organization_id_fkey",
        "teams_organization_id_fkey",
    )
    _rename_constraint_if_exists(
        "teams",
        "team_permission_created_by_fkey",
        "teams_created_by_fkey",
    )
    _rename_constraint_if_exists(
        "teams",
        "team_permission_managed_by_fkey",
        "teams_managed_by_fkey",
    )
    _rename_index_if_exists(
        "ix_team_permission_organization_id",
        "ix_teams_organization_id",
    )
    _rename_index_if_exists("ix_team_permission_created_by", "ix_teams_created_by")
    _rename_index_if_exists("ix_team_permission_managed_by", "ix_teams_managed_by")


def _rename_membership_constraints() -> None:
    _rename_constraint_if_exists(
        "team_memberships",
        "user_team_permissions_pkey",
        "team_memberships_pkey",
    )
    _rename_constraint_if_exists(
        "team_memberships",
        "uq_user_team_permissions_org_user_team",
        "uq_team_memberships_org_user_team",
    )
    _rename_constraint_if_exists(
        "team_memberships",
        "fk_user_team_permissions_team_org",
        "fk_team_memberships_team_org",
    )
    _rename_constraint_if_exists(
        "team_memberships",
        "user_team_permissions_organization_id_fkey",
        "team_memberships_grantee_organization_id_fkey",
    )
    _rename_constraint_if_exists(
        "team_memberships",
        "user_team_permissions_team_permission_id_fkey",
        "team_memberships_team_id_fkey",
    )
    _rename_constraint_if_exists(
        "team_memberships",
        "user_team_permissions_user_id_fkey",
        "team_memberships_user_id_fkey",
    )
    _rename_constraint_if_exists(
        "team_memberships",
        "user_team_permissions_assigned_by_fkey",
        "team_memberships_assigned_by_fkey",
    )
    _rename_index_if_exists(
        "ix_user_team_permissions_grantee_organization_id",
        "ix_team_memberships_grantee_organization_id",
    )
    _rename_index_if_exists(
        "ix_user_team_permissions_team_id",
        "ix_team_memberships_team_id",
    )
    _rename_index_if_exists(
        "ix_user_team_permissions_user_id",
        "ix_team_memberships_user_id",
    )
    _rename_index_if_exists(
        "ix_user_team_permissions_assigned_by",
        "ix_team_memberships_assigned_by",
    )


def _rename_workflow_permission_constraints() -> None:
    _rename_constraint_if_exists(
        "team_workflow_permissions",
        "workflow_team_permissions_pkey",
        "team_workflow_permissions_pkey",
    )
    _rename_constraint_if_exists(
        "team_workflow_permissions",
        "uq_workflow_team_permissions_org_workflow_team",
        "uq_team_workflow_permissions_org_workflow_team",
    )
    _rename_constraint_if_exists(
        "team_workflow_permissions",
        "fk_workflow_team_permissions_team_org",
        "fk_team_workflow_permissions_team_org",
    )
    _rename_constraint_if_exists(
        "team_workflow_permissions",
        "workflow_team_permissions_organization_id_fkey",
        "team_workflow_permissions_grantee_organization_id_fkey",
    )
    _rename_constraint_if_exists(
        "team_workflow_permissions",
        "workflow_team_permissions_team_permission_id_fkey",
        "team_workflow_permissions_team_id_fkey",
    )
    _rename_constraint_if_exists(
        "team_workflow_permissions",
        "workflow_team_permissions_workflow_id_fkey",
        "team_workflow_permissions_workflow_id_fkey",
    )
    _rename_constraint_if_exists(
        "team_workflow_permissions",
        "workflow_team_permissions_assigned_by_fkey",
        "team_workflow_permissions_assigned_by_fkey",
    )
    _rename_index_if_exists(
        "ix_workflow_team_permissions_grantee_organization_id",
        "ix_team_workflow_permissions_grantee_organization_id",
    )
    _rename_index_if_exists(
        "ix_workflow_team_permissions_team_id",
        "ix_team_workflow_permissions_team_id",
    )
    _rename_index_if_exists(
        "ix_workflow_team_permissions_workflow_id",
        "ix_team_workflow_permissions_workflow_id",
    )
    _rename_index_if_exists(
        "ix_workflow_team_permissions_assigned_by",
        "ix_team_workflow_permissions_assigned_by",
    )


def upgrade() -> None:
    _rename_table_if_exists("team_permission", "teams")
    _rename_table_if_exists("user_team_permissions", "team_memberships")
    _rename_table_if_exists("workflow_team_permissions", "team_workflow_permissions")

    _rename_team_constraints()
    _rename_membership_constraints()
    _rename_workflow_permission_constraints()


def _restore_team_constraints() -> None:
    _rename_constraint_if_exists("team_permission", "teams_pkey", "team_permission_pkey")
    _rename_constraint_if_exists(
        "team_permission",
        "uq_teams_organization_name",
        "uq_permission_team_organization_name",
    )
    _rename_constraint_if_exists(
        "team_permission",
        "uq_teams_id_organization_id",
        "uq_team_permission_id_organization_id",
    )
    _rename_constraint_if_exists(
        "team_permission",
        "teams_organization_id_fkey",
        "team_permission_organization_id_fkey",
    )
    _rename_constraint_if_exists(
        "team_permission",
        "teams_created_by_fkey",
        "team_permission_created_by_fkey",
    )
    _rename_constraint_if_exists(
        "team_permission",
        "teams_managed_by_fkey",
        "team_permission_managed_by_fkey",
    )
    _rename_index_if_exists(
        "ix_teams_organization_id",
        "ix_team_permission_organization_id",
    )
    _rename_index_if_exists("ix_teams_created_by", "ix_team_permission_created_by")
    _rename_index_if_exists("ix_teams_managed_by", "ix_team_permission_managed_by")


def _restore_membership_constraints() -> None:
    _rename_constraint_if_exists(
        "user_team_permissions",
        "team_memberships_pkey",
        "user_team_permissions_pkey",
    )
    _rename_constraint_if_exists(
        "user_team_permissions",
        "uq_team_memberships_org_user_team",
        "uq_user_team_permissions_org_user_team",
    )
    _rename_constraint_if_exists(
        "user_team_permissions",
        "fk_team_memberships_team_org",
        "fk_user_team_permissions_team_org",
    )
    _rename_constraint_if_exists(
        "user_team_permissions",
        "team_memberships_grantee_organization_id_fkey",
        "user_team_permissions_organization_id_fkey",
    )
    _rename_constraint_if_exists(
        "user_team_permissions",
        "team_memberships_team_id_fkey",
        "user_team_permissions_team_permission_id_fkey",
    )
    _rename_constraint_if_exists(
        "user_team_permissions",
        "team_memberships_user_id_fkey",
        "user_team_permissions_user_id_fkey",
    )
    _rename_constraint_if_exists(
        "user_team_permissions",
        "team_memberships_assigned_by_fkey",
        "user_team_permissions_assigned_by_fkey",
    )
    _rename_index_if_exists(
        "ix_team_memberships_grantee_organization_id",
        "ix_user_team_permissions_grantee_organization_id",
    )
    _rename_index_if_exists(
        "ix_team_memberships_team_id",
        "ix_user_team_permissions_team_id",
    )
    _rename_index_if_exists(
        "ix_team_memberships_user_id",
        "ix_user_team_permissions_user_id",
    )
    _rename_index_if_exists(
        "ix_team_memberships_assigned_by",
        "ix_user_team_permissions_assigned_by",
    )


def _restore_workflow_permission_constraints() -> None:
    _rename_constraint_if_exists(
        "workflow_team_permissions",
        "team_workflow_permissions_pkey",
        "workflow_team_permissions_pkey",
    )
    _rename_constraint_if_exists(
        "workflow_team_permissions",
        "uq_team_workflow_permissions_org_workflow_team",
        "uq_workflow_team_permissions_org_workflow_team",
    )
    _rename_constraint_if_exists(
        "workflow_team_permissions",
        "fk_team_workflow_permissions_team_org",
        "fk_workflow_team_permissions_team_org",
    )
    _rename_constraint_if_exists(
        "workflow_team_permissions",
        "team_workflow_permissions_grantee_organization_id_fkey",
        "workflow_team_permissions_organization_id_fkey",
    )
    _rename_constraint_if_exists(
        "workflow_team_permissions",
        "team_workflow_permissions_team_id_fkey",
        "workflow_team_permissions_team_permission_id_fkey",
    )
    _rename_constraint_if_exists(
        "workflow_team_permissions",
        "team_workflow_permissions_workflow_id_fkey",
        "workflow_team_permissions_workflow_id_fkey",
    )
    _rename_constraint_if_exists(
        "workflow_team_permissions",
        "team_workflow_permissions_assigned_by_fkey",
        "workflow_team_permissions_assigned_by_fkey",
    )
    _rename_index_if_exists(
        "ix_team_workflow_permissions_grantee_organization_id",
        "ix_workflow_team_permissions_grantee_organization_id",
    )
    _rename_index_if_exists(
        "ix_team_workflow_permissions_team_id",
        "ix_workflow_team_permissions_team_id",
    )
    _rename_index_if_exists(
        "ix_team_workflow_permissions_workflow_id",
        "ix_workflow_team_permissions_workflow_id",
    )
    _rename_index_if_exists(
        "ix_team_workflow_permissions_assigned_by",
        "ix_workflow_team_permissions_assigned_by",
    )


def downgrade() -> None:
    _rename_table_if_exists("team_workflow_permissions", "workflow_team_permissions")
    _rename_table_if_exists("team_memberships", "user_team_permissions")
    _rename_table_if_exists("teams", "team_permission")

    _restore_team_constraints()
    _restore_membership_constraints()
    _restore_workflow_permission_constraints()
