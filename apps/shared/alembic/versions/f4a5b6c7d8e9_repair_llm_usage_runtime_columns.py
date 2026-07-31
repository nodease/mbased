"""Repair missing LLM usage runtime provenance columns.

Revision ID: f4a5b6c7d8e9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-18 15:15:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "llm_usage_logs"


def _column_names() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(TABLE_NAME)
    }


def _constraint_names() -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(TABLE_NAME)
        if constraint.get("name")
    }


def _index_names() -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(TABLE_NAME)
        if index.get("name")
    }


def upgrade() -> None:
    columns = _column_names()
    additions = (
        ("runtime_surface", sa.String(length=64)),
        ("runtime_session_id", postgresql.UUID(as_uuid=True)),
        ("runtime_request_id", postgresql.UUID(as_uuid=True)),
        ("runtime_attempt", sa.Integer()),
    )
    for name, column_type in additions:
        if name not in columns:
            op.add_column(TABLE_NAME, sa.Column(name, column_type, nullable=True))

    constraints = _constraint_names()
    if "ck_llm_usage_logs_runtime_attempt_positive" not in constraints:
        op.create_check_constraint(
            "ck_llm_usage_logs_runtime_attempt_positive",
            TABLE_NAME,
            "runtime_attempt IS NULL OR runtime_attempt > 0",
        )
    if "ck_llm_usage_logs_agent_builder_runtime_identity" not in constraints:
        op.create_check_constraint(
            "ck_llm_usage_logs_agent_builder_runtime_identity",
            TABLE_NAME,
            "runtime_surface <> 'agent_builder_intent' OR "
            "(runtime_session_id IS NOT NULL AND runtime_request_id IS NOT NULL "
            "AND runtime_attempt IS NOT NULL)",
        )
    if "ck_llm_usage_logs_agent_builder_billing_facts" not in constraints:
        op.create_check_constraint(
            "ck_llm_usage_logs_agent_builder_billing_facts",
            TABLE_NAME,
            "runtime_surface <> 'agent_builder_intent' OR "
            "(prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND total_cost IS NOT NULL AND total_cost >= 0 AND latency_ms >= 0)",
        )

    indexes = _index_names()
    if "uq_llm_usage_logs_agent_builder_attempt" not in indexes:
        op.create_index(
            "uq_llm_usage_logs_agent_builder_attempt",
            TABLE_NAME,
            [
                "runtime_surface",
                "runtime_session_id",
                "runtime_request_id",
                "runtime_attempt",
            ],
            unique=True,
            postgresql_where=sa.text("runtime_surface = 'agent_builder_intent'"),
        )
    if "ix_llm_usage_logs_org_surface_created" not in indexes:
        op.create_index(
            "ix_llm_usage_logs_org_surface_created",
            TABLE_NAME,
            ["organization_id", "runtime_surface", "created_at"],
        )


def downgrade() -> None:
    # The original Agent Builder migration owns these schema objects. Keep them
    # when downgrading this repair so the preceding schema remains valid.
    pass
