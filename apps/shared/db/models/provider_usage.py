"""Durable provider-attempt usage and correction records."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from apps.shared.db.base import Base
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


class ProviderUsageOperationRecord(Base):
    """Canonical fact for one admitted provider attempt.

    Resource identifiers are immutable snapshots.  Apart from the tenant
    boundary, control-plane rows deliberately are not foreign keys, so their
    lifecycle cannot erase or mutate historical cost/outcome facts.
    """

    __tablename__ = "provider_usage_operations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider_attempt_id",
            "purpose",
            name="uq_provider_usage_operation_attempt",
        ),
        UniqueConstraint(
            "audit_event_id",
            name="uq_provider_usage_operation_audit_event",
        ),
        CheckConstraint(
            "deployment_version >= 1 AND capability_revision >= 1 "
            "AND policy_revision >= 1 AND state_version >= 1 "
            "AND input_token_cap >= 0 AND output_token_cap >= 0 "
            "AND cost_cap_microusd >= 0 AND admitted_input_tokens >= 0 "
            "AND admitted_output_tokens >= 0 "
            "AND admitted_input_tokens <= input_token_cap "
            "AND admitted_output_tokens <= output_token_cap "
            "AND input_price_per_1k >= 0 AND output_price_per_1k >= 0",
            name="ck_provider_usage_operation_bounds",
        ),
        CheckConstraint(
            "purpose IN ('main_generation', 'memory_summary', 'query_embedding') "
            "AND state IN ('intent', 'provider_started', 'succeeded', "
            "'failed_definitive', 'outcome_unknown') "
            "AND projection_status IN ('pending', 'projected', "
            "'awaiting_workflow_run', 'retryable_failure', 'terminal_failure') "
            "AND execution_subject_kind IN ('user', 'anonymous_public', 'system') "
            "AND credential_principal_kind = 'user' "
            "AND billing_principal_kind = 'organization' "
            "AND audit_actor_kind IN ('user', 'public', 'system') "
            "AND length(permission_revision) = 64 "
            "AND length(relation_revision) = 64 "
            "AND length(egress_revision) = 64 "
            "AND length(pricing_revision) = 64",
            name="ck_provider_usage_operation_identity_kinds",
        ),
        CheckConstraint(
            "purpose <> 'query_embedding' OR (output_token_cap = 0 "
            "AND admitted_output_tokens = 0 "
            "AND (completion_tokens IS NULL OR completion_tokens = 0))",
            name="ck_provider_usage_operation_query_embedding_output",
        ),
        CheckConstraint(
            "jsonb_typeof(container_path) = 'array'",
            name="ck_provider_usage_operation_location",
        ),
        CheckConstraint(
            "((execution_subject_kind = 'user' AND execution_subject_id IS NOT NULL "
            "AND audit_actor_kind = 'user' "
            "AND audit_actor_id = execution_subject_id) "
            "OR (execution_subject_kind = 'anonymous_public' "
            "AND execution_subject_id IS NULL AND audit_actor_kind = 'public' "
            "AND audit_actor_id IS NULL) "
            "OR (execution_subject_kind = 'system' "
            "AND execution_subject_id IS NULL AND audit_actor_kind = 'system' "
            "AND audit_actor_id IS NULL)) "
            "AND credential_principal_id IS NOT NULL "
            "AND billing_principal_id = organization_id",
            name="ck_provider_usage_operation_identity_alignment",
        ),
        CheckConstraint(
            "(state = 'succeeded' AND prompt_tokens IS NOT NULL "
            "AND completion_tokens IS NOT NULL AND total_cost_microusd IS NOT NULL "
            "AND latency_ms IS NOT NULL AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 AND total_cost_microusd >= 0 "
            "AND prompt_tokens <= admitted_input_tokens "
            "AND completion_tokens <= admitted_output_tokens "
            "AND total_cost_microusd <= cost_cap_microusd "
            "AND latency_ms >= 0 AND usage_revision >= 1 "
            "AND safe_reason_code IS NULL) "
            "OR (state = 'failed_definitive' AND prompt_tokens IS NULL "
            "AND completion_tokens IS NULL AND total_cost_microusd IS NULL "
            "AND latency_ms IS NULL AND usage_revision = 0 "
            "AND safe_reason_code IN ('provider_not_sent', 'provider_rejected')) "
            "OR (state = 'outcome_unknown' AND prompt_tokens IS NULL "
            "AND completion_tokens IS NULL AND total_cost_microusd IS NULL "
            "AND latency_ms IS NULL AND usage_revision = 0 "
            "AND safe_reason_code IN ('provider_call_failed', 'provider_timeout', "
            "'provider_usage_invalid', 'stale_provider_started', "
            "'terminal_record_failed')) "
            "OR (state IN ('intent', 'provider_started') AND prompt_tokens IS NULL "
            "AND completion_tokens IS NULL AND total_cost_microusd IS NULL "
            "AND latency_ms IS NULL AND usage_revision = 0 "
            "AND safe_reason_code IS NULL)",
            name="ck_provider_usage_operation_state_payload",
        ),
        CheckConstraint(
            "(state = 'intent' AND provider_started_at IS NULL "
            "AND terminal_at IS NULL AND audit_event_id IS NULL) "
            "OR (state = 'provider_started' AND provider_started_at IS NOT NULL "
            "AND terminal_at IS NULL AND audit_event_id IS NULL) "
            "OR (state IN ('succeeded', 'failed_definitive', 'outcome_unknown') "
            "AND provider_started_at IS NOT NULL AND terminal_at IS NOT NULL "
            "AND audit_event_id IS NOT NULL)",
            name="ck_provider_usage_operation_timestamps",
        ),
        CheckConstraint(
            "projection_attempts >= 0 "
            "AND (projection_status <> 'awaiting_workflow_run' "
            "OR workflow_run_id IS NOT NULL) "
            "AND (projected_usage_revision IS NULL "
            "OR (projected_usage_revision >= 1 "
            "AND projected_usage_revision <= usage_revision)) "
            "AND ((projection_status IN ('projected', 'awaiting_workflow_run') "
            "AND state = 'succeeded' "
            "AND projected_usage_log_id IS NOT NULL AND projected_at IS NOT NULL "
            "AND projected_usage_revision = usage_revision "
            "AND projection_reason_code IS NULL) "
            "OR projection_status NOT IN ('projected', 'awaiting_workflow_run'))",
            name="ck_provider_usage_operation_projection",
        ),
        Index(
            "ix_provider_usage_operation_projection",
            "organization_id",
            "state",
            "projection_status",
            "intent_created_at",
        ),
        Index(
            "ix_provider_usage_operation_budget_period",
            "organization_id",
            "workflow_id",
            "provider_started_at",
            "state",
        ),
        Index(
            "ix_provider_usage_operation_workflow_budget_period",
            "workflow_id",
            "provider_started_at",
            postgresql_where=text(
                "provider_started_at IS NOT NULL "
                "AND purpose IN ('main_generation', 'memory_summary', "
                "'query_embedding') "
                "AND state IN ('provider_started', 'succeeded', 'outcome_unknown')"
            ),
        ),
        Index(
            "ix_provider_usage_operation_subject_cost_period",
            "execution_subject_id",
            "provider_started_at",
            postgresql_where=text(
                "execution_subject_kind = 'user' "
                "AND execution_subject_id IS NOT NULL "
                "AND provider_started_at IS NOT NULL "
                "AND purpose IN ('main_generation', 'memory_summary', "
                "'query_embedding') "
                "AND state = 'succeeded'"
            ),
        ),
        Index(
            "ix_provider_usage_operation_projection_recovery",
            "provider_started_at",
            "id",
            postgresql_where=text(
                "state = 'succeeded' AND projection_status IN "
                "('pending', 'retryable_failure', 'awaiting_workflow_run')"
            ),
        ),
        Index(
            "ix_provider_usage_operation_started_recovery",
            "provider_started_at",
            "id",
            postgresql_where=text("state = 'provider_started'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    deployment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    container_path: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    node_invocation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    execution_admission_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    provider_attempt_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)

    capability_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    capability_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    capability_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    model_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    model_api_id: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    permission_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    relation_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    egress_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    pricing_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    input_price_per_1k: Mapped[Decimal] = mapped_column(
        Numeric(20, 9), nullable=False
    )
    output_price_per_1k: Mapped[Decimal] = mapped_column(
        Numeric(20, 9), nullable=False
    )
    input_token_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    output_token_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_cap_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    admitted_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    admitted_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)

    execution_subject_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_subject_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    credential_principal_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="user", server_default=text("'user'")
    )
    credential_principal_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    billing_principal_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_principal_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    audit_actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    audit_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )

    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="intent", server_default=text("'intent'")
    )
    state_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    safe_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    provider_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cost_microusd: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    cost_optimizer_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    projection_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default=text("'pending'")
    )
    projected_usage_log_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    projected_usage_revision: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    projected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    projection_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    projection_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    projection_reason_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    audit_event_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )


class ProviderUsageCorrectionRecord(Base):
    """Append-only correction receipt for one canonical usage revision."""

    __tablename__ = "provider_usage_corrections"
    __table_args__ = (
        UniqueConstraint(
            "operation_id",
            "correction_key",
            name="uq_provider_usage_correction_key",
        ),
        UniqueConstraint(
            "operation_id",
            "resulting_usage_revision",
            name="uq_provider_usage_correction_revision",
        ),
        CheckConstraint(
            "base_usage_revision >= 1 "
            "AND resulting_usage_revision = base_usage_revision + 1 "
            "AND prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND total_cost_microusd >= 0 AND latency_ms >= 0 "
            "AND source IN ('provider_reconciliation', 'billing_import', 'operator') "
            "AND reason_code IN ('provider_reported_usage', 'billing_reconciliation')",
            name="ck_provider_usage_correction_values",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("provider_usage_operations.id", ondelete="CASCADE"),
        nullable=False,
    )
    correction_key: Mapped[str] = mapped_column(String(128), nullable=False)
    base_usage_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_usage_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


__all__ = ["ProviderUsageCorrectionRecord", "ProviderUsageOperationRecord"]
