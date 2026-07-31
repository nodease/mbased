"""Add persisted LLM node model routing policy lifecycle

Revision ID: fb8c9d0e1f23
Revises: fa7b8c9d0e12
Create Date: 2026-07-10 16:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fb8c9d0e1f23"
down_revision: Union[str, Sequence[str], None] = "fa7b8c9d0e12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the policy lifecycle schema as it existed at this revision.

    Do not import the live ORM models here. Later revisions add bootstrap
    artifacts and their foreign keys, so importing the evolving model makes a
    fresh database reference a table that has not been created yet.
    """
    bind = op.get_bind()
    metadata = sa.MetaData()

    # These tables were created by earlier revisions. Registering their primary
    # keys in this local metadata lets SQLAlchemy compile the historical foreign
    # keys without importing today's ORM models.
    for table_name in (
        "organization",
        "workflows",
        "workflow_deployments",
        "users",
        "llm_usage_logs",
        "workflow_runs",
    ):
        sa.Table(
            table_name,
            metadata,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        )

    policy = sa.Table(
        "llm_node_model_routing_policies",
        metadata,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=True),
        sa.Column("active_policy", postgresql.JSONB(), nullable=False),
        sa.Column("pending_policy", postgresql.JSONB(), nullable=True),
        sa.Column("refresh_every_runs", sa.Integer(), nullable=False),
        sa.Column("eligible_runs_since_last_refresh", sa.Integer(), nullable=False),
        sa.Column("refresh_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_result", sa.String(length=32), nullable=True),
        sa.Column("judge_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organization.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["workflow_deployments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["judge_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "deployment_id",
            "node_id",
            name="uq_model_routing_policy_workflow_deployment_node",
        ),
        sa.Index(
            "ix_model_routing_policy_runtime_lookup",
            "workflow_id",
            "deployment_id",
            "node_id",
            "enabled",
        ),
    )
    updates = sa.Table(
        "llm_node_model_routing_policy_updates",
        metadata,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("eligible_run_count", sa.Integer(), nullable=False),
        sa.Column("excluded_run_count", sa.Integer(), nullable=False),
        sa.Column("excluded_reason_summary", postgresql.JSONB(), nullable=False),
        sa.Column("judge_provider", sa.String(length=64), nullable=True),
        sa.Column("judge_model", sa.String(length=255), nullable=True),
        sa.Column("judge_usage_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("prompt_version", sa.String(length=128), nullable=True),
        sa.Column("input_summary", postgresql.JSONB(), nullable=False),
        sa.Column("output_summary", postgresql.JSONB(), nullable=False),
        sa.Column("new_policy_version", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["llm_node_model_routing_policies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["judge_usage_log_id"], ["llm_usage_logs.id"], ondelete="SET NULL"
        ),
        sa.Index(
            "ix_model_routing_policy_update_policy_created", "policy_id", "created_at"
        ),
    )
    run_events = sa.Table(
        "llm_node_model_routing_policy_run_events",
        metadata,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["llm_node_model_routing_policies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "policy_id", "workflow_run_id", name="uq_model_routing_policy_run_event"
        ),
        sa.Index(
            "ix_model_routing_policy_run_event_policy_created", "policy_id", "created_at"
        ),
    )

    policy.create(bind=bind, checkfirst=True)
    updates.create(bind=bind, checkfirst=True)
    run_events.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    for table_name in (
        "llm_node_model_routing_policy_run_events",
        "llm_node_model_routing_policy_updates",
        "llm_node_model_routing_policies",
    ):
        sa.Table(table_name, metadata).drop(bind=bind, checkfirst=True)
