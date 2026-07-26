from __future__ import annotations

import uuid
from datetime import datetime, timezone

from apps.shared.db.base import Base
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationWorkflowExecutionAdmissionRecord(Base):
    """Workflow-owned, content-free admission and execution fence."""

    __tablename__ = "conversation_workflow_execution_admissions"
    __table_args__ = (
        CheckConstraint(
            "deployment_version >= 1 AND storage_generation >= 1 "
            "AND version >= 1 AND lease_generation >= 0",
            name="ck_conv_workflow_admission_versions",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_conv_workflow_admission_fingerprint",
        ),
        CheckConstraint(
            "state IN ('admitted', 'leased', 'completed', 'failed', "
            "'outcome_unknown')",
            name="ck_conv_workflow_admission_state",
        ),
        CheckConstraint(
            "(state = 'admitted' AND lease_owner IS NULL "
            "AND lease_deadline IS NULL AND attempt_id IS NULL "
            "AND result_entry_id IS NULL AND result_digest IS NULL "
            "AND safe_failure_reason IS NULL AND terminal_at IS NULL) OR "
            "(state = 'leased' AND lease_owner IS NOT NULL "
            "AND lease_deadline IS NOT NULL AND attempt_id IS NOT NULL "
            "AND result_entry_id IS NULL AND result_digest IS NULL "
            "AND safe_failure_reason IS NULL AND terminal_at IS NULL) OR "
            "(state = 'completed' AND lease_owner IS NOT NULL "
            "AND attempt_id IS NOT NULL AND result_entry_id IS NOT NULL "
            "AND length(result_digest) = 64 AND safe_failure_reason IS NULL "
            "AND terminal_at IS NOT NULL) OR "
            "(state IN ('failed', 'outcome_unknown') "
            "AND lease_owner IS NOT NULL AND attempt_id IS NOT NULL "
            "AND result_entry_id IS NULL AND result_digest IS NULL "
            "AND safe_failure_reason IS NOT NULL AND terminal_at IS NOT NULL)",
            name="ck_conv_workflow_admission_state_fields",
        ),
        Index(
            "uq_conv_workflow_admission_dispatch",
            "organization_id",
            "dispatch_id",
            unique=True,
        ),
        Index(
            "ix_conv_workflow_admission_session_terminal",
            "organization_id",
            "session_id",
            "terminal_at",
        ),
        Index(
            "ix_conv_workflow_admission_lease",
            "state",
            "lease_deadline",
        ),
        UniqueConstraint(
            "execution_id",
            name="uq_conv_workflow_admission_execution",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, nullable=False, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="CASCADE"),
        nullable=False,
    )
    dispatch_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    turn_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    app_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    deployment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_worker_capability: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="admitted", server_default=text("'admitted'")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    lease_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    result_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_failure_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, server_default=text("now()")
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ConversationWorkflowExecutionEventRecord(Base):
    """Content-free durable journal for public conversation execution."""

    __tablename__ = "conversation_workflow_execution_events"
    __table_args__ = (
        CheckConstraint(
            "deployment_version >= 1",
            name="ck_conv_workflow_event_deployment_version",
        ),
        CheckConstraint(
            "actor_type = 'public'",
            name="ck_conv_workflow_event_public_actor",
        ),
        CheckConstraint(
            "event_type IN ('execution_admitted', 'execution_running', "
            "'execution_completed', 'execution_failed', "
            "'execution_outcome_unknown')",
            name="ck_conv_workflow_event_type",
        ),
        CheckConstraint(
            "(event_type IN ('execution_failed', 'execution_outcome_unknown') "
            "AND safe_failure_reason IS NOT NULL) OR "
            "(event_type NOT IN ('execution_failed', "
            "'execution_outcome_unknown') AND safe_failure_reason IS NULL)",
            name="ck_conv_workflow_event_failure_reason",
        ),
        UniqueConstraint(
            "admission_id",
            "event_type",
            name="uq_conv_workflow_event_admission_type",
        ),
        Index(
            "ix_conv_workflow_event_execution_created",
            "execution_id",
            "created_at",
        ),
        Index(
            "ix_conv_workflow_event_org_created",
            "organization_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, nullable=False, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="CASCADE"),
        nullable=False,
    )
    admission_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "conversation_workflow_execution_admissions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    app_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    deployment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="public", server_default=text("'public'")
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    safe_failure_reason: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=text("now()"),
    )


__all__ = [
    "ConversationWorkflowExecutionAdmissionRecord",
    "ConversationWorkflowExecutionEventRecord",
]
