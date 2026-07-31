"""Add deferred model-routing local-learning labels.

Revision ID: e2f3a4b5c6d7
Revises: d1a2b3c4d5e6
Create Date: 2026-07-18 13:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_node_model_routing_learning_labels",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("selected_model_id", sa.String(length=255), nullable=False),
        sa.Column("candidate_model_ids", postgresql.JSONB(), nullable=False),
        sa.Column("feature_vector", postgresql.JSONB(), nullable=False),
        sa.Column("encoder_model_id", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Numeric(8, 6), nullable=True),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outcome_reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["llm_node_model_routing_policies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "policy_id",
            "workflow_run_id",
            "node_id",
            name="uq_model_routing_learning_label_policy_run_node",
        ),
    )
    op.create_index(
        "ix_model_routing_learning_label_policy_status",
        "llm_node_model_routing_learning_labels",
        ["policy_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_routing_learning_label_policy_status",
        table_name="llm_node_model_routing_learning_labels",
    )
    op.drop_table("llm_node_model_routing_learning_labels")
