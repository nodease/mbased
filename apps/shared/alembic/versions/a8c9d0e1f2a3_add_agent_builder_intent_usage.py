"""add Agent Builder intent usage provenance

Revision ID: a8c9d0e1f2a3
Revises: aa0b1c2d3e4f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "aa0b1c2d3e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "llm_usage_logs",
        "credential_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "llm_usage_logs",
        "model_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.drop_constraint(
        "llm_usage_logs_credential_id_fkey",
        "llm_usage_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "llm_usage_logs_credential_id_fkey",
        "llm_usage_logs",
        "llm_credentials",
        ["credential_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "llm_usage_logs_model_id_fkey",
        "llm_usage_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "llm_usage_logs_model_id_fkey",
        "llm_usage_logs",
        "llm_models",
        ["model_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "llm_usage_logs",
        sa.Column("runtime_surface", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "llm_usage_logs",
        sa.Column(
            "runtime_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "llm_usage_logs",
        sa.Column(
            "runtime_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "llm_usage_logs",
        sa.Column("runtime_attempt", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_llm_usage_logs_runtime_attempt_positive",
        "llm_usage_logs",
        "runtime_attempt IS NULL OR runtime_attempt > 0",
    )
    op.create_check_constraint(
        "ck_llm_usage_logs_agent_builder_runtime_identity",
        "llm_usage_logs",
        "runtime_surface <> 'agent_builder_intent' OR "
        "(runtime_session_id IS NOT NULL AND runtime_request_id IS NOT NULL "
        "AND runtime_attempt IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_llm_usage_logs_agent_builder_billing_facts",
        "llm_usage_logs",
        "runtime_surface <> 'agent_builder_intent' OR "
        "(prompt_tokens >= 0 AND completion_tokens >= 0 "
        "AND total_cost IS NOT NULL AND total_cost >= 0 AND latency_ms >= 0)",
    )
    op.create_index(
        "uq_llm_usage_logs_agent_builder_attempt",
        "llm_usage_logs",
        [
            "runtime_surface",
            "runtime_session_id",
            "runtime_request_id",
            "runtime_attempt",
        ],
        unique=True,
        postgresql_where=sa.text("runtime_surface = 'agent_builder_intent'"),
    )
    op.create_index(
        "ix_llm_usage_logs_org_surface_created",
        "llm_usage_logs",
        ["organization_id", "runtime_surface", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE llm_usage_logs IN ACCESS EXCLUSIVE MODE"))
    agent_builder_usage_count = bind.execute(
        sa.text(
            "SELECT count(*) FROM llm_usage_logs "
            "WHERE runtime_surface = 'agent_builder_intent'"
        )
    ).scalar_one()
    if agent_builder_usage_count:
        raise RuntimeError(
            "cannot downgrade Agent Builder usage migration while Agent Builder "
            "usage rows exist; preserving runtime provenance requires the current schema"
        )
    null_reference_count = bind.execute(
        sa.text(
            "SELECT count(*) FROM llm_usage_logs "
            "WHERE credential_id IS NULL OR model_id IS NULL"
        )
    ).scalar_one()
    if null_reference_count:
        raise RuntimeError(
            "cannot downgrade Agent Builder usage migration while preserved "
            "usage rows have deleted model or credential references"
        )

    op.drop_index(
        "ix_llm_usage_logs_org_surface_created",
        table_name="llm_usage_logs",
    )
    op.drop_index(
        "uq_llm_usage_logs_agent_builder_attempt",
        table_name="llm_usage_logs",
        postgresql_where=sa.text("runtime_surface = 'agent_builder_intent'"),
    )
    op.drop_constraint(
        "ck_llm_usage_logs_agent_builder_billing_facts",
        "llm_usage_logs",
        type_="check",
    )
    op.drop_constraint(
        "ck_llm_usage_logs_agent_builder_runtime_identity",
        "llm_usage_logs",
        type_="check",
    )
    op.drop_constraint(
        "ck_llm_usage_logs_runtime_attempt_positive",
        "llm_usage_logs",
        type_="check",
    )
    op.drop_column("llm_usage_logs", "runtime_attempt")
    op.drop_column("llm_usage_logs", "runtime_request_id")
    op.drop_column("llm_usage_logs", "runtime_session_id")
    op.drop_column("llm_usage_logs", "runtime_surface")
    op.drop_constraint(
        "llm_usage_logs_model_id_fkey",
        "llm_usage_logs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "llm_usage_logs_credential_id_fkey",
        "llm_usage_logs",
        type_="foreignkey",
    )
    op.alter_column(
        "llm_usage_logs",
        "model_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "llm_usage_logs",
        "credential_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        "llm_usage_logs_model_id_fkey",
        "llm_usage_logs",
        "llm_models",
        ["model_id"],
        ["id"],
    )
    op.create_foreign_key(
        "llm_usage_logs_credential_id_fkey",
        "llm_usage_logs",
        "llm_credentials",
        ["credential_id"],
        ["id"],
    )
