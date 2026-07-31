"""Add controls introduced after the adaptive cohort base migration.

Revision ID: a6f4d2c8e1b7
Revises: f1c2d3e4f5a6
Create Date: 2026-07-14 19:00:00.000000

`f1c2d3e4f5a6` was already applied to local developer databases before the
cohort-capacity and source fields were introduced. This additive revision is
intentionally idempotent: fresh databases receive the columns from the base
table definition, and existing databases receive only what is absent.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a6f4d2c8e1b7"
down_revision: Union[str, Sequence[str], None] = "f1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    policy_columns = _column_names("llm_node_model_routing_policies")
    if "max_cohorts" not in policy_columns:
        op.add_column(
            "llm_node_model_routing_policies",
            sa.Column(
                "max_cohorts",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("6"),
            ),
        )

    cohort_columns = _column_names("llm_node_model_routing_cohorts")
    if "source" not in cohort_columns:
        op.add_column(
            "llm_node_model_routing_cohorts",
            sa.Column(
                "source",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'auto'"),
            ),
        )


def downgrade() -> None:
    cohort_columns = _column_names("llm_node_model_routing_cohorts")
    if "source" in cohort_columns:
        op.drop_column("llm_node_model_routing_cohorts", "source")

    policy_columns = _column_names("llm_node_model_routing_policies")
    if "max_cohorts" in policy_columns:
        op.drop_column("llm_node_model_routing_policies", "max_cohorts")
