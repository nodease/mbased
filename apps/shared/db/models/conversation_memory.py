from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from apps.shared.db.base import Base
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


class _TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=text("now()"),
    )


class ConversationSessionRecord(_TimestampMixin, Base):
    __tablename__ = "conversation_sessions"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_conv_sessions_id_org",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            "deployment_id",
            "deployment_version",
            "audience_kind",
            name="uq_conv_sessions_grant_binding",
        ),
        ForeignKeyConstraint(
            ["active_turn_id", "id", "organization_id"],
            [
                "conversation_turns.id",
                "conversation_turns.session_id",
                "conversation_turns.organization_id",
            ],
            name="fk_conv_sessions_active_turn_scope",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "(deployment_version IS NOT NULL AND deployment_version > 0) "
            "OR (deployment_snapshot_hash IS NOT NULL "
            "AND length(deployment_snapshot_hash) = 64)",
            name="ck_conv_sessions_binding",
        ),
        CheckConstraint(
            "(audience_kind = 'public_chatbot' "
            "AND subject_type IS NULL AND subject_id IS NULL) OR "
            "(audience_kind = 'authenticated_internal_chatbot' "
            "AND subject_type IS NOT NULL AND subject_id IS NOT NULL)",
            name="ck_conv_sessions_audience_subject",
        ),
        CheckConstraint(
            "lifecycle_revision >= 1 AND content_revision >= 0 "
            "AND next_turn_sequence >= 1 AND storage_generation >= 1",
            name="ck_conv_sessions_revisions",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "idle_expires_at < absolute_expires_at",
            name="ck_conv_sessions_expiry_order",
        ),
        Index(
            "ix_conv_sessions_org_lifecycle_expiry",
            "organization_id",
            "lifecycle",
            "idle_expires_at",
        ),
        Index(
            "ix_conv_sessions_org_deployment",
            "organization_id",
            "deployment_id",
            "deployment_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    app_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="RESTRICT"),
        nullable=False,
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    deployment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deployment_snapshot_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    mapping_version: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    storage_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    audience_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    lifecycle: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    lifecycle_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    content_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    active_turn_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "conversation_turns.id",
            name="fk_conv_sessions_active_turn",
            ondelete="SET NULL",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    next_turn_sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    delete_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ConversationAccessGrantRecord(_TimestampMixin, Base):
    __tablename__ = "conversation_access_grants"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_conv_grants_id_session_org",
        ),
        UniqueConstraint(
            "verifier_key_version",
            "verifier_hash",
            name="uq_conv_grants_verifier",
        ),
        ForeignKeyConstraint(
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
        CheckConstraint(
            "state IN ('active', 'transcript_only', 'revoked', 'expired')",
            name="ck_conv_grants_state",
        ),
        CheckConstraint(
            "(state IN ('active', 'transcript_only') AND revoked_at IS NULL) OR "
            "(state = 'revoked' AND revoked_at IS NOT NULL) OR "
            "(state = 'expired' AND expires_at IS NOT NULL)",
            name="ck_conv_grants_state_fields",
        ),
        Index(
            "ix_conv_grants_session_state",
            "organization_id",
            "session_id",
            "state",
        ),
        Index("ix_conv_grants_expiry", "state", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    deployment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    audience_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    verifier_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    verifier_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    replay_record_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ConversationTurnRecord(_TimestampMixin, Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_conv_turns_id_session_org",
        ),
        UniqueConstraint(
            "organization_id",
            "session_id",
            "sequence",
            name="uq_conv_turns_session_sequence",
        ),
        UniqueConstraint(
            "organization_id",
            "session_id",
            "request_idempotency_hash",
            name="uq_conv_turns_session_request",
        ),
        ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_conv_turns_session_org",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "sequence >= 1 AND version >= 1 AND started_lifecycle_revision >= 1",
            name="ck_conv_turns_versions",
        ),
        CheckConstraint(
            "length(request_idempotency_hash) = 64 "
            "AND length(request_fingerprint) = 64",
            name="ck_conv_turns_request_hashes",
        ),
        CheckConstraint(
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
        Index(
            "uq_conv_turns_one_active",
            "organization_id",
            "session_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending_dispatch', 'queued', 'running')"
            ),
        ),
        Index(
            "ix_conv_turns_session_status_sequence",
            "organization_id",
            "session_id",
            "status",
            "sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    started_lifecycle_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    request_idempotency_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="pending_dispatch",
        server_default=text("'pending_dispatch'"),
    )
    user_entry_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    assistant_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    latest_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    safe_failure_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ConversationMemoryEntryRecord(_TimestampMixin, Base):
    __tablename__ = "conversation_memory_entries"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_mem_entries_id_session_org",
        ),
        UniqueConstraint(
            "organization_id",
            "session_id",
            "sequence",
            name="uq_mem_entries_session_sequence",
        ),
        UniqueConstraint(
            "organization_id",
            "session_id",
            "idempotency_key_hash",
            name="uq_mem_entries_session_idempotency",
        ),
        ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_mem_entries_session_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
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
        CheckConstraint(
            "entry_type IN ('user_turn', 'assistant_turn', 'node_projection') "
            "AND lifecycle IN ('provisional', 'approved', 'rejected', 'expired')",
            name="ck_mem_entries_enums",
        ),
        CheckConstraint(
            "sequence >= 1 AND "
            "((lifecycle = 'approved' AND content_revision IS NOT NULL "
            "AND content_revision >= 1) OR "
            "(lifecycle <> 'approved' AND content_revision IS NULL))",
            name="ck_mem_entries_revision",
        ),
        CheckConstraint(
            _PROTECTED_PROJECTION_ENVELOPE_CHECK,
            name="ck_mem_entries_content_envelope",
        ),
        Index(
            "ix_mem_entries_candidates",
            "organization_id",
            "session_id",
            "channel",
            "lifecycle",
            "sequence",
        ),
        Index("ix_mem_entries_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    producer_node_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    display_ciphertext: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    display_key_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    display_format_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    display_content_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    display_plaintext_byte_length: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    model_ciphertext: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    model_key_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    model_format_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    model_content_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    model_plaintext_byte_length: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    content_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sensitivity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unclassified",
        server_default=text("'unclassified'"),
    )
    privacy_policy_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    erased_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class MemoryDataDependencyRecord(_TimestampMixin, Base):
    __tablename__ = "memory_data_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_mem_dependencies_id_org",
        ),
        UniqueConstraint(
            "organization_id",
            "source_kind",
            "canonical_resource_id",
            "canonical_resource_version",
            "authorization_safe_reference",
            name="uq_mem_dependencies_identity",
        ),
        CheckConstraint(
            "source_kind IN ('knowledge', 'connector', 'tool', 'subworkflow', "
            "'system_policy', 'privacy_policy')",
            name="ck_mem_dependencies_source_kind",
        ),
        Index(
            "ix_mem_dependencies_resource",
            "organization_id",
            "source_kind",
            "canonical_resource_id",
        ),
        Index(
            "ix_mem_dependencies_auth_revision",
            "organization_id",
            "authorization_decision_revision",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_resource_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    canonical_resource_version: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    authorization_safe_reference: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False)
    authorization_decision_revision: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    resource_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ConversationMemorySummaryRecord(_TimestampMixin, Base):
    __tablename__ = "conversation_memory_summaries"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_mem_summaries_id_session_org",
        ),
        UniqueConstraint(
            "organization_id",
            "session_id",
            "channel",
            "source_revision",
            "policy_version",
            "summarizer_version",
            name="uq_mem_summaries_cache",
        ),
        ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_mem_summaries_session_org",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('provisional', 'approved', 'stale', 'rejected', 'erased')",
            name="ck_mem_summaries_status",
        ),
        CheckConstraint(
            "source_sequence_start >= 1 "
            "AND source_sequence_end >= source_sequence_start "
            "AND source_revision >= 0",
            name="ck_mem_summaries_source_range",
        ),
        CheckConstraint(
            _PROTECTED_PROJECTION_ENVELOPE_CHECK,
            name="ck_mem_summaries_content_envelope",
        ),
        Index(
            "ix_mem_summaries_candidates",
            "organization_id",
            "session_id",
            "channel",
            "status",
            "source_revision",
        ),
        Index("ix_mem_summaries_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sequence_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sequence_end: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    summarizer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    display_ciphertext: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    display_key_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    display_format_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    display_content_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    display_plaintext_byte_length: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    model_ciphertext: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    model_key_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    model_format_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    model_content_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    model_plaintext_byte_length: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    provider_capability_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    usage_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    erased_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class MemoryEntryDependencyRecord(Base):
    __tablename__ = "memory_entry_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["entry_id", "session_id", "organization_id"],
            [
                "conversation_memory_entries.id",
                "conversation_memory_entries.session_id",
                "conversation_memory_entries.organization_id",
            ],
            name="fk_mem_entry_deps_entry_session_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["dependency_id", "organization_id"],
            [
                "memory_data_dependencies.id",
                "memory_data_dependencies.organization_id",
            ],
            name="fk_mem_entry_deps_dependency_org",
            ondelete="CASCADE",
        ),
        Index(
            "ix_mem_entry_deps_reverse",
            "organization_id",
            "dependency_id",
            "entry_id",
        ),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    dependency_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )


