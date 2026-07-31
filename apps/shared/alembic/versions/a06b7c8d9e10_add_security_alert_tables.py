"""Add Security Alert persistence tables.

Revision ID: a06b7c8d9e10
Revises: fd2e3f4a5b67
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a06b7c8d9e10"
down_revision: Union[str, Sequence[str], None] = "fd2e3f4a5b67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

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


def upgrade() -> None:
    op.create_table(
        "security_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", sa.String(length=100), nullable=False),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("policy_reason", sa.String(length=128), nullable=True),
        sa.Column("detection_key", sa.String(length=255), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lifecycle_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("acknowledged_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_type", sa.String(length=32), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("occurrence_count >= 0", name="ck_security_alerts_occurrence_count_nonnegative"),
        sa.CheckConstraint("lifecycle_version >= 1", name="ck_security_alerts_lifecycle_version_positive"),
        sa.CheckConstraint("severity IN ('medium', 'high')", name="ck_security_alerts_severity"),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name="ck_security_alerts_status"),
        sa.CheckConstraint("resolution_type IS NULL OR resolution_type IN ('mitigated', 'false_positive', 'accepted_risk')", name="ck_security_alerts_resolution_type"),
        sa.CheckConstraint("first_detected_at <= last_detected_at", name="ck_security_alerts_timestamp_order"),
        sa.CheckConstraint(
            _STATUS_FIELDS_CHECK_SQL,
            name="ck_security_alerts_status_fields",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_security_alerts_organization_last_detected", "security_alerts", ["organization_id", "last_detected_at"])
    op.create_index("uq_security_alerts_active_detection_key", "security_alerts", ["detection_key"], unique=True, postgresql_where=sa.text("status IN ('open', 'acknowledged')"))

    op.create_table(
        "security_alert_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("security_alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_log_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["audit_log_id"], ["audit_logs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["security_alert_id"], ["security_alerts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("security_alert_id", "audit_log_id", name="uq_security_alert_audit_events_alert_audit"),
    )
    op.create_index("ix_security_alert_audit_events_audit_log_id", "security_alert_audit_events", ["audit_log_id"])


def downgrade() -> None:
    op.drop_index("ix_security_alert_audit_events_audit_log_id", table_name="security_alert_audit_events")
    op.drop_table("security_alert_audit_events")
    op.drop_index("uq_security_alerts_active_detection_key", table_name="security_alerts")
    op.drop_index("ix_security_alerts_organization_last_detected", table_name="security_alerts")
    op.drop_table("security_alerts")
