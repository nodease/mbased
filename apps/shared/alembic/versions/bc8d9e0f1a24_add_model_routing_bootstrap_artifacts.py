"""Add draft-stage model routing bootstrap artifacts.

Revision ID: bc8d9e0f1a24
Revises: bb7c8d9e0f13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "bc8d9e0f1a24"
down_revision: Union[str, Sequence[str], None] = "bb7c8d9e0f13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_node_model_routing_bootstraps",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("task_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("task_description", sa.String(length=4000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("default_model_id", sa.String(length=255), nullable=False),
        sa.Column("fallback_model_id", sa.String(length=255), nullable=True),
        sa.Column("initial_budget_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("planner_model_id", sa.String(length=255), nullable=True),
        sa.Column("planner_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("classifier_artifact", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generation_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stale_reason", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "node_id", "task_fingerprint", name="uq_model_routing_bootstrap_workflow_node_fingerprint"),
    )
    op.create_index("ix_model_routing_bootstrap_workflow_node_status", "llm_node_model_routing_bootstraps", ["workflow_id", "node_id", "status"], unique=False)
    op.create_index("ix_llm_node_model_routing_bootstraps_organization_id", "llm_node_model_routing_bootstraps", ["organization_id"], unique=False)
    op.create_index("ix_llm_node_model_routing_bootstraps_workflow_id", "llm_node_model_routing_bootstraps", ["workflow_id"], unique=False)
    op.create_table(
        "llm_node_model_routing_bootstrap_samples",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("bootstrap_id", sa.UUID(), nullable=False),
        sa.Column("source_node_run_id", sa.UUID(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.String(length=16), nullable=False),
        sa.Column("safe_input_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feature_hash", sa.String(length=64), nullable=False),
        sa.Column("input_length", sa.Integer(), nullable=False),
        sa.Column("knowledge_enabled", sa.Boolean(), nullable=False),
        sa.Column("output_format", sa.String(length=32), nullable=False),
        sa.Column("planner_reason", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bootstrap_id"], ["llm_node_model_routing_bootstraps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_node_run_id"], ["workflow_node_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_routing_bootstrap_sample_bootstrap_ordinal", "llm_node_model_routing_bootstrap_samples", ["bootstrap_id", "ordinal"], unique=False)
    op.add_column("llm_node_model_routing_policies", sa.Column("bootstrap_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_model_routing_policy_bootstrap", "llm_node_model_routing_policies", "llm_node_model_routing_bootstraps", ["bootstrap_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_llm_node_model_routing_policies_bootstrap_id", "llm_node_model_routing_policies", ["bootstrap_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_llm_node_model_routing_policies_bootstrap_id", table_name="llm_node_model_routing_policies")
    op.drop_constraint("fk_model_routing_policy_bootstrap", "llm_node_model_routing_policies", type_="foreignkey")
    op.drop_column("llm_node_model_routing_policies", "bootstrap_id")
    op.drop_index("ix_model_routing_bootstrap_sample_bootstrap_ordinal", table_name="llm_node_model_routing_bootstrap_samples")
    op.drop_table("llm_node_model_routing_bootstrap_samples")
    op.drop_index("ix_llm_node_model_routing_bootstrap_workflow_node_status", table_name="llm_node_model_routing_bootstraps")
    op.drop_index("ix_llm_node_model_routing_bootstraps_workflow_id", table_name="llm_node_model_routing_bootstraps")
    op.drop_index("ix_llm_node_model_routing_bootstraps_organization_id", table_name="llm_node_model_routing_bootstraps")
    op.drop_table("llm_node_model_routing_bootstraps")
