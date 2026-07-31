from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from apps.shared.db.base import Base
from apps.shared.domain.schedule_dispatch import (
    CANCELED_REASONS,
    OUTCOME_RESOLUTIONS,
    PENDING_REASONS,
    POST_ADMISSION_DEAD_LETTER_REASONS,
    PRE_ADMISSION_DEAD_LETTER_REASONS,
    REASON_EXECUTION_OUTCOME_UNKNOWN,
    SCHEDULE_DISPATCH_STATUSES,
    STATUS_CANCELED,
    STATUS_DEAD_LETTERED,
    STATUS_DISPATCHING,
    STATUS_ENQUEUED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
)
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


def _sql_values(values: frozenset[str]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


def _status_field_constraint() -> str:
    terminal_common = (
        "completed_at IS NOT NULL AND lease_owner IS NULL AND "
        "lease_expires_at IS NULL AND execution_deadline_at IS NULL"
    )
    return " OR ".join(
        (
            "(status = 'pending' AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND execution_deadline_at IS NULL AND started_at IS NULL AND completed_at IS NULL)",
            "(status = 'dispatching' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND celery_task_id IS NOT NULL "
            "AND execution_deadline_at IS NULL AND started_at IS NULL "
            "AND completed_at IS NULL)",
            "(status = 'enqueued' AND celery_task_id IS NOT NULL "
            "AND enqueued_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_owner IS NULL AND execution_deadline_at IS NULL "
            "AND started_at IS NULL AND completed_at IS NULL)",
            "(status = 'running' AND lease_owner IS NOT NULL "
            "AND celery_task_id IS NOT NULL AND workflow_run_id IS NOT NULL "
            "AND enqueued_at IS NOT NULL AND started_at IS NOT NULL "
            "AND execution_deadline_at IS NOT NULL AND lease_expires_at IS NULL "
            "AND completed_at IS NULL)",
            f"(status = 'succeeded' AND {terminal_common} "
            "AND workflow_run_id IS NOT NULL AND enqueued_at IS NOT NULL "
            "AND started_at IS NOT NULL)",
            f"(status = 'canceled' AND {terminal_common} "
            "AND workflow_run_id IS NULL AND started_at IS NULL)",
            f"(status = 'dead_lettered' AND {terminal_common} "
            "AND ((workflow_run_id IS NULL AND started_at IS NULL) OR "
            "(workflow_run_id IS NOT NULL AND celery_task_id IS NOT NULL "
            "AND enqueued_at IS NOT NULL AND started_at IS NOT NULL)))",
        )
    )


def _safe_reason_constraint() -> str:
    non_reason_statuses = frozenset(
        {STATUS_DISPATCHING, STATUS_ENQUEUED, STATUS_RUNNING, STATUS_SUCCEEDED}
    )
    return " OR ".join(
        (
            f"(status = '{STATUS_PENDING}' AND "
            f"(safe_reason_code IS NULL OR safe_reason_code IN ({_sql_values(PENDING_REASONS)})))",
            f"(status = '{STATUS_CANCELED}' AND "
            f"safe_reason_code IS NOT NULL AND "
            f"safe_reason_code IN ({_sql_values(CANCELED_REASONS)}))",
            f"(status = '{STATUS_DEAD_LETTERED}' AND "
            f"safe_reason_code IS NOT NULL AND ((safe_reason_code IN "
            f"({_sql_values(PRE_ADMISSION_DEAD_LETTER_REASONS)}) "
            "AND workflow_run_id IS NULL AND started_at IS NULL) OR "
            f"(safe_reason_code IN ({_sql_values(POST_ADMISSION_DEAD_LETTER_REASONS)}) "
            "AND workflow_run_id IS NOT NULL AND celery_task_id IS NOT NULL "
            "AND enqueued_at IS NOT NULL AND started_at IS NOT NULL)))",
            f"(status IN ({_sql_values(non_reason_statuses)}) "
            "AND safe_reason_code IS NULL)",
        )
    )


def _outcome_review_constraint() -> str:
    all_null = (
        "outcome_reviewed_at IS NULL AND outcome_review_audit_id IS NULL "
        "AND outcome_resolution_code IS NULL"
    )
    all_present = (
        "outcome_reviewed_at IS NOT NULL AND outcome_review_audit_id IS NOT NULL "
        "AND outcome_resolution_code IS NOT NULL"
    )
    return (
        f"({all_null}) OR (status = '{STATUS_DEAD_LETTERED}' AND "
        f"safe_reason_code = '{REASON_EXECUTION_OUTCOME_UNKNOWN}' AND {all_present} "
        f"AND outcome_resolution_code IN ({_sql_values(OUTCOME_RESOLUTIONS)}))"
    )


class ScheduleDispatchClaim(Base):
    __tablename__ = "schedule_dispatch_claims"
    __table_args__ = (
        UniqueConstraint(
            "schedule_id",
            "scheduled_for",
            name="uq_schedule_dispatch_claims_occurrence",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_schedule_dispatch_claims_idempotency_key",
        ),
        UniqueConstraint(
            "workflow_run_id",
            name="uq_schedule_dispatch_claims_workflow_run_id",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(SCHEDULE_DISPATCH_STATUSES)})",
            name="ck_schedule_dispatch_claims_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_schedule_dispatch_claims_attempt_count",
        ),
        CheckConstraint(
            "celery_task_id IS NULL OR celery_task_id = idempotency_key",
            name="ck_schedule_dispatch_claims_task_id",
        ),
        CheckConstraint(
            "next_attempt_at IS NULL OR status = 'pending'",
            name="ck_schedule_dispatch_claims_next_attempt_status",
        ),
        CheckConstraint(
            "status NOT IN ('pending', 'dispatching', 'enqueued') "
            "OR workflow_run_id IS NULL",
            name="ck_schedule_dispatch_claims_preadmission_run",
        ),
        CheckConstraint(
            _status_field_constraint(),
            name="ck_schedule_dispatch_claims_status_fields",
        ),
        CheckConstraint(
            _safe_reason_constraint(),
            name="ck_schedule_dispatch_claims_safe_reason",
        ),
        CheckConstraint(
            _outcome_review_constraint(),
            name="ck_schedule_dispatch_claims_outcome_review",
        ),
        CheckConstraint(
            "(enqueued_at IS NULL OR claimed_at <= enqueued_at) AND "
            "(started_at IS NULL OR (enqueued_at IS NOT NULL AND enqueued_at <= started_at)) AND "
            "(completed_at IS NULL OR started_at IS NULL OR started_at <= completed_at)",
            name="ck_schedule_dispatch_claims_timestamp_order",
        ),
        Index(
            "ix_schedule_dispatch_claims_status_next_attempt",
            "status",
            "next_attempt_at",
        ),
        Index(
            "ix_schedule_dispatch_claims_status_lease_expiry",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_schedule_dispatch_claims_status_execution_deadline",
            "status",
            "execution_deadline_at",
        ),
        Index(
            "ix_schedule_dispatch_claims_visibility_gap",
            "workflow_run_missing_reported_at",
            "status",
            "started_at",
        ),
        Index("ix_schedule_dispatch_claims_deployment_id", "deployment_id"),
        Index("ix_schedule_dispatch_claims_completed_at", "completed_at"),
        Index(
            "ix_schedule_dispatch_claims_org_status_completed",
            "organization_id",
            "status",
            "completed_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATUS_PENDING, server_default=STATUS_PENDING
    )
    lease_owner: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_deadline_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    workflow_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    workflow_run_missing_reported_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    safe_reason_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    outcome_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome_review_audit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    outcome_resolution_code: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
    enqueued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
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
