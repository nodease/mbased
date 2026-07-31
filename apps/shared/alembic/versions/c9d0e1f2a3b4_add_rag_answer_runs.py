"""add rag answer runs

Revision ID: c9d0e1f2a3b4
Revises: b2c3d4e5f6a7
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rag_answer_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "actor_user_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "knowledge_base_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("correlation_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("query_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "retrieval_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "citation_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "answer_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("answer_hash", sa.String(length=128), nullable=True),
        sa.Column("hash_version", sa.String(length=64), nullable=True),
        sa.Column(
            "policy_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("generation_model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "generation_model_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "generation_credential_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "generation_credential_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "usage_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('requested', 'running', 'completed', 'failed', 'cancelled', 'blocked')",
            name="ck_rag_answer_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["generation_credential_id"], ["llm_credentials.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["generation_model_id"], ["llm_models.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rag_answer_runs_organization_id",
        "rag_answer_runs",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_rag_answer_runs_user_id",
        "rag_answer_runs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_rag_answer_runs_knowledge_base_id",
        "rag_answer_runs",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_index(
        "ix_rag_answer_runs_generation_model_id",
        "rag_answer_runs",
        ["generation_model_id"],
        unique=False,
    )
    op.create_index(
        "ix_rag_answer_runs_generation_credential_id",
        "rag_answer_runs",
        ["generation_credential_id"],
        unique=False,
    )
    op.create_index(
        "ix_rag_answer_runs_org_correlation_created",
        "rag_answer_runs",
        ["organization_id", "correlation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_rag_answer_runs_retention_expires_at",
        "rag_answer_runs",
        ["retention_expires_at"],
        unique=False,
    )
    for column in (
        "retrieval_summary",
        "citation_summary",
        "answer_summary",
        "policy_result",
        "usage_summary",
    ):
        op.alter_column("rag_answer_runs", column, server_default=None)


def downgrade() -> None:
    op.drop_index("ix_rag_answer_runs_retention_expires_at", table_name="rag_answer_runs")
    op.drop_index(
        "ix_rag_answer_runs_org_correlation_created",
        table_name="rag_answer_runs",
    )
    op.drop_index(
        "ix_rag_answer_runs_generation_credential_id",
        table_name="rag_answer_runs",
    )
    op.drop_index("ix_rag_answer_runs_generation_model_id", table_name="rag_answer_runs")
    op.drop_index("ix_rag_answer_runs_knowledge_base_id", table_name="rag_answer_runs")
    op.drop_index("ix_rag_answer_runs_user_id", table_name="rag_answer_runs")
    op.drop_index("ix_rag_answer_runs_organization_id", table_name="rag_answer_runs")
    op.drop_table("rag_answer_runs")
