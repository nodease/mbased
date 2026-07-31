"""add knowledge collection sync jobs

Revision ID: a7b8c9d0e1f2
Revises: c1e5f4a3c2d4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "c1e5f4a3c2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_knowledge_collections_id_organization_id",
        "knowledge_collections",
        ["id", "organization_id"],
    )
    op.create_table(
        "knowledge_collection_sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_key_hash", sa.String(length=64), nullable=False),
        sa.Column("target_snapshot_revision", sa.String(length=64), nullable=False),
        sa.Column("previous_sync_state", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("safe_reason_code", sa.String(length=100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="225", nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partially_failed', 'failed', 'cancelled')",
            name="ck_knowledge_collection_sync_jobs_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0",
            name="ck_knowledge_collection_sync_jobs_attempts",
        ),
        sa.CheckConstraint(
            "total_count >= 0 AND completed_count >= 0 AND failed_count >= 0 "
            "AND skipped_count >= 0 "
            "AND completed_count + failed_count + skipped_count <= total_count",
            name="ck_knowledge_collection_sync_jobs_counts",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_knowledge_collection_sync_jobs_lease",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["collection_id", "organization_id"],
            ["knowledge_collections.id", "knowledge_collections.organization_id"],
            name="fk_knowledge_collection_sync_jobs_collection_org",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            "collection_id",
            name="uq_knowledge_collection_sync_jobs_id_org_collection",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "collection_id",
            "request_key_hash",
            name="uq_knowledge_collection_sync_jobs_request",
        ),
    )
    op.create_index(
        "uq_knowledge_collection_sync_jobs_active",
        "knowledge_collection_sync_jobs",
        ["organization_id", "collection_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_knowledge_collection_sync_jobs_due",
        "knowledge_collection_sync_jobs",
        ["status", "next_retry_at"],
    )
    op.create_index(
        "ix_knowledge_collection_sync_jobs_lease",
        "knowledge_collection_sync_jobs",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_knowledge_collection_sync_jobs_retention",
        "knowledge_collection_sync_jobs",
        ["status", "completed_at"],
    )
    op.create_index(
        "ix_knowledge_collection_sync_jobs_org_collection_requested",
        "knowledge_collection_sync_jobs",
        ["organization_id", "collection_id", "requested_at"],
    )

    op.create_table(
        "knowledge_collection_sync_job_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("target_revision", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("safe_reason_code", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')",
            name="ck_knowledge_collection_sync_job_items_status",
        ),
        sa.CheckConstraint(
            "position >= 0 AND attempt_count >= 0 AND max_attempts > 0",
            name="ck_knowledge_collection_sync_job_items_counters",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "organization_id", "collection_id"],
            [
                "knowledge_collection_sync_jobs.id",
                "knowledge_collection_sync_jobs.organization_id",
                "knowledge_collection_sync_jobs.collection_id",
            ],
            name="fk_knowledge_collection_sync_job_items_job_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "document_id",
            name="uq_knowledge_collection_sync_job_items_document",
        ),
        sa.UniqueConstraint(
            "job_id",
            "position",
            name="uq_knowledge_collection_sync_job_items_position",
        ),
    )
    op.create_index(
        "ix_knowledge_collection_sync_job_items_job_status_position",
        "knowledge_collection_sync_job_items",
        ["job_id", "status", "position"],
    )
    op.create_index(
        "ix_knowledge_collection_sync_job_items_kb_org",
        "knowledge_collection_sync_job_items",
        ["knowledge_base_id", "organization_id"],
    )
    op.create_index(
        "ix_knowledge_collection_sync_job_items_document",
        "knowledge_collection_sync_job_items",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_collection_sync_job_items_document",
        table_name="knowledge_collection_sync_job_items",
    )
    op.drop_index(
        "ix_knowledge_collection_sync_job_items_kb_org",
        table_name="knowledge_collection_sync_job_items",
    )
    op.drop_index(
        "ix_knowledge_collection_sync_job_items_job_status_position",
        table_name="knowledge_collection_sync_job_items",
    )
    op.drop_table("knowledge_collection_sync_job_items")

    op.drop_index(
        "ix_knowledge_collection_sync_jobs_org_collection_requested",
        table_name="knowledge_collection_sync_jobs",
    )
    op.drop_index(
        "ix_knowledge_collection_sync_jobs_retention",
        table_name="knowledge_collection_sync_jobs",
    )
    op.drop_index(
        "ix_knowledge_collection_sync_jobs_lease",
        table_name="knowledge_collection_sync_jobs",
    )
    op.drop_index(
        "ix_knowledge_collection_sync_jobs_due",
        table_name="knowledge_collection_sync_jobs",
    )
    op.drop_index(
        "uq_knowledge_collection_sync_jobs_active",
        table_name="knowledge_collection_sync_jobs",
    )
    op.drop_table("knowledge_collection_sync_jobs")
    op.drop_constraint(
        "uq_knowledge_collections_id_organization_id",
        "knowledge_collections",
        type_="unique",
    )
