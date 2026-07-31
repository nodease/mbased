"""Add organization_id to knowledge bases

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-06-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
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


def _has_fk(table_name: str, fk_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        fk["name"] == fk_name
        for fk in inspect(op.get_bind()).get_foreign_keys(table_name)
    )


def upgrade() -> None:
    if not _has_column("knowledge_bases", "organization_id"):
        op.add_column(
            "knowledge_bases",
            sa.Column("organization_id", sa.UUID(), nullable=True),
        )

    if not _has_index("knowledge_bases", "ix_knowledge_bases_organization_id"):
        op.create_index(
            "ix_knowledge_bases_organization_id",
            "knowledge_bases",
            ["organization_id"],
        )

    op.execute(
        sa.text(
            """
            UPDATE knowledge_bases
            SET organization_id = users.organization_id
            FROM users
            WHERE knowledge_bases.user_id = users.id
              AND knowledge_bases.organization_id IS NULL
              AND users.organization_id IS NOT NULL;
            """
        )
    )

    if not _has_fk(
        "knowledge_bases",
        "fk_knowledge_bases_organization_id_organization",
    ):
        op.create_foreign_key(
            "fk_knowledge_bases_organization_id_organization",
            "knowledge_bases",
            "organization",
            ["organization_id"],
            ["id"],
        )


def downgrade() -> None:
    if _has_fk("knowledge_bases", "fk_knowledge_bases_organization_id_organization"):
        op.drop_constraint(
            "fk_knowledge_bases_organization_id_organization",
            "knowledge_bases",
            type_="foreignkey",
        )

    if _has_index("knowledge_bases", "ix_knowledge_bases_organization_id"):
        op.drop_index(
            "ix_knowledge_bases_organization_id",
            table_name="knowledge_bases",
        )

    if _has_column("knowledge_bases", "organization_id"):
        op.drop_column("knowledge_bases", "organization_id")
