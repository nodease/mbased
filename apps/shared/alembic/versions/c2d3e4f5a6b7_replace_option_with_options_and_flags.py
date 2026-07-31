"""Replace option columns with JSONB options and flags

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-06-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OPTION_TABLES = (
    ("organization", "ck_organization_flags_nonnegative"),
    ("teams", "ck_teams_flags_nonnegative"),
    ("team_memberships", "ck_team_memberships_flags_nonnegative"),
    ("team_workflow_permissions", "ck_team_workflow_permissions_flags_nonnegative"),
    ("team_knowledge_permissions", "ck_team_knowledge_permissions_flags_nonnegative"),
    ("team_llm_permissions", "ck_team_llm_permissions_flags_nonnegative"),
    ("team_audit_permissions", "ck_team_audit_permissions_flags_nonnegative"),
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


def _has_check_constraint(table_name: str, constraint_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        constraint["name"] == constraint_name
        for constraint in _inspector().get_check_constraints(table_name)
    )


def upgrade() -> None:
    for table_name, check_name in OPTION_TABLES:
        if _has_column(table_name, "option"):
            op.drop_column(table_name, "option")

        if not _has_column(table_name, "options"):
            op.add_column(
                table_name,
                sa.Column(
                    "options",
                    postgresql.JSONB(),
                    nullable=False,
                    server_default=sa.text("'{}'::jsonb"),
                ),
            )

        if not _has_column(table_name, "flags"):
            op.add_column(
                table_name,
                sa.Column(
                    "flags",
                    sa.BigInteger(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
            )

        if not _has_check_constraint(table_name, check_name):
            op.create_check_constraint(check_name, table_name, "flags >= 0")


def downgrade() -> None:
    for table_name, check_name in reversed(OPTION_TABLES):
        if _has_check_constraint(table_name, check_name):
            op.drop_constraint(check_name, table_name, type_="check")

        if _has_column(table_name, "flags"):
            op.drop_column(table_name, "flags")

        if _has_column(table_name, "options"):
            op.drop_column(table_name, "options")

        if not _has_column(table_name, "option"):
            op.add_column(
                table_name,
                sa.Column("option", sa.String(length=50), nullable=True),
            )
