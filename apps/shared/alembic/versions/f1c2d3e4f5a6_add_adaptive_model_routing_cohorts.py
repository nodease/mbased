"""Add the retired input-cohort routing tables.

Revision ID: f1c2d3e4f5a6
Revises: 0f4a5b6c7d89
Create Date: 2026-07-14 12:00:00.000000

This historical revision intentionally owns its old table definitions locally.
The application no longer imports the retired cohort ORM models, but a fresh
Alembic upgrade must still be able to replay this revision before the later
cleanup migration removes the tables again.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "0f4a5b6c7d89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _create_legacy_tables(bind) -> None:
    """Create the pre-cleanup schema without importing retired ORM models."""
    metadata = sa.MetaData()
    # Referenced tables were created by earlier revisions. Keep only their
    # primary keys in this migration-local metadata so the historical foreign
    # keys compile without depending on live ORM models.
    for table_name in (
        "llm_node_model_routing_policies",
        "llm_node_model_routing_policy_updates",
        "workflow_runs",
        "workflow_node_runs",
        "llm_usage_logs",
        "cost_optimizer_candidates",
    ):
        sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.UUID(), primary_key=True),
        )

    cohorts = sa.Table(
        "llm_node_model_routing_cohorts",
        metadata,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "policy_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cohort_key", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("label_en", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("safety_protected", sa.Boolean(), nullable=False),
        sa.Column("encoder_model_id", sa.String(length=255), nullable=False),
        sa.Column("centroid_embedding", postgresql.JSONB(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("review_window_count", sa.Integer(), nullable=False),
        sa.Column("low_share_streak", sa.Integer(), nullable=False),
        sa.Column("last_traffic_share", sa.Numeric(10, 6), nullable=False),
        sa.Column("node_config_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dormant_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_id", "cohort_key", name="uq_model_routing_cohort_key"),
    )
    examples = sa.Table(
        "llm_node_model_routing_cohort_examples",
        metadata,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "cohort_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_cohorts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("synthetic_text", sa.String(length=2000), nullable=False),
        sa.Column("embedding", postgresql.JSONB(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cohort_id", "ordinal", name="uq_model_routing_cohort_example_order"),
    )
    observations = sa.Table(
        "llm_node_model_routing_observations",
        metadata,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "policy_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_run_id",
            sa.UUID(),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_node_run_id",
            sa.UUID(),
            sa.ForeignKey("workflow_node_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("review_window_key", sa.String(length=128), nullable=False),
        sa.Column("encoder_model_id", sa.String(length=255), nullable=False),
        sa.Column("embedding", postgresql.JSONB(), nullable=False),
        sa.Column(
            "matched_cohort_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_cohorts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("match_status", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "policy_id",
            "workflow_node_run_id",
            name="uq_model_routing_observation_policy_node_run",
        ),
    )
    evidence = sa.Table(
        "llm_node_model_routing_model_evidence",
        metadata,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "cohort_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_cohorts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("node_config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("evidence_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("quality_summary", postgresql.JSONB(), nullable=False),
        sa.Column("efficiency_summary", postgresql.JSONB(), nullable=False),
        sa.Column("source_candidate_ids", postgresql.JSONB(), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "cohort_id",
            "model_id",
            "node_config_fingerprint",
            name="uq_model_routing_evidence_cohort_model_fingerprint",
        ),
    )
    batches = sa.Table(
        "llm_node_model_routing_validation_batches",
        metadata,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "policy_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_update_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_policy_updates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("candidate_plan", postgresql.JSONB(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("completed_items", sa.Integer(), nullable=False),
        sa.Column("reserved_cost", sa.Numeric(12, 6), nullable=False),
        sa.Column("spent_cost", sa.Numeric(12, 6), nullable=False),
        sa.Column("error_summary", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("request_fingerprint", name="uq_model_routing_validation_batch_request"),
    )
    budgets = sa.Table(
        "llm_node_model_routing_validation_budget_months",
        metadata,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "policy_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("limit_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("spent_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_id", "month_start", name="uq_model_routing_budget_month"),
    )
    items = sa.Table(
        "llm_node_model_routing_validation_items",
        metadata,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_validation_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cohort_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_cohorts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "observation_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("baseline_model_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "candidate_workflow_run_id",
            sa.UUID(),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "candidate_node_run_id",
            sa.UUID(),
            sa.ForeignKey("workflow_node_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("execution_summary", postgresql.JSONB(), nullable=False),
        sa.Column("quality_summary", postgresql.JSONB(), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "batch_id",
            "cohort_id",
            "observation_id",
            "model_id",
            name="uq_model_routing_validation_item_request",
        ),
    )
    costs = sa.Table(
        "llm_node_model_routing_validation_cost_events",
        metadata,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.UUID(),
            sa.ForeignKey("llm_node_model_routing_validation_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("amount_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column(
            "usage_log_id",
            sa.UUID(),
            sa.ForeignKey("llm_usage_logs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "candidate_id",
            sa.UUID(),
            sa.ForeignKey("cost_optimizer_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table in (cohorts, examples, observations, evidence, batches, budgets, items, costs):
        table.create(bind=bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()
    _create_legacy_tables(bind)

    observation_columns = _column_names("llm_node_model_routing_observations")
    if "review_window_key" not in observation_columns:
        op.add_column(
            "llm_node_model_routing_observations",
            sa.Column(
                "review_window_key",
                sa.String(length=128),
                nullable=False,
                server_default=sa.text("'legacy'"),
            ),
        )

    policy_columns = _column_names("llm_node_model_routing_policies")
    if "execution_subject_user_id" not in policy_columns:
        op.add_column(
            "llm_node_model_routing_policies",
            sa.Column("execution_subject_user_id", sa.UUID(), nullable=True),
        )
        op.create_foreign_key(
            "fk_model_routing_policy_execution_subject_user",
            "llm_node_model_routing_policies",
            "users",
            ["execution_subject_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "validation_budget_usd" not in policy_columns:
        op.add_column(
            "llm_node_model_routing_policies",
            sa.Column(
                "validation_budget_usd",
                sa.Numeric(12, 6),
                nullable=False,
                server_default=sa.text("3"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    policy_columns = _column_names("llm_node_model_routing_policies")
    if "validation_budget_usd" in policy_columns:
        op.drop_column("llm_node_model_routing_policies", "validation_budget_usd")
    if "execution_subject_user_id" in policy_columns:
        op.drop_constraint(
            "fk_model_routing_policy_execution_subject_user",
            "llm_node_model_routing_policies",
            type_="foreignkey",
        )
        op.drop_column("llm_node_model_routing_policies", "execution_subject_user_id")

    for table_name in (
        "llm_node_model_routing_validation_cost_events",
        "llm_node_model_routing_validation_items",
        "llm_node_model_routing_validation_budget_months",
        "llm_node_model_routing_validation_batches",
        "llm_node_model_routing_model_evidence",
        "llm_node_model_routing_observations",
        "llm_node_model_routing_cohort_examples",
        "llm_node_model_routing_cohorts",
    ):
        if sa.inspect(bind).has_table(table_name):
            op.drop_table(table_name)
