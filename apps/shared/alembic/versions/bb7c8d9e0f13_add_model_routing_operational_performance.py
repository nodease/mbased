"""Add model routing operational performance aggregates.

Revision ID: bb7c8d9e0f13
Revises: ba6f5c4d3e2f
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "bb7c8d9e0f13"
down_revision: Union[str, Sequence[str], None] = "ba6f5c4d3e2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_node_model_routing_policies",
        sa.Column(
            "performance_checkpoint",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "llm_node_model_routing_performances",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("policy_id", sa.UUID(), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("input_profile", sa.String(length=32), nullable=False),
        sa.Column("run_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "schema_pass_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "schema_eval_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "downstream_success_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "downstream_eval_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("fallback_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "total_cost",
            sa.Numeric(precision=18, scale=9),
            server_default="0",
            nullable=False,
        ),
        sa.Column("total_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "total_latency_ms", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["llm_node_model_routing_policies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "policy_id",
            "model_id",
            "input_profile",
            name="uq_model_routing_performance_policy_model_profile",
        ),
    )
    op.create_index(
        "ix_model_routing_performance_policy_updated",
        "llm_node_model_routing_performances",
        ["policy_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_routing_performance_policy_updated",
        table_name="llm_node_model_routing_performances",
    )
    op.drop_table("llm_node_model_routing_performances")
    op.drop_column(
        "llm_node_model_routing_policies",
        "performance_checkpoint",
    )
