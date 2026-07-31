"""Rename LLM usage latency column

Revision ID: f8a9b0c1d2e3
Revises: f2a3b4c5d6e7
Create Date: 2026-06-27 21:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_MISSING_L_LATENCY_COLUMN = "a" + "tency_ms"


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(
        column["name"] == column_name
        for column in inspector.get_columns(table_name)
    )


def upgrade() -> None:
    if _has_column(
        "llm_usage_logs",
        LEGACY_MISSING_L_LATENCY_COLUMN,
    ) and not _has_column("llm_usage_logs", "latency_ms"):
        op.alter_column(
            "llm_usage_logs",
            LEGACY_MISSING_L_LATENCY_COLUMN,
            new_column_name="latency_ms",
            existing_type=sa.Integer(),
            existing_nullable=False,
        )
        return

    if not _has_column("llm_usage_logs", "latency_ms"):
        op.add_column(
            "llm_usage_logs",
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        )
        op.alter_column("llm_usage_logs", "latency_ms", server_default=None)


def downgrade() -> None:
    if _has_column("llm_usage_logs", "latency_ms") and not _has_column(
        "llm_usage_logs",
        LEGACY_MISSING_L_LATENCY_COLUMN,
    ):
        op.alter_column(
            "llm_usage_logs",
            "latency_ms",
            new_column_name=LEGACY_MISSING_L_LATENCY_COLUMN,
            existing_type=sa.Integer(),
            existing_nullable=False,
        )