class MemorySummaryDependencyRecord(Base):
    __tablename__ = "memory_summary_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["summary_id", "session_id", "organization_id"],
            [
                "conversation_memory_summaries.id",
                "conversation_memory_summaries.session_id",
                "conversation_memory_summaries.organization_id",
            ],
            name="fk_mem_summary_deps_summary_session_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["dependency_id", "organization_id"],
            [
                "memory_data_dependencies.id",
                "memory_data_dependencies.organization_id",
            ],
            name="fk_mem_summary_deps_dependency_org",
            ondelete="CASCADE",
        ),
        Index(
            "ix_mem_summary_deps_reverse",
            "organization_id",
            "dependency_id",
            "summary_id",
        ),
    )

    summary_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    dependency_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )


class MemoryTurnDispatchJobRecord(_TimestampMixin, Base):
    __tablename__ = "memory_turn_dispatch_jobs"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "turn_id",
            "session_id",
            "organization_id",
            name="uq_mem_dispatch_id_turn_session_org",
        ),
        UniqueConstraint(
            "organization_id",
            "turn_id",
            name="uq_mem_dispatch_turn",
        ),
        ForeignKeyConstraint(
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
        CheckConstraint(
            "status IN ('pending', 'claimed', 'published', 'acknowledged', "
            "'reconcile_required', 'terminal')",
            name="ck_mem_dispatch_status",
        ),
        CheckConstraint(
            "claim_generation >= 0 AND claim_generation = attempt_count "
            "AND attempt_count >= 0 AND attempt_count <= max_attempts "
            "AND max_attempts > 0 AND storage_generation >= 1",
            name="ck_mem_dispatch_counters",
        ),
        CheckConstraint(
            "(status = 'claimed' AND claim_owner IS NOT NULL "
            "AND claim_deadline_at IS NOT NULL) OR "
            "(status <> 'claimed' AND claim_owner IS NULL "
            "AND claim_deadline_at IS NULL)",
            name="ck_mem_dispatch_claim_fields",
        ),
        CheckConstraint(
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
        Index(
            "ix_mem_dispatch_due",
            "status",
            "next_attempt_at",
            "claim_deadline_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    memory_contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    storage_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_worker_capability: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    claim_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    claim_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    broker_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    workflow_admission_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    safe_failure_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class MemorySummaryGenerationJobRecord(_TimestampMixin, Base):
    __tablename__ = "memory_summary_generation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "session_id",
            "channel",
            "source_revision",
            "policy_version",
            "summarizer_version",
            name="uq_mem_summary_jobs_cache",
        ),
        ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_mem_summary_jobs_session_org",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('pending', 'lease_acquired', 'budget_reserved', "
            "'provider_started', 'provider_succeeded', 'summary_committed', "
            "'usage_committed', 'reconcile_required', 'failed')",
            name="ck_mem_summary_jobs_status",
        ),
        CheckConstraint(
            "source_revision >= 0 AND lease_generation >= 0 "
            "AND attempt_count >= 0 AND max_attempts > 0",
            name="ck_mem_summary_jobs_counters",
        ),
        Index(
            "ix_mem_summary_jobs_due",
            "status",
            "next_attempt_at",
            "lease_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    summarizer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    lease_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provider_capability_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    budget_reservation_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    provider_attempt_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    summary_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    usage_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    safe_failure_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class MemoryContextPlanRecord(_TimestampMixin, Base):
    __tablename__ = "memory_context_plans"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_mem_context_plans_id_session_org",
        ),
        ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_mem_context_plans_session_org",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "lifecycle_revision >= 1 AND content_revision >= 0 "
            "AND source_revision >= 0 AND length(content_digest) = 64 "
            "AND length(authorization_revision_set_digest) = 64",
            name="ck_mem_context_plans_versions",
        ),
        Index(
            "ix_mem_context_plans_expiry",
            "organization_id",
            "session_id",
            "expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    ordered_references: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    authorization_revision_set_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class MemoryContextLeaseRecord(_TimestampMixin, Base):
    __tablename__ = "memory_context_leases"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "session_id",
            "organization_id",
            name="uq_mem_context_leases_id_session_org",
        ),
        ForeignKeyConstraint(
            ["plan_id", "session_id", "organization_id"],
            [
                "memory_context_plans.id",
                "memory_context_plans.session_id",
                "memory_context_plans.organization_id",
            ],
            name="fk_mem_context_leases_plan_session_org",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "state IN ('issued', 'claimed', 'invalidated', 'expired') "
            "AND claim_generation >= 0",
            name="ck_mem_context_leases_state",
        ),
        CheckConstraint(
            "(state = 'claimed' AND provider_attempt_id IS NOT NULL "
            "AND claim_deadline_at IS NOT NULL) OR "
            "(state <> 'claimed' AND provider_attempt_id IS NULL)",
            name="ck_mem_context_leases_claim_fields",
        ),
        Index(
            "ix_mem_context_leases_expiry",
            "organization_id",
            "session_id",
            "state",
            "expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    node_invocation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    audience_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    provider_capability_reference: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    provider_capability_revision: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="issued",
        server_default=text("'issued'"),
    )
    claim_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    provider_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    claim_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class MemoryContextProviderAttemptRecord(_TimestampMixin, Base):
    __tablename__ = "memory_context_provider_attempts"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "lease_id",
            "session_id",
            "organization_id",
            name="uq_mem_provider_attempt_id_lease_org",
        ),
        ForeignKeyConstraint(
            ["lease_id", "session_id", "organization_id"],
            [
                "memory_context_leases.id",
                "memory_context_leases.session_id",
                "memory_context_leases.organization_id",
            ],
            name="fk_mem_provider_attempt_lease_session_org",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('claimed', 'provider_started', 'succeeded', 'failed', "
            "'outcome_unknown', 'reconciled') AND version >= 1",
            name="ck_mem_provider_attempt_status",
        ),
        CheckConstraint(
            "(status = 'claimed' AND provider_started_at IS NULL "
            "AND terminal_at IS NULL) OR "
            "(status = 'provider_started' AND provider_started_at IS NOT NULL "
            "AND terminal_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'outcome_unknown', 'reconciled') "
            "AND terminal_at IS NOT NULL)",
            name="ck_mem_provider_attempt_fields",
        ),
        Index(
            "ix_mem_provider_attempt_state",
            "organization_id",
            "session_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    lease_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    node_invocation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    provider_capability_reference: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    provider_capability_revision: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    provider_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provider_correlation_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    usage_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    safe_failure_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ConversationPurgeJobRecord(_TimestampMixin, Base):
    __tablename__ = "conversation_purge_jobs"
    __table_args__ = (
        UniqueConstraint(
            "receipt_verifier_key_version",
            "receipt_verifier_hash",
            name="uq_conv_purge_receipt_verifier",
        ),
        ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["conversation_sessions.id", "conversation_sessions.organization_id"],
            name="fk_conv_purge_session_org",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', "
            "'completed_with_hold', 'retryable_failure', 'terminal_failure')",
            name="ck_conv_purge_status",
        ),
        CheckConstraint(
            "claim_generation >= 0 AND attempt_count >= 0 AND max_attempts > 0",
            name="ck_conv_purge_counters",
        ),
        CheckConstraint(
            "((deployment_id IS NULL AND deployment_version IS NULL "
            "AND audience_kind IS NULL AND app_id IS NULL) OR "
            "(deployment_id IS NOT NULL AND audience_kind IS NOT NULL AND "
            "((audience_kind = 'public_chatbot' "
            "AND deployment_version IS NOT NULL AND deployment_version > 0) OR "
            "(audience_kind = 'authenticated_internal_chatbot' AND "
            "(deployment_version IS NULL OR deployment_version > 0)))))",
            name="ck_conv_purge_scope_snapshot",
        ),
        CheckConstraint(
            "(status = 'running' AND claim_owner IS NOT NULL "
            "AND claim_deadline_at IS NOT NULL) OR "
            "(status <> 'running' AND claim_owner IS NULL)",
            name="ck_conv_purge_claim_fields",
        ),
        Index(
            "ix_conv_purge_due",
            "status",
            "next_attempt_at",
            "claim_deadline_at",
        ),
        Index("ix_conv_purge_receipt_expiry", "receipt_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversation_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_reference_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    app_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    deployment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audience_kind: Mapped[str | None] = mapped_column(String(48), nullable=True)
    receipt_verifier_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    receipt_verifier_key_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    receipt_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    claim_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    claim_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    purge_cursor: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    legal_hold_policy_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    backup_erasure_policy_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    safe_failure_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ConversationIdempotencyRecord(_TimestampMixin, Base):
    __tablename__ = "conversation_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_conv_idempotency_id_org",
        ),
        UniqueConstraint(
            "organization_id",
            "operation",
            "scope_digest",
            "idempotency_key_hash",
            name="uq_conv_idempotency_scope_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed') "
            "AND length(scope_digest) = 64 "
            "AND length(idempotency_key_hash) = 64 "
            "AND length(request_fingerprint) = 64",
            name="ck_conv_idempotency_fields",
        ),
        CheckConstraint(
            "((result_lifecycle IS NULL "
            "AND result_lifecycle_revision IS NULL "
            "AND result_memory_contract_version IS NULL "
            "AND result_expires_at IS NULL "
            "AND result_previous_lifecycle IS NULL "
            "AND result_previous_lifecycle_revision IS NULL) OR "
            "(result_lifecycle IN "
            "('active', 'closed', 'delete_pending', 'deleted', 'expired') "
            "AND result_lifecycle_revision > 0 "
            "AND ((result_memory_contract_version IS NULL "
            "AND result_expires_at IS NULL) OR "
            "(result_memory_contract_version IS NOT NULL "
            "AND result_expires_at IS NOT NULL)) "
            "AND ((result_previous_lifecycle IS NULL "
            "AND result_previous_lifecycle_revision IS NULL) OR "
            "(result_previous_lifecycle IN "
            "('active', 'closed', 'delete_pending', 'deleted', 'expired') "
            "AND result_previous_lifecycle_revision > 0))))",
            name="ck_conv_idempotency_result_snapshot",
        ),
        CheckConstraint(
            "((authorization_app_id IS NULL "
            "AND authorization_verifier_key_version IS NULL "
            "AND authorization_verifier_hash IS NULL) OR "
            "(authorization_app_id IS NOT NULL "
            "AND authorization_verifier_key_version IS NOT NULL "
            "AND authorization_verifier_hash IS NOT NULL "
            "AND length(authorization_verifier_hash) = 64))",
            name="ck_conv_idempotency_authorization_scope",
        ),
        Index("ix_conv_idempotency_expiry", "retention_expires_at"),
        Index(
            "ix_conv_idempotency_authorized_replay",
            "organization_id",
            "authorization_app_id",
            "operation",
            "idempotency_key_hash",
            "authorization_verifier_key_version",
            "authorization_verifier_hash",
            postgresql_where=text("authorization_app_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_app_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    authorization_verifier_key_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    authorization_verifier_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    replay_record_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    secret_replay_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    retention_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    safe_result_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    result_lifecycle: Mapped[str | None] = mapped_column(String(24), nullable=True)
    result_lifecycle_revision: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    result_memory_contract_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    result_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    result_previous_lifecycle: Mapped[str | None] = mapped_column(
        String(24),
        nullable=True,
    )
    result_previous_lifecycle_revision: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )


class ConversationSecretReplayRecord(Base):
    """Short-lived encrypted capability replay payload.

    The raw access grant or purge receipt is never a column in this table.  A
    record is bound to one idempotency result and removed by its expiry worker.
    """

    __tablename__ = "conversation_secret_replays"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_record_id",
            name="uq_conv_secret_replay_idempotency",
        ),
        ForeignKeyConstraint(
            ["idempotency_record_id", "organization_id"],
            [
                "conversation_idempotency_records.id",
                "conversation_idempotency_records.organization_id",
            ],
            name="fk_conv_secret_replay_idempotency_org",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "purpose IN ('access_grant', 'purge_receipt') "
            "AND length(associated_data_digest) = 64",
            name="ck_conv_secret_replay_fields",
        ),
        Index("ix_conv_secret_replays_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    idempotency_record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    associated_data_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=text("now()"),
    )
