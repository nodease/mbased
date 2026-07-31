"""Remove user organization hierarchy and add option columns

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-06-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OPTION_TABLES = (
    "organization",
    "teams",
    "team_memberships",
    "team_workflow_permissions",
    "team_knowledge_permissions",
    "team_llm_permissions",
    "team_audit_permissions",
)


def _inspector():
    return inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        column["name"] == column_name
        for column in _inspector().get_columns(table_name)
    )


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        index["name"] == index_name
        for index in _inspector().get_indexes(table_name)
    )


def _constraint_names(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()

    inspector = _inspector()
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


def _has_constraint(table_name: str, constraint_name: str) -> bool:
    return constraint_name in _constraint_names(table_name)


def _fk_name_for_columns(table_name: str, constrained_columns: list[str]) -> str | None:
    if not _has_table(table_name):
        return None
    for fk in _inspector().get_foreign_keys(table_name):
        if fk.get("constrained_columns") == constrained_columns:
            return fk.get("name")
    return None


def _add_option_columns() -> None:
    for table_name in OPTION_TABLES:
        if _has_table(table_name) and not _has_column(table_name, "option"):
            op.add_column(
                table_name,
                sa.Column("option", sa.String(length=50), nullable=True),
            )


def _drop_option_columns() -> None:
    for table_name in reversed(OPTION_TABLES):
        if _has_column(table_name, "option"):
            op.drop_column(table_name, "option")


def _drop_users_organization_id() -> None:
    fk_name = _fk_name_for_columns("users", ["organization_id"])
    if fk_name:
        op.drop_constraint(fk_name, "users", type_="foreignkey")

    if _has_index("users", "ix_users_organization_id"):
        op.drop_index("ix_users_organization_id", table_name="users")

    if _has_column("users", "organization_id"):
        op.drop_column("users", "organization_id")


def _restore_users_organization_id() -> None:
    if not _has_column("users", "organization_id"):
        op.add_column("users", sa.Column("organization_id", sa.UUID(), nullable=True))

    if not _has_index("users", "ix_users_organization_id"):
        op.create_index("ix_users_organization_id", "users", ["organization_id"])

    if not _fk_name_for_columns("users", ["organization_id"]):
        op.create_foreign_key(
            "fk_users_organization_id_organization",
            "users",
            "organization",
            ["organization_id"],
            ["id"],
        )


def _drop_organization_parent_id() -> None:
    fk_name = _fk_name_for_columns("organization", ["parent_id"])
    if fk_name:
        op.drop_constraint(fk_name, "organization", type_="foreignkey")

    if _has_constraint("organization", "uq_organization_parent_name"):
        op.drop_constraint(
            "uq_organization_parent_name",
            "organization",
            type_="unique",
        )

    if _has_index("organization", "ix_organization_parent_id"):
        op.drop_index("ix_organization_parent_id", table_name="organization")

    if _has_column("organization", "parent_id"):
        op.drop_column("organization", "parent_id")


def _restore_organization_parent_id() -> None:
    if not _has_column("organization", "parent_id"):
        op.add_column("organization", sa.Column("parent_id", sa.UUID(), nullable=True))

    if not _has_index("organization", "ix_organization_parent_id"):
        op.create_index("ix_organization_parent_id", "organization", ["parent_id"])

    if not _fk_name_for_columns("organization", ["parent_id"]):
        op.create_foreign_key(
            "organization_parent_id_fkey",
            "organization",
            "organization",
            ["parent_id"],
            ["id"],
        )

    if not _has_constraint("organization", "uq_organization_parent_name"):
        op.create_unique_constraint(
            "uq_organization_parent_name",
            "organization",
            ["parent_id", "name"],
        )


def _drop_organization_structure() -> None:
    if _has_table("organization_structure"):
        op.drop_table("organization_structure")


def _restore_organization_structure() -> None:
    if _has_table("organization_structure"):
        return

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
    op.create_index(
        "ix_organization_structure_descendant_id",
        "organization_structure",
        ["descendant_id"],
    )


def upgrade() -> None:
    _add_option_columns()
    _drop_organization_structure()
    _drop_users_organization_id()
    _drop_organization_parent_id()


def downgrade() -> None:
    _restore_organization_parent_id()
    _restore_organization_structure()
    _restore_users_organization_id()
    _drop_option_columns()
