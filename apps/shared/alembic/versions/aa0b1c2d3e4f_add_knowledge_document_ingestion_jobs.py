"""add durable Knowledge document ingestion jobs

Revision ID: aa0b1c2d3e4f
Revises: a9b0c1d2e3f4
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "aa0b1c2d3e4f"
down_revision: str | Sequence[str] | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_document_ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("operation_kind", sa.String(length=32), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("input_revision", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False
        ),
        sa.Column(
            "retryable", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("safe_reason_code", sa.String(length=100), nullable=True),
        sa.Column("owner_token", sa.String(length=128), nullable=True),
        sa.Column("fencing_token", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "dispatch_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "result_document_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation_kind IN ('process', 'sync', 'resume', 'reindex')",
            name="ck_knowledge_document_ingestion_jobs_operation",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retry_scheduled', 'succeeded', "
            "'dead_lettered', 'cancelled')",
            name="ck_knowledge_document_ingestion_jobs_status",
        ),
        sa.CheckConstraint(
            "generation > 0 AND attempt_count >= 0 AND max_attempts > 0",
            name="ck_knowledge_document_ingestion_jobs_attempts",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND owner_token IS NOT NULL "
            "AND fencing_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL) OR "
            "(status <> 'running' AND owner_token IS NULL "
            "AND fencing_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_knowledge_document_ingestion_jobs_lease",
        ),
        sa.CheckConstraint(
            "dispatch_lease_expires_at IS NULL OR "
            "status IN ('pending', 'retry_scheduled')",
            name="ck_knowledge_document_ingestion_jobs_dispatch_lease",
        ),
        sa.CheckConstraint(
            "(status = 'dead_lettered' AND dead_lettered_at IS NOT NULL) OR "
            "(status <> 'dead_lettered' AND dead_lettered_at IS NULL)",
            name="ck_knowledge_document_ingestion_jobs_dead_letter",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organization.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["result_document_version_id"],
            ["document_versions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_knowledge_document_ingestion_jobs_org_idempotency",
        ),
    )
    op.create_index(
        "uq_knowledge_document_ingestion_jobs_active_document",
        "knowledge_document_ingestion_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text(
            "document_id IS NOT NULL AND status IN "
            "('pending', 'running', 'retry_scheduled')"
        ),
    )
    op.create_index(
        "ix_knowledge_document_ingestion_jobs_due",
        "knowledge_document_ingestion_jobs",
        [
            "status",
            "next_retry_at",
            "dispatch_lease_expires_at",
            "requested_at",
        ],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_document_ingestion_jobs_stale_lease",
        "knowledge_document_ingestion_jobs",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_document_ingestion_jobs_org_document_requested",
        "knowledge_document_ingestion_jobs",
        ["organization_id", "document_id", "requested_at"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_document_ingestion_jobs_organization_id",
        "knowledge_document_ingestion_jobs",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_document_ingestion_jobs_knowledge_base_id",
        "knowledge_document_ingestion_jobs",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_document_ingestion_jobs_document_id",
        "knowledge_document_ingestion_jobs",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_document_ingestion_jobs_document_id",
        table_name="knowledge_document_ingestion_jobs",
    )
    op.drop_index(
        "ix_knowledge_document_ingestion_jobs_knowledge_base_id",
        table_name="knowledge_document_ingestion_jobs",
    )
    op.drop_index(
        "ix_knowledge_document_ingestion_jobs_organization_id",
        table_name="knowledge_document_ingestion_jobs",
    )
    op.drop_index(
        "ix_knowledge_document_ingestion_jobs_org_document_requested",
        table_name="knowledge_document_ingestion_jobs",
    )
    op.drop_index(
        "ix_knowledge_document_ingestion_jobs_stale_lease",
        table_name="knowledge_document_ingestion_jobs",
    )
    op.drop_index(
        "ix_knowledge_document_ingestion_jobs_due",
        table_name="knowledge_document_ingestion_jobs",
    )
    op.drop_index(
        "uq_knowledge_document_ingestion_jobs_active_document",
        table_name="knowledge_document_ingestion_jobs",
    )
    op.drop_table("knowledge_document_ingestion_jobs")
