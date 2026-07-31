"""Add Cost Optimizer experiment tables

Revision ID: f9a0b1c2d3e4
Revises: f8a9b0c1d2e3
Create Date: 2026-07-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"


def _jsonb():
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "cost_optimizer_experiments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("app_id", sa.UUID(), nullable=True),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("baseline_node_run_id", sa.UUID(), nullable=True),
        sa.Column("baseline_workflow_run_id", sa.UUID(), nullable=True),
        sa.Column("baseline_node_options", _jsonb(), nullable=False),
        sa.Column("baseline_usage_summary", _jsonb(), nullable=False),
        sa.Column("baseline_trace_summary", _jsonb(), nullable=False),
        sa.Column("usage_summary", _jsonb(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["baseline_node_run_id"], ["workflow_node_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["baseline_workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_cost_optimizer_experiments_app_id"),
        "cost_optimizer_experiments",
        ["app_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_optimizer_experiments_baseline_node_run_id"),
        "cost_optimizer_experiments",
        ["baseline_node_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_optimizer_experiments_baseline_workflow_run_id"),
        "cost_optimizer_experiments",
        ["baseline_workflow_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_optimizer_experiments_created_by"),
        "cost_optimizer_experiments",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        "ix_cost_optimizer_experiments_lookup",
        "cost_optimizer_experiments",
        ["workflow_id", "node_id", "baseline_node_run_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_optimizer_experiments_node_id"),
        "cost_optimizer_experiments",
        ["node_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_optimizer_experiments_organization_id"),
        "cost_optimizer_experiments",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_cost_optimizer_experiments_user_history",
        "cost_optimizer_experiments",
        ["organization_id", "created_by", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_cost_optimizer_experiments_retention",
        "cost_optimizer_experiments",
        ["retention_expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_optimizer_experiments_workflow_id"),
        "cost_optimizer_experiments",
        ["workflow_id"],
        unique=False,
    )

    op.create_table(
        "cost_optimizer_candidates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("experiment_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("fallback_model_id", sa.String(), nullable=True),
        sa.Column("task_type", sa.String(), nullable=True),
        sa.Column("candidate_settings", _jsonb(), nullable=False),
        sa.Column("candidate_workflow_run_id", sa.UUID(), nullable=True),
        sa.Column("candidate_node_run_id", sa.UUID(), nullable=True),
        sa.Column("total_cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("schema_status", sa.String(), nullable=True),
        sa.Column("downstream_state", sa.String(), nullable=True),
        sa.Column("usage_summary", _jsonb(), nullable=False),
        sa.Column("schema_validation", _jsonb(), nullable=False),
        sa.Column("retrieval_summary", _jsonb(), nullable=True),
        sa.Column("downstream_compatibility", _jsonb(), nullable=False),
        sa.Column("diff_summary", _jsonb(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("is_applied", sa.Boolean(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_by", sa.UUID(), nullable=True),
        sa.Column("applied_llm_node_version_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["applied_by"], ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["applied_llm_node_version_id"],
            ["llm_node_versions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_node_run_id"], ["workflow_node_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["cost_optimizer_experiments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_cost_optimizer_candidates_candidate_node_run_id"),
        "cost_optimizer_candidates",
        ["candidate_node_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_optimizer_candidates_candidate_workflow_run_id"),
        "cost_optimizer_candidates",
        ["candidate_workflow_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_optimizer_candidates_experiment_id"),
        "cost_optimizer_candidates",
        ["experiment_id"],
        unique=False,
    )
    op.create_index(
        "ix_cost_optimizer_candidates_experiment_status",
        "cost_optimizer_candidates",
        ["experiment_id", "status", "is_applied"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cost_optimizer_candidates_model_id"),
        "cost_optimizer_candidates",
        ["model_id"],
        unique=False,
    )
    op.create_index(
        "ix_cost_optimizer_candidates_model_created",
        "cost_optimizer_candidates",
        ["model_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_cost_optimizer_candidates_one_applied",
        "cost_optimizer_candidates",
        ["experiment_id"],
        unique=True,
        postgresql_where=sa.text("is_applied = true"),
    )
    op.add_column(
        "llm_usage_logs",
        sa.Column("cost_optimizer_candidate_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_llm_usage_logs_cost_optimizer_candidate_id",
        "llm_usage_logs",
        "cost_optimizer_candidates",
        ["cost_optimizer_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_llm_usage_logs_cost_optimizer_candidate_id"),
        "llm_usage_logs",
        ["cost_optimizer_candidate_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_llm_usage_logs_cost_optimizer_candidate_id"),
        table_name="llm_usage_logs",
    )
    op.drop_constraint(
        "fk_llm_usage_logs_cost_optimizer_candidate_id",
        "llm_usage_logs",
        type_="foreignkey",
    )
    op.drop_column("llm_usage_logs", "cost_optimizer_candidate_id")

    op.drop_index(
        "uq_cost_optimizer_candidates_one_applied",
        table_name="cost_optimizer_candidates",
    )
    op.drop_index(
        "ix_cost_optimizer_candidates_model_created",
        table_name="cost_optimizer_candidates",
    )
    op.drop_index(
        op.f("ix_cost_optimizer_candidates_model_id"),
        table_name="cost_optimizer_candidates",
    )
    op.drop_index(
        "ix_cost_optimizer_candidates_experiment_status",
        table_name="cost_optimizer_candidates",
    )
    op.drop_index(
        op.f("ix_cost_optimizer_candidates_experiment_id"),
        table_name="cost_optimizer_candidates",
    )
    op.drop_index(
        op.f("ix_cost_optimizer_candidates_candidate_workflow_run_id"),
        table_name="cost_optimizer_candidates",
    )
    op.drop_index(
        op.f("ix_cost_optimizer_candidates_candidate_node_run_id"),
        table_name="cost_optimizer_candidates",
    )
    op.drop_table("cost_optimizer_candidates")

    op.drop_index(
        op.f("ix_cost_optimizer_experiments_workflow_id"),
        table_name="cost_optimizer_experiments",
    )
    op.drop_index(
        "ix_cost_optimizer_experiments_retention",
        table_name="cost_optimizer_experiments",
    )
    op.drop_index(
        "ix_cost_optimizer_experiments_user_history",
        table_name="cost_optimizer_experiments",
    )
    op.drop_index(
        op.f("ix_cost_optimizer_experiments_organization_id"),
        table_name="cost_optimizer_experiments",
    )
    op.drop_index(
        op.f("ix_cost_optimizer_experiments_node_id"),
        table_name="cost_optimizer_experiments",
    )
    op.drop_index(
        "ix_cost_optimizer_experiments_lookup",
        table_name="cost_optimizer_experiments",
    )
    op.drop_index(
        op.f("ix_cost_optimizer_experiments_created_by"),
        table_name="cost_optimizer_experiments",
    )
    op.drop_index(
        op.f("ix_cost_optimizer_experiments_baseline_workflow_run_id"),
        table_name="cost_optimizer_experiments",
    )
    op.drop_index(
        op.f("ix_cost_optimizer_experiments_baseline_node_run_id"),
        table_name="cost_optimizer_experiments",
    )
    op.drop_index(
        op.f("ix_cost_optimizer_experiments_app_id"),
        table_name="cost_optimizer_experiments",
    )
    op.drop_table("cost_optimizer_experiments")
