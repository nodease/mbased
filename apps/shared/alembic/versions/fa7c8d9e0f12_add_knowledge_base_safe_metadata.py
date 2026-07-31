"""add knowledge base safe metadata

Revision ID: fa7c8d9e0f12
Revises: fa7b8c9d0e12
Create Date: 2026-07-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fa7c8d9e0f12"
down_revision: Union[str, Sequence[str], None] = "fa7b8c9d0e12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(
        column["name"] == column_name
        for column in inspector.get_columns(table_name)
    )


def upgrade() -> None:
    if not _has_column("knowledge_bases", "safe_metadata"):
        op.add_column(
            "knowledge_bases",
            sa.Column(
                "safe_metadata",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    if _has_column("knowledge_bases", "safe_metadata"):
        op.drop_column("knowledge_bases", "safe_metadata")
