"""Add the dormant Conversation Memory persistence foundation.

Revision ID: ab1c2d3e4f50
Revises: aa0b1c2d3e4f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ab1c2d3e4f50"
down_revision: str | Sequence[str] | None = "aa0b1c2d3e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROTECTED_PROJECTION_ENVELOPE_CHECK = (
    "(erased_at IS NOT NULL "
    "AND display_ciphertext IS NULL AND display_key_version IS NULL "
    "AND display_format_version IS NULL AND display_content_digest IS NULL "
    "AND display_plaintext_byte_length IS NULL "
    "AND model_ciphertext IS NULL AND model_key_version IS NULL "
    "AND model_format_version IS NULL AND model_content_digest IS NULL "
    "AND model_plaintext_byte_length IS NULL) OR "
    "(erased_at IS NULL "
    "AND (display_ciphertext IS NOT NULL OR model_ciphertext IS NOT NULL) "
    "AND ((display_ciphertext IS NULL AND display_key_version IS NULL "
    "AND display_format_version IS NULL AND display_content_digest IS NULL "
    "AND display_plaintext_byte_length IS NULL) OR "
    "(display_ciphertext IS NOT NULL AND display_key_version IS NOT NULL "
    "AND display_format_version IS NOT NULL "
    "AND length(display_content_digest) = 64 "
    "AND display_plaintext_byte_length BETWEEN 1 AND 16384)) "
    "AND ((model_ciphertext IS NULL AND model_key_version IS NULL "
    "AND model_format_version IS NULL AND model_content_digest IS NULL "
    "AND model_plaintext_byte_length IS NULL) OR "
    "(model_ciphertext IS NOT NULL AND model_key_version IS NOT NULL "
    "AND model_format_version IS NOT NULL "
    "AND length(model_content_digest) = 64 "
    "AND model_plaintext_byte_length BETWEEN 1 AND 16384)))"
)


def _uuid_primary_key() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        primary_key=True,
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "conversation_sessions",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_version", sa.Integer(), nullable=True),
        sa.Column("deployment_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("mapping_version", sa.String(length=64), nullable=False),
        sa.Column("memory_policy_version", sa.String(length=64), nullable=False),
        sa.Column("memory_contract_version", sa.String(length=64), nullable=False),
        sa.Column(
            "storage_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("audience_kind", sa.String(length=48), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "lifecycle",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "lifecycle_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "content_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "active_turn_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "next_turn_sequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "idle_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "absolute_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "delete_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_conv_sessions_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["app_id"],
            ["apps.id"],
            name="fk_conv_sessions_app",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_conv_sessions_workflow",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["workflow_deployments.id"],
            name="fk_conv_sessions_deployment",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_conv_sessions_id_org",
        ),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            "deployment_id",
            "deployment_version",
            "audience_kind",
            name="uq_conv_sessions_grant_binding",
        ),
        sa.CheckConstraint(
            "(deployment_version IS NOT NULL AND deployment_version > 0) "
            "OR (deployment_snapshot_hash IS NOT NULL "
            "AND length(deployment_snapshot_hash) = 64)",
            name="ck_conv_sessions_binding",
        ),
        sa.CheckConstraint(
            "(audience_kind = 'public_chatbot' "
            "AND subject_type IS NULL AND subject_id IS NULL) OR "
            "(audience_kind = 'authenticated_internal_chatbot' "
            "AND subject_type IS NOT NULL AND subject_id IS NOT NULL)",
            name="ck_conv_sessions_audience_subject",
        ),
        sa.CheckConstraint(
            "lifecycle_revision >= 1 AND content_revision >= 0 "
            "AND next_turn_sequence >= 1 AND storage_generation >= 1",
            name="ck_conv_sessions_revisions",
        ),
        sa.CheckConstraint(
            "(lifecycle = 'active' AND closed_at IS NULL "
            "AND delete_requested_at IS NULL AND deleted_at IS NULL) OR "
            "(lifecycle = 'closed' AND closed_at IS NOT NULL "
            "AND active_turn_id IS NULL AND delete_requested_at IS NULL "
            "AND deleted_at IS NULL) OR "
            "(lifecycle = 'delete_pending' AND active_turn_id IS NULL "
            "AND delete_requested_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(lifecycle = 'deleted' AND active_turn_id IS NULL "
            "AND delete_requested_at IS NOT NULL AND deleted_at IS NOT NULL) OR "
            "(lifecycle = 'expired' AND active_turn_id IS NULL "
            "AND deleted_at IS NULL)",
            name="ck_conv_sessions_lifecycle_fields",
        ),
        sa.CheckConstraint(
            "idle_expires_at < absolute_expires_at",
            name="ck_conv_sessions_expiry_order",
        ),
    )
    op.create_index(
        "ix_conv_sessions_org_lifecycle_expiry",
        "conversation_sessions",
        ["organization_id", "lifecycle", "idle_expires_at"],
    )
    op.create_index(
        "ix_conv_sessions_org_deployment",
        "conversation_sessions",
        ["organization_id", "deployment_id", "deployment_version"],
    )

    op.create_table(
        "conversation_access_grants",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_version", sa.Integer(), nullable=False),
        sa.Column("audience_kind", sa.String(length=48), nullable=False),
        sa.Column("verifier_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "verifier_key_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "replay_record_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            [
                "session_id",
                "organization_id",
                "deployment_id",
                "deployment_version",
                "audience_kind",
            ],
            [
                "conversation_sessions.id",
                "conversation_sessions.organization_id",
                "conversation_sessions.deployment_id",
                "conversation_sessions.deployment_version",
                "conversation_sessions.audience_kind",
            ],
            name="fk_conv_grants_session_binding",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_conv_grants_id_session_org",
        ),
        sa.UniqueConstraint(
            "verifier_key_version",
            "verifier_hash",
            name="uq_conv_grants_verifier",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'transcript_only', 'revoked', 'expired')",
            name="ck_conv_grants_state",
        ),
        sa.CheckConstraint(
            "(state IN ('active', 'transcript_only') AND revoked_at IS NULL) OR "
            "(state = 'revoked' AND revoked_at IS NOT NULL) OR "
            "(state = 'expired' AND expires_at IS NOT NULL)",
            name="ck_conv_grants_state_fields",
        ),
    )
    op.create_index(
        "ix_conv_grants_session_state",
        "conversation_access_grants",
        ["organization_id", "session_id", "state"],
    )
    op.create_index(
        "ix_conv_grants_expiry",
        "conversation_access_grants",
        ["state", "expires_at"],
    )

    op.create_table(
        "conversation_turns",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "started_lifecycle_revision",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "request_idempotency_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "request_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'pending_dispatch'"),
        ),
        sa.Column("user_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "assistant_entry_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("dispatch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "latest_attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "safe_failure_reason",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_conv_turns_session_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_conv_turns_id_session_org",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "session_id",
            "sequence",
            name="uq_conv_turns_session_sequence",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "session_id",
            "request_idempotency_hash",
            name="uq_conv_turns_session_request",
        ),
        sa.CheckConstraint(
            "sequence >= 1 AND version >= 1 AND started_lifecycle_revision >= 1",
            name="ck_conv_turns_versions",
        ),
        sa.CheckConstraint(
            "length(request_idempotency_hash) = 64 "
            "AND length(request_fingerprint) = 64",
            name="ck_conv_turns_request_hashes",
        ),
        sa.CheckConstraint(
            "(status IN ('pending_dispatch', 'queued') "
            "AND assistant_entry_id IS NULL AND execution_id IS NULL "
            "AND latest_attempt_id IS NULL AND safe_failure_reason IS NULL "
            "AND started_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'running' AND assistant_entry_id IS NULL "
            "AND execution_id IS NOT NULL AND latest_attempt_id IS NOT NULL "
            "AND safe_failure_reason IS NULL AND started_at IS NOT NULL "
            "AND completed_at IS NULL) OR "
            "(status = 'completed' AND assistant_entry_id IS NOT NULL "
            "AND execution_id IS NOT NULL AND latest_attempt_id IS NOT NULL "
            "AND safe_failure_reason IS NULL AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL) OR "
            "(status IN ('failed', 'cancelled') "
            "AND assistant_entry_id IS NULL AND safe_failure_reason IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_conv_turns_status_fields",
        ),
    )
    op.create_index(
        "uq_conv_turns_one_active",
        "conversation_turns",
        ["organization_id", "session_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending_dispatch', 'queued', 'running')"),
    )
    op.create_index(
        "ix_conv_turns_session_status_sequence",
        "conversation_turns",
        ["organization_id", "session_id", "status", "sequence"],
    )
    op.create_foreign_key(
        "fk_conv_sessions_active_turn",
        "conversation_sessions",
        "conversation_turns",
        ["active_turn_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_conv_sessions_active_turn_scope",
        "conversation_sessions",
        "conversation_turns",
        ["active_turn_id", "id", "organization_id"],
        ["id", "session_id", "organization_id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "conversation_memory_entries",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("entry_type", sa.String(length=32), nullable=False),
        sa.Column("lifecycle", sa.String(length=24), nullable=False),
        sa.Column("channel", sa.String(length=128), nullable=False),
        sa.Column("producer_node_id", sa.String(length=255), nullable=True),
        sa.Column("display_ciphertext", postgresql.BYTEA(), nullable=True),
        sa.Column("display_key_version", sa.String(length=64), nullable=True),
        sa.Column("display_format_version", sa.String(length=64), nullable=True),
        sa.Column("display_content_digest", sa.String(length=64), nullable=True),
        sa.Column("display_plaintext_byte_length", sa.Integer(), nullable=True),
        sa.Column("model_ciphertext", postgresql.BYTEA(), nullable=True),
        sa.Column("model_key_version", sa.String(length=64), nullable=True),
        sa.Column("model_format_version", sa.String(length=64), nullable=True),
        sa.Column("model_content_digest", sa.String(length=64), nullable=True),
        sa.Column("model_plaintext_byte_length", sa.Integer(), nullable=True),
        sa.Column("content_revision", sa.Integer(), nullable=True),
        sa.Column(
            "idempotency_key_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "sensitivity",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'unclassified'"),
        ),
        sa.Column(
            "privacy_policy_version",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_mem_entries_session_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "session_id", "organization_id"],
            [
                "conversation_turns.id",
                "conversation_turns.session_id",
                "conversation_turns.organization_id",
            ],
            name="fk_mem_entries_turn_session_org",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_mem_entries_id_session_org",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "session_id",
            "sequence",
            name="uq_mem_entries_session_sequence",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "session_id",
            "idempotency_key_hash",
            name="uq_mem_entries_session_idempotency",
        ),
        sa.CheckConstraint(
            "entry_type IN ('user_turn', 'assistant_turn', 'node_projection') "
            "AND lifecycle IN ('provisional', 'approved', 'rejected', 'expired')",
            name="ck_mem_entries_enums",
        ),
        sa.CheckConstraint(
            "sequence >= 1 AND "
            "((lifecycle = 'approved' AND content_revision IS NOT NULL "
            "AND content_revision >= 1) OR "
            "(lifecycle <> 'approved' AND content_revision IS NULL))",
            name="ck_mem_entries_revision",
        ),
        sa.CheckConstraint(
            _PROTECTED_PROJECTION_ENVELOPE_CHECK,
            name="ck_mem_entries_content_envelope",
        ),
    )
    op.create_index(
        "ix_mem_entries_candidates",
        "conversation_memory_entries",
        ["organization_id", "session_id", "channel", "lifecycle", "sequence"],
    )
    op.create_index(
        "ix_mem_entries_expiry",
        "conversation_memory_entries",
        ["expires_at"],
    )

    op.create_table(
        "memory_data_dependencies",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "canonical_resource_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "canonical_resource_version",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "authorization_safe_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("sensitivity", sa.String(length=32), nullable=False),
        sa.Column(
            "authorization_decision_revision",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("resource_revision", sa.String(length=128), nullable=False),
        sa.Column("policy_revision", sa.String(length=128), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_mem_dependencies_organization",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_mem_dependencies_id_org",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_kind",
            "canonical_resource_id",
            "canonical_resource_version",
            "authorization_safe_reference",
            name="uq_mem_dependencies_identity",
        ),
        sa.CheckConstraint(
            "source_kind IN ('knowledge', 'connector', 'tool', 'subworkflow', "
            "'system_policy', 'privacy_policy')",
            name="ck_mem_dependencies_source_kind",
        ),
    )
    op.create_index(
        "ix_mem_dependencies_resource",
        "memory_data_dependencies",
        ["organization_id", "source_kind", "canonical_resource_id"],
    )
    op.create_index(
        "ix_mem_dependencies_auth_revision",
        "memory_data_dependencies",
        ["organization_id", "authorization_decision_revision"],
    )

    op.create_table(
        "conversation_memory_summaries",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=128), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("source_sequence_start", sa.Integer(), nullable=False),
        sa.Column("source_sequence_end", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("summarizer_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("display_ciphertext", postgresql.BYTEA(), nullable=True),
        sa.Column("display_key_version", sa.String(length=64), nullable=True),
        sa.Column("display_format_version", sa.String(length=64), nullable=True),
        sa.Column("display_content_digest", sa.String(length=64), nullable=True),
        sa.Column("display_plaintext_byte_length", sa.Integer(), nullable=True),
        sa.Column("model_ciphertext", postgresql.BYTEA(), nullable=True),
        sa.Column("model_key_version", sa.String(length=64), nullable=True),
        sa.Column("model_format_version", sa.String(length=64), nullable=True),
        sa.Column("model_content_digest", sa.String(length=64), nullable=True),
        sa.Column("model_plaintext_byte_length", sa.Integer(), nullable=True),
        sa.Column(
            "provider_capability_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column("usage_reference", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_mem_summaries_session_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_mem_summaries_id_session_org",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "session_id",
            "channel",
            "source_revision",
            "policy_version",
            "summarizer_version",
            name="uq_mem_summaries_cache",
        ),
        sa.CheckConstraint(
            "status IN ('provisional', 'approved', 'stale', 'rejected', 'erased')",
            name="ck_mem_summaries_status",
        ),
        sa.CheckConstraint(
            "source_sequence_start >= 1 "
            "AND source_sequence_end >= source_sequence_start "
            "AND source_revision >= 0",
            name="ck_mem_summaries_source_range",
        ),
        sa.CheckConstraint(
            _PROTECTED_PROJECTION_ENVELOPE_CHECK,
            name="ck_mem_summaries_content_envelope",
        ),
    )
    op.create_index(
        "ix_mem_summaries_candidates",
        "conversation_memory_summaries",
        ["organization_id", "session_id", "channel", "status", "source_revision"],
    )
    op.create_index(
        "ix_mem_summaries_expiry",
        "conversation_memory_summaries",
        ["expires_at"],
    )

    op.create_table(
        "memory_entry_dependencies",
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "dependency_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entry_id", "session_id", "organization_id"],
            [
                "conversation_memory_entries.id",
                "conversation_memory_entries.session_id",
                "conversation_memory_entries.organization_id",
            ],
            name="fk_mem_entry_deps_entry_session_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dependency_id", "organization_id"],
            [
                "memory_data_dependencies.id",
                "memory_data_dependencies.organization_id",
            ],
            name="fk_mem_entry_deps_dependency_org",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_mem_entry_deps_reverse",
        "memory_entry_dependencies",
        ["organization_id", "dependency_id", "entry_id"],
    )

    op.create_table(
        "memory_summary_dependencies",
        sa.Column(
            "summary_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "dependency_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["summary_id", "session_id", "organization_id"],
            [
                "conversation_memory_summaries.id",
                "conversation_memory_summaries.session_id",
                "conversation_memory_summaries.organization_id",
            ],
            name="fk_mem_summary_deps_summary_session_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dependency_id", "organization_id"],
            [
                "memory_data_dependencies.id",
                "memory_data_dependencies.organization_id",
            ],
            name="fk_mem_summary_deps_dependency_org",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_mem_summary_deps_reverse",
        "memory_summary_dependencies",
        ["organization_id", "dependency_id", "summary_id"],
    )

    op.create_table(
        "memory_turn_dispatch_jobs",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "memory_contract_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("storage_generation", sa.Integer(), nullable=False),
        sa.Column(
            "minimum_worker_capability",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "claim_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("claim_owner", sa.String(length=64), nullable=True),
        sa.Column(
            "claim_deadline_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("broker_message_id", sa.String(length=255), nullable=True),
        sa.Column(
            "workflow_admission_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "safe_failure_reason",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["turn_id", "session_id", "organization_id"],
            [
                "conversation_turns.id",
                "conversation_turns.session_id",
                "conversation_turns.organization_id",
            ],
            name="fk_mem_dispatch_turn_session_org",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "id",
            "turn_id",
            "session_id",
            "organization_id",
            name="uq_mem_dispatch_id_turn_session_org",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "turn_id",
            name="uq_mem_dispatch_turn",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'published', 'acknowledged', "
            "'reconcile_required', 'terminal')",
            name="ck_mem_dispatch_status",
        ),
        sa.CheckConstraint(
            "claim_generation >= 0 AND claim_generation = attempt_count "
            "AND attempt_count >= 0 AND attempt_count <= max_attempts "
            "AND max_attempts > 0 AND storage_generation >= 1",
            name="ck_mem_dispatch_counters",
        ),
        sa.CheckConstraint(
            "(status = 'claimed' AND claim_owner IS NOT NULL "
            "AND claim_deadline_at IS NOT NULL) OR "
            "(status <> 'claimed' AND claim_owner IS NULL "
            "AND claim_deadline_at IS NULL)",
            name="ck_mem_dispatch_claim_fields",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND claim_generation = 0 "
            "AND broker_message_id IS NULL AND published_at IS NULL "
            "AND acknowledged_at IS NULL AND terminal_at IS NULL "
            "AND safe_failure_reason IS NULL) OR "
            "(status = 'claimed' AND claim_generation > 0 "
            "AND broker_message_id IS NULL AND published_at IS NULL "
            "AND acknowledged_at IS NULL AND terminal_at IS NULL "
            "AND safe_failure_reason IS NULL) OR "
            "(status = 'published' AND claim_generation > 0 "
            "AND broker_message_id IS NOT NULL AND published_at IS NOT NULL "
            "AND acknowledged_at IS NULL AND terminal_at IS NULL "
            "AND safe_failure_reason IS NULL) OR "
            "(status = 'acknowledged' AND claim_generation > 0 "
            "AND broker_message_id IS NOT NULL AND published_at IS NOT NULL "
            "AND acknowledged_at IS NOT NULL AND terminal_at IS NULL "
            "AND safe_failure_reason IS NULL) OR "
            "(status = 'reconcile_required' AND claim_generation > 0 "
            "AND safe_failure_reason IS NOT NULL AND terminal_at IS NULL) OR "
            "(status = 'terminal' AND claim_generation > 0 "
            "AND safe_failure_reason IS NOT NULL AND terminal_at IS NOT NULL)",
            name="ck_mem_dispatch_state_fields",
        ),
    )
    op.create_index(
        "ix_mem_dispatch_due",
        "memory_turn_dispatch_jobs",
        ["status", "next_attempt_at", "claim_deadline_at"],
    )

    op.create_table(
        "memory_summary_generation_jobs",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=128), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("summarizer_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "lease_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "provider_capability_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "budget_reservation_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "provider_attempt_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column("summary_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("usage_reference", sa.String(length=128), nullable=True),
        sa.Column(
            "safe_failure_reason",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_mem_summary_jobs_session_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "session_id",
            "channel",
            "source_revision",
            "policy_version",
            "summarizer_version",
            name="uq_mem_summary_jobs_cache",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'lease_acquired', 'budget_reserved', "
            "'provider_started', 'provider_succeeded', 'summary_committed', "
            "'usage_committed', 'reconcile_required', 'failed')",
            name="ck_mem_summary_jobs_status",
        ),
        sa.CheckConstraint(
            "source_revision >= 0 AND lease_generation >= 0 "
            "AND attempt_count >= 0 AND max_attempts > 0",
            name="ck_mem_summary_jobs_counters",
        ),
    )
    op.create_index(
        "ix_mem_summary_jobs_due",
        "memory_summary_generation_jobs",
        ["status", "next_attempt_at", "lease_expires_at"],
    )

    op.create_table(
        "memory_context_plans",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(length=128), nullable=False),
        sa.Column(
            "ordered_references",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("lifecycle_revision", sa.Integer(), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column(
            "authorization_revision_set_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_mem_context_plans_session_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_mem_context_plans_id_session_org",
        ),
        sa.CheckConstraint(
            "lifecycle_revision >= 1 AND content_revision >= 0 "
            "AND source_revision >= 0 AND length(content_digest) = 64 "
            "AND length(authorization_revision_set_digest) = 64",
            name="ck_mem_context_plans_versions",
        ),
    )
    op.create_index(
        "ix_mem_context_plans_expiry",
        "memory_context_plans",
        ["organization_id", "session_id", "expires_at"],
    )

    op.create_table(
        "memory_context_leases",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "node_invocation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("audience_kind", sa.String(length=48), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "provider_capability_reference",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "provider_capability_revision",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column(
            "state",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'issued'"),
        ),
        sa.Column(
            "claim_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "provider_attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "claim_deadline_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["plan_id", "session_id", "organization_id"],
            [
                "memory_context_plans.id",
                "memory_context_plans.session_id",
                "memory_context_plans.organization_id",
            ],
            name="fk_mem_context_leases_plan_session_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_mem_context_leases_id_session_org",
        ),
        sa.CheckConstraint(
            "state IN ('issued', 'claimed', 'invalidated', 'expired') "
            "AND claim_generation >= 0",
            name="ck_mem_context_leases_state",
        ),
        sa.CheckConstraint(
            "(state = 'claimed' AND provider_attempt_id IS NOT NULL "
            "AND claim_deadline_at IS NOT NULL) OR "
            "(state <> 'claimed' AND provider_attempt_id IS NULL)",
            name="ck_mem_context_leases_claim_fields",
        ),
    )
    op.create_index(
        "ix_mem_context_leases_expiry",
        "memory_context_leases",
        ["organization_id", "session_id", "state", "expires_at"],
    )

    op.create_table(
        "memory_context_provider_attempts",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "node_invocation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "provider_capability_reference",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "provider_capability_revision",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column(
            "claim_deadline_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "provider_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "provider_correlation_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column("usage_reference", sa.String(length=128), nullable=True),
        sa.Column(
            "safe_failure_reason",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["lease_id", "session_id", "organization_id"],
            [
                "memory_context_leases.id",
                "memory_context_leases.session_id",
                "memory_context_leases.organization_id",
            ],
            name="fk_mem_provider_attempt_lease_session_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id",
            "lease_id",
            "session_id",
            "organization_id",
            name="uq_mem_provider_attempt_id_lease_org",
        ),
        sa.CheckConstraint(
            "status IN ('claimed', 'provider_started', 'succeeded', 'failed', "
            "'outcome_unknown', 'reconciled') AND version >= 1",
            name="ck_mem_provider_attempt_status",
        ),
        sa.CheckConstraint(
            "(status = 'claimed' AND provider_started_at IS NULL "
            "AND terminal_at IS NULL) OR "
            "(status = 'provider_started' AND provider_started_at IS NOT NULL "
            "AND terminal_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'outcome_unknown', 'reconciled') "
            "AND terminal_at IS NOT NULL)",
            name="ck_mem_provider_attempt_fields",
        ),
    )
    op.create_index(
        "ix_mem_provider_attempt_state",
        "memory_context_provider_attempts",
        ["organization_id", "session_id", "status"],
    )

    op.create_table(
        "conversation_purge_jobs",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "session_reference_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "receipt_verifier_hash",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "receipt_verifier_key_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "receipt_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "claim_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("claim_owner", sa.String(length=64), nullable=True),
        sa.Column(
            "claim_deadline_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "purge_cursor",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "legal_hold_policy_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "backup_erasure_policy_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "safe_failure_reason",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_conv_purge_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["conversation_sessions.id"],
            name="fk_conv_purge_session",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_conv_purge_session_org",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "receipt_verifier_key_version",
            "receipt_verifier_hash",
            name="uq_conv_purge_receipt_verifier",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', "
            "'completed_with_hold', 'retryable_failure', 'terminal_failure')",
            name="ck_conv_purge_status",
        ),
        sa.CheckConstraint(
            "claim_generation >= 0 AND attempt_count >= 0 AND max_attempts > 0",
            name="ck_conv_purge_counters",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND claim_owner IS NOT NULL "
            "AND claim_deadline_at IS NOT NULL) OR "
            "(status <> 'running' AND claim_owner IS NULL)",
            name="ck_conv_purge_claim_fields",
        ),
    )
    op.create_index(
        "ix_conv_purge_due",
        "conversation_purge_jobs",
        ["status", "next_attempt_at", "claim_deadline_at"],
    )
    op.create_index(
        "ix_conv_purge_receipt_expiry",
        "conversation_purge_jobs",
        ["receipt_expires_at"],
    )

    op.create_table(
        "conversation_idempotency_records",
        _uuid_primary_key(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("scope_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "idempotency_key_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "request_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column(
            "resource_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "replay_record_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "secret_replay_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "retention_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("safe_result_code", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_conv_idempotency_organization",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "operation",
            "scope_digest",
            "idempotency_key_hash",
            name="uq_conv_idempotency_scope_key",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed') "
            "AND length(scope_digest) = 64 "
            "AND length(idempotency_key_hash) = 64 "
            "AND length(request_fingerprint) = 64",
            name="ck_conv_idempotency_fields",
        ),
    )
    op.create_index(
        "ix_conv_idempotency_expiry",
        "conversation_idempotency_records",
        ["retention_expires_at"],
    )


def downgrade() -> None:
    op.drop_table("conversation_idempotency_records")
    op.drop_table("conversation_purge_jobs")
    op.drop_table("memory_context_provider_attempts")
    op.drop_table("memory_context_leases")
    op.drop_table("memory_context_plans")
    op.drop_table("memory_summary_generation_jobs")
    op.drop_table("memory_turn_dispatch_jobs")
    op.drop_table("memory_summary_dependencies")
    op.drop_table("memory_entry_dependencies")
    op.drop_table("conversation_memory_summaries")
    op.drop_table("memory_data_dependencies")
    op.drop_table("conversation_memory_entries")
    op.drop_constraint(
        "fk_conv_sessions_active_turn_scope",
        "conversation_sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_conv_sessions_active_turn",
        "conversation_sessions",
        type_="foreignkey",
    )
    op.drop_table("conversation_turns")
    op.drop_table("conversation_access_grants")
    op.drop_table("conversation_sessions")
