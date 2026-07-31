from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from apps.shared.db.base import Base
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class WorkflowNodeEffectAttempt(Base):
    __tablename__ = "workflow_node_effect_attempts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "execution_id",
            "node_invocation_id",
            "effect_sequence",
            name="uq_workflow_node_effect_attempts_slot",
        ),
        CheckConstraint(
            "effect_sequence >= 0 AND claim_generation > 0",
            name="ck_workflow_node_effect_attempts_counters",
        ),
        CheckConstraint(
            "provider_replay_capability IN ('supported', 'unsupported', 'unknown') "
            "AND result_reuse_capability IN ('supported', 'unavailable') "
            "AND status IN ('prepared', 'in_flight', 'terminal') "
            "AND (outcome IS NULL OR outcome IN "
            "('succeeded', 'failed_before_effect', 'effect_outcome_unknown')) "
            "AND (replay_decision IS NULL OR replay_decision IN "
            "('reuse_result', 'result_unavailable', 'retry_before_effect', "
            "'replay_same_key', 'stop'))",
            name="ck_workflow_node_effect_attempts_enums",
        ),
        CheckConstraint(
            "((status = 'prepared' AND claim_owner IS NOT NULL "
            "AND claim_expires_at IS NOT NULL AND provider_started_at IS NULL "
            "AND outcome IS NULL AND replay_decision IS NULL AND terminal_at IS NULL) "
            "OR (status = 'in_flight' AND claim_owner IS NOT NULL "
            "AND claim_expires_at IS NOT NULL AND provider_started_at IS NOT NULL "
            "AND outcome IS NULL AND replay_decision IS NULL AND terminal_at IS NULL) "
            "OR (status = 'terminal' AND claim_owner IS NULL "
            "AND claim_expires_at IS NULL AND outcome IS NOT NULL "
            "AND replay_decision IS NOT NULL AND terminal_at IS NOT NULL))",
            name="ck_workflow_node_effect_attempts_status_shape",
        ),
        CheckConstraint(
            "((status <> 'terminal' AND outcome IS NULL AND replay_decision IS NULL) OR "
            "(status = 'terminal' AND ("
            "(outcome = 'succeeded' AND provider_started_at IS NOT NULL "
            "AND replay_decision IN ('reuse_result', 'result_unavailable')) OR "
            "(outcome = 'failed_before_effect' "
            "AND replay_decision IN ('retry_before_effect', 'stop')) OR "
            "(outcome = 'effect_outcome_unknown' AND provider_started_at IS NOT NULL "
            "AND replay_decision IN ('replay_same_key', 'stop'))))) "
            "AND (replay_decision <> 'replay_same_key' "
            "OR provider_replay_capability = 'supported')",
            name="ck_workflow_node_effect_attempts_outcome_decision",
        ),
        CheckConstraint(
            "((provider_replay_capability = 'supported' "
            "AND replay_deadline_at IS NOT NULL AND key_version IS NOT NULL "
            "AND key_format_version IS NOT NULL "
            "AND idempotency_key_fingerprint IS NOT NULL) OR "
            "(provider_replay_capability IN ('unsupported', 'unknown') "
            "AND replay_deadline_at IS NULL AND key_version IS NULL "
            "AND key_format_version IS NULL "
            "AND idempotency_key_fingerprint IS NULL))",
            name="ck_workflow_node_effect_attempts_key_contract",
        ),
        CheckConstraint(
            "((status = 'terminal' AND replay_decision = 'reuse_result' "
            "AND outcome = 'succeeded' AND result_reuse_capability = 'supported' "
            "AND replay_result IS NOT NULL) OR "
            "((status <> 'terminal' OR replay_decision <> 'reuse_result') "
            "AND replay_result IS NULL))",
            name="ck_workflow_node_effect_attempts_replay_result",
        ),
        CheckConstraint(
            "status = 'terminal' OR "
            "(provider_status_code IS NULL AND error_code IS NULL)",
            name="ck_workflow_node_effect_attempts_provider_summary",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ("
            "'connection_failed', 'invalid_prepared_request', "
            "'provider_call_failed', 'provider_call_finalize_failed', "
            "'provider_key_field_conflict', "
            "'provider_key_request_conflict', 'provider_rejected_request', "
            "'response_lost', 'response_malformed', 'timeout', "
            "'unexpected_provider_status')",
            name="ck_workflow_node_effect_attempts_error_code",
        ),
        Index(
            "ix_workflow_node_effect_attempts_contract",
            "provider",
            "operation",
            "provider_contract_version",
        ),
        Index(
            "ix_workflow_node_effect_attempts_hmac_readiness",
            "provider",
            "operation",
            "provider_contract_version",
            "key_version",
            postgresql_where=text(
                "provider_replay_capability = 'supported' AND "
                "(status IN ('prepared', 'in_flight') OR "
                "(status = 'terminal' AND replay_decision IN "
                "('retry_before_effect', 'replay_same_key')))"
            ),
        ),
        Index("ix_workflow_node_effect_attempts_workflow_run_id", "workflow_run_id"),
        Index("ix_workflow_node_effect_attempts_node_run_id", "node_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    app_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    node_invocation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    node_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    effect_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_replay_capability: Mapped[str] = mapped_column(String(16), nullable=False)
    result_reuse_capability: Mapped[str] = mapped_column(String(16), nullable=False)
    effect_input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    replay_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    replay_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    claim_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    key_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    key_format_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    idempotency_key_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    replay_result: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    provider_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
        server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
