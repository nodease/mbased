from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from apps.shared.db.base import Base
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

_STATUS_FIELDS_CHECK_SQL = (
    "(status = 'open' AND acknowledged_by IS NULL "
    "AND acknowledged_at IS NULL AND resolution_type IS NULL "
    "AND resolution_reason IS NULL AND resolved_by IS NULL "
    "AND resolved_at IS NULL) OR "
    "(status = 'acknowledged' AND acknowledged_at IS NOT NULL "
    "AND resolution_type IS NULL AND resolution_reason IS NULL "
    "AND resolved_by IS NULL AND resolved_at IS NULL) OR "
    "(status = 'resolved' AND resolution_type IS NOT NULL "
    "AND resolution_reason IS NOT NULL AND resolved_at IS NOT NULL)"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SecurityAlert(Base):
    __tablename__ = "security_alerts"
    __table_args__ = (
        CheckConstraint(
            "occurrence_count >= 0",
            name="ck_security_alerts_occurrence_count_nonnegative",
        ),
        CheckConstraint(
            "episode_count >= 1",
            name="ck_security_alerts_episode_count_positive",
        ),
        CheckConstraint(
            "lifecycle_version >= 1",
            name="ck_security_alerts_lifecycle_version_positive",
        ),
        CheckConstraint(
            "severity IN ('medium', 'high')",
            name="ck_security_alerts_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')",
            name="ck_security_alerts_status",
        ),
        CheckConstraint(
            "resolution_type IS NULL OR resolution_type IN "
            "('mitigated', 'false_positive', 'accepted_risk')",
            name="ck_security_alerts_resolution_type",
        ),
        CheckConstraint(
            "first_detected_at <= last_detected_at",
            name="ck_security_alerts_timestamp_order",
        ),
        CheckConstraint(
            _STATUS_FIELDS_CHECK_SQL,
            name="ck_security_alerts_status_fields",
        ),
        Index(
            "uq_security_alerts_active_detection_key",
            "detection_key",
            unique=True,
            postgresql_where=text("status IN ('open', 'acknowledged')"),
        ),
        Index(
            "ix_security_alerts_organization_last_detected",
            "organization_id",
            "last_detected_at",
        ),
        Index(
            "ix_security_alerts_org_status_detected",
            "organization_id",
            "status",
            "last_detected_at",
            "id",
        ),
        Index(
            "ix_security_alerts_org_severity_detected",
            "organization_id",
            "severity",
            "last_detected_at",
            "id",
        ),
        Index(
            "ix_security_alerts_org_rule_detected",
            "organization_id",
            "rule_id",
            "last_detected_at",
            "id",
        ),
        Index(
            "ix_security_alerts_org_actor_detected",
            "organization_id",
            "subject_actor_id",
            "last_detected_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    subject_actor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default=text("'open'")
    )
    policy_reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    detection_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    episode_count: Mapped[int] = mapped_column(
        nullable=False, default=1, server_default=text("1")
    )
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_episode_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=text("now()"),
    )
    lifecycle_version: Mapped[int] = mapped_column(
        nullable=False, default=1, server_default=text("1")
    )
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    resolution_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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


class SecurityAlertAuditEvent(Base):
    __tablename__ = "security_alert_audit_events"
    __table_args__ = (
        UniqueConstraint(
            "security_alert_id",
            "audit_log_id",
            name="uq_security_alert_audit_events_alert_audit",
        ),
        Index(
            "ix_security_alert_audit_events_audit_log_id",
            "audit_log_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    security_alert_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("security_alerts.id", ondelete="CASCADE"),
        nullable=False,
    )
    audit_log_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit_logs.id", ondelete="CASCADE"),
        nullable=False,
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=text("now()"),
    )


class SecurityAlertReconciliationWatermark(Base):
    __tablename__ = "security_alert_reconciliation_watermarks"
    __table_args__ = (
        CheckConstraint(
            "(cursor_occurred_at IS NULL AND cursor_audit_log_id IS NULL) OR "
            "(cursor_occurred_at IS NOT NULL AND cursor_audit_log_id IS NOT NULL)",
            name="ck_security_alert_reconciliation_cursor_pair",
        ),
        CheckConstraint(
            "reconciliation_generation >= 0",
            name="ck_security_alert_reconcile_generation_nonnegative",
        ),
    )

    processor_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    activation_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    cursor_occurred_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cursor_audit_log_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    reconciliation_generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
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


class SecurityAlertReconciliationReceipt(Base):
    __tablename__ = "security_alert_reconciliation_receipts"
    __table_args__ = (
        CheckConstraint(
            "discovered_generation >= 0 AND evaluated_generation >= 0",
            name="ck_security_alert_receipt_generations_nonnegative",
        ),
        CheckConstraint(
            "evaluated_generation >= discovered_generation",
            name="ck_security_alert_receipt_evaluation_order",
        ),
    )

    processor_name: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
    )
    audit_log_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )
    discovered_generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    evaluated_generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=text("now()"),
    )


class SecurityAlertNotificationOutbox(Base):
    __tablename__ = "security_alert_notification_outbox"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_security_alert_notification_outbox_org_idempotency",
        ),
        CheckConstraint(
            "status IN ('pending', 'leased', 'succeeded', "
            "'retry_scheduled', 'dead_lettered')",
            name="ck_security_alert_notification_outbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_security_alert_notification_outbox_attempt_nonnegative",
        ),
        CheckConstraint(
            "max_attempts > 0",
            name="ck_security_alert_notification_outbox_max_attempts_positive",
        ),
        Index(
            "ix_security_alert_notification_outbox_status_retry",
            "status",
            "next_retry_at",
        ),
        Index(
            "ix_security_alert_notification_outbox_lease",
            "status",
            "lease_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default=text("'pending'")
    )
    owner_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retryable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    safe_reason_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dead_lettered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
