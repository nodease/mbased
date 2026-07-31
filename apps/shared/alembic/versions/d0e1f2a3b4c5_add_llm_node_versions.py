"""add llm node versions

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_node_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("fallback_model_id", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("user_prompt", sa.Text(), nullable=True),
        sa.Column("assistant_prompt", sa.Text(), nullable=True),
        sa.Column(
            "referenced_variables",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_bases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("context_variable", sa.Text(), nullable=True),
        sa.Column("score_threshold", sa.Float(), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=True),
        sa.Column(
            "output_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "retrieval_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "tool_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "score_threshold IS NULL OR (score_threshold >= 0 AND score_threshold <= 1)",
            name="ck_llm_node_versions_score_threshold_range",
        ),
        sa.CheckConstraint(
            "top_k IS NULL OR top_k > 0",
            name="ck_llm_node_versions_top_k_positive",
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_llm_node_versions_version_number_positive",
        ),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["app_id", "node_id", "parent_version_id"],
            [
                "llm_node_versions.app_id",
                "llm_node_versions.node_id",
                "llm_node_versions.id",
            ],
            name="fk_llm_node_versions_parent_same_node",
        ),
        sa.ForeignKeyConstraint(
            ["source_workflow_id"], ["workflows.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "app_id",
            "node_id",
            "id",
            name="uq_llm_node_versions_app_node_id",
        ),
        sa.UniqueConstraint(
            "app_id",
            "node_id",
            "version_number",
            name="uq_llm_node_versions_app_node_version",
        ),
    )
    op.create_index(
        "ix_llm_node_versions_app_id",
        "llm_node_versions",
        ["app_id"],
        unique=False,
    )
    op.create_index(
        "ix_llm_node_versions_created_by",
        "llm_node_versions",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        "ix_llm_node_versions_parent_version_id",
        "llm_node_versions",
        ["parent_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_llm_node_versions_source_workflow_id",
        "llm_node_versions",
        ["source_workflow_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_node_versions_source_workflow_id", table_name="llm_node_versions"
    )
    op.drop_index(
        "ix_llm_node_versions_parent_version_id", table_name="llm_node_versions"
    )
    op.drop_index("ix_llm_node_versions_created_by", table_name="llm_node_versions")
    op.drop_index("ix_llm_node_versions_app_id", table_name="llm_node_versions")
    op.drop_table("llm_node_versions")
