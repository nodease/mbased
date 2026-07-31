"""tracing 1차 모델 추가

Revision ID: f7a8b9c0d1e2
Revises: 2a28cca99a72
Create Date: 2026-06-25 00:00:00.000000

"""

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "2a28cca99a72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _jsonb():
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    now = datetime.now(timezone.utc)

    op.create_table(
        "trace_redaction_policies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.UUID(), nullable=True),
        sa.Column("redaction_enabled", sa.Boolean(), nullable=False),
        sa.Column("raw_payload_storage_enabled", sa.Boolean(), nullable=False),
        sa.Column("prompt_completion_storage_enabled", sa.Boolean(), nullable=False),
        sa.Column("pii_detection_enabled", sa.Boolean(), nullable=False),
        sa.Column("store_redacted_copy_only", sa.Boolean(), nullable=False),
        sa.Column("sensitive_headers", _jsonb(), nullable=True),
        sa.Column("sensitive_json_paths", _jsonb(), nullable=True),
        sa.Column("sensitive_keywords", _jsonb(), nullable=True),
        sa.Column("regex_rules", _jsonb(), nullable=True),
        sa.Column("replacement", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trace_redaction_policies_scope",
        "trace_redaction_policies",
        ["scope_type", "scope_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "trace_retention_policies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.UUID(), nullable=True),
        sa.Column("metadata_retention_days", sa.Integer(), nullable=False),
        sa.Column("raw_payload_retention_days", sa.Integer(), nullable=False),
        sa.Column("redacted_payload_retention_days", sa.Integer(), nullable=False),
        sa.Column("prompt_completion_retention_days", sa.Integer(), nullable=False),
        sa.Column("failed_trace_retention_days", sa.Integer(), nullable=False),
        sa.Column("retention_action", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trace_retention_policies_scope",
        "trace_retention_policies",
        ["scope_type", "scope_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "trace_visibility_policies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.UUID(), nullable=True),
        sa.Column("owner_trace_access_enabled", sa.Boolean(), nullable=False),
        sa.Column("owner_redacted_payload_access_enabled", sa.Boolean(), nullable=False),
        sa.Column("owner_raw_payload_access_enabled", sa.Boolean(), nullable=False),
        sa.Column("owner_prompt_completion_access_enabled", sa.Boolean(), nullable=False),
        sa.Column("admin_raw_payload_access_enabled", sa.Boolean(), nullable=False),
        sa.Column("admin_prompt_completion_access_enabled", sa.Boolean(), nullable=False),
        sa.Column("deny_owner_trace_access", sa.Boolean(), nullable=False),
        sa.Column("default_view_level", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trace_visibility_policies_scope",
        "trace_visibility_policies",
        ["scope_type", "scope_id", "is_active"],
        unique=False,
    )

    op.add_column("workflow_runs", sa.Column("app_id", sa.UUID(), nullable=True))
    op.add_column(
        "workflow_runs", sa.Column("correlation_id", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "workflow_runs", sa.Column("request_id", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "workflow_runs", sa.Column("workflow_task_id", sa.String(length=255), nullable=True)
    )
    op.add_column("workflow_runs", sa.Column("trace_metadata", _jsonb(), nullable=True))
    op.add_column(
        "workflow_runs",
        sa.Column("redaction_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("pii_detected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "workflow_runs", sa.Column("redaction_policy_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "workflow_runs", sa.Column("retention_policy_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "workflow_runs", sa.Column("visibility_policy_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "payload_storage_mode",
            sa.String(length=32),
            nullable=False,
            server_default="redacted_only",
        ),
    )
    op.create_foreign_key(
        "fk_workflow_runs_app_id_apps",
        "workflow_runs",
        "apps",
        ["app_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_workflow_runs_app_id", "workflow_runs", ["app_id"], unique=False)
    op.create_index(
        "ix_workflow_runs_correlation_id",
        "workflow_runs",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_runs_request_id", "workflow_runs", ["request_id"], unique=False
    )

    op.add_column("workflow_node_runs", sa.Column("duration", sa.Float(), nullable=True))
    op.add_column(
        "workflow_node_runs", sa.Column("trace_metadata", _jsonb(), nullable=True)
    )
    op.add_column(
        "workflow_node_runs",
        sa.Column("redaction_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "workflow_node_runs",
        sa.Column("pii_detected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "workflow_node_runs", sa.Column("redaction_policy_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "workflow_node_runs", sa.Column("parent_node_run_id", sa.UUID(), nullable=True)
    )
    op.add_column("workflow_node_runs", sa.Column("sequence", sa.Integer(), nullable=True))
    op.add_column(
        "workflow_node_runs",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_foreign_key(
        "fk_workflow_node_runs_parent_node_run_id",
        "workflow_node_runs",
        "workflow_node_runs",
        ["parent_node_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_workflow_node_runs_parent_node_run_id",
        "workflow_node_runs",
        ["parent_node_run_id"],
        unique=False,
    )

    op.create_table(
        "trace_payloads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("workflow_node_run_id", sa.UUID(), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("payload_kind", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("redacted_payload", _jsonb(), nullable=True),
        sa.Column("raw_payload_encrypted", sa.Text(), nullable=True),
        sa.Column("raw_payload_hash", sa.String(length=128), nullable=True),
        sa.Column("redaction_applied", sa.Boolean(), nullable=False),
        sa.Column("pii_detected", sa.Boolean(), nullable=False),
        sa.Column("secret_detected", sa.Boolean(), nullable=False),
        sa.Column("redaction_metadata", _jsonb(), nullable=True),
        sa.Column("storage_mode", sa.String(length=32), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_node_run_id"], ["workflow_node_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trace_payloads_workflow_run_id",
        "trace_payloads",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_trace_payloads_workflow_node_run_id",
        "trace_payloads",
        ["workflow_node_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_trace_payloads_payload_kind",
        "trace_payloads",
        ["payload_kind"],
        unique=False,
    )
    op.create_index("ix_trace_payloads_scope", "trace_payloads", ["scope"], unique=False)
    op.create_index(
        "ix_trace_payloads_retention_expires_at",
        "trace_payloads",
        ["retention_expires_at"],
        unique=False,
    )

    op.create_table(
        "trace_payload_access_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("payload_id", sa.UUID(), nullable=True),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
        sa.Column("view_level", sa.String(length=32), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payload_id"], ["trace_payloads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trace_payload_access_events_payload_id",
        "trace_payload_access_events",
        ["payload_id"],
        unique=False,
    )
    op.create_index(
        "ix_trace_payload_access_events_workflow_run_id",
        "trace_payload_access_events",
        ["workflow_run_id"],
        unique=False,
    )

    redaction_table = sa.table(
        "trace_redaction_policies",
        sa.column("id", sa.UUID()),
        sa.column("scope_type", sa.String()),
        sa.column("scope_id", sa.UUID()),
        sa.column("redaction_enabled", sa.Boolean()),
        sa.column("raw_payload_storage_enabled", sa.Boolean()),
        sa.column("prompt_completion_storage_enabled", sa.Boolean()),
        sa.column("pii_detection_enabled", sa.Boolean()),
        sa.column("store_redacted_copy_only", sa.Boolean()),
        sa.column("sensitive_headers", _jsonb()),
        sa.column("sensitive_json_paths", _jsonb()),
        sa.column("sensitive_keywords", _jsonb()),
        sa.column("regex_rules", _jsonb()),
        sa.column("replacement", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("updated_by", sa.UUID()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    retention_table = sa.table(
        "trace_retention_policies",
        sa.column("id", sa.UUID()),
        sa.column("scope_type", sa.String()),
        sa.column("scope_id", sa.UUID()),
        sa.column("metadata_retention_days", sa.Integer()),
        sa.column("raw_payload_retention_days", sa.Integer()),
        sa.column("redacted_payload_retention_days", sa.Integer()),
        sa.column("prompt_completion_retention_days", sa.Integer()),
        sa.column("failed_trace_retention_days", sa.Integer()),
        sa.column("retention_action", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("updated_by", sa.UUID()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    visibility_table = sa.table(
        "trace_visibility_policies",
        sa.column("id", sa.UUID()),
        sa.column("scope_type", sa.String()),
        sa.column("scope_id", sa.UUID()),
        sa.column("owner_trace_access_enabled", sa.Boolean()),
        sa.column("owner_redacted_payload_access_enabled", sa.Boolean()),
        sa.column("owner_raw_payload_access_enabled", sa.Boolean()),
        sa.column("owner_prompt_completion_access_enabled", sa.Boolean()),
        sa.column("admin_raw_payload_access_enabled", sa.Boolean()),
        sa.column("admin_prompt_completion_access_enabled", sa.Boolean()),
        sa.column("deny_owner_trace_access", sa.Boolean()),
        sa.column("default_view_level", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("updated_by", sa.UUID()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    op.bulk_insert(
        redaction_table,
        [
            {
                "id": uuid.uuid4(),
                "scope_type": "global",
                "scope_id": None,
                "redaction_enabled": True,
                "raw_payload_storage_enabled": False,
                "prompt_completion_storage_enabled": True,
                "pii_detection_enabled": True,
                "store_redacted_copy_only": True,
                "sensitive_headers": [
                    "authorization",
                    "cookie",
                    "set-cookie",
                    "x-api-key",
                    "x-auth-token",
                    "x-webhook-secret",
                    "proxy-authorization",
                ],
                "sensitive_json_paths": [],
                "sensitive_keywords": [],
                "regex_rules": [],
                "replacement": "[REDACTED]",
                "is_active": True,
                "updated_by": None,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    op.bulk_insert(
        retention_table,
        [
            {
                "id": uuid.uuid4(),
                "scope_type": "global",
                "scope_id": None,
                "metadata_retention_days": 90,
                "raw_payload_retention_days": 7,
                "redacted_payload_retention_days": 30,
                "prompt_completion_retention_days": 30,
                "failed_trace_retention_days": 90,
                "retention_action": "delete",
                "is_active": True,
                "updated_by": None,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    op.bulk_insert(
        visibility_table,
        [
            {
                "id": uuid.uuid4(),
                "scope_type": "global",
                "scope_id": None,
                "owner_trace_access_enabled": True,
                "owner_redacted_payload_access_enabled": False,
                "owner_raw_payload_access_enabled": False,
                "owner_prompt_completion_access_enabled": False,
                "admin_raw_payload_access_enabled": False,
                "admin_prompt_completion_access_enabled": False,
                "deny_owner_trace_access": False,
                "default_view_level": "metadata",
                "is_active": True,
                "updated_by": None,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trace_payload_access_events_workflow_run_id",
        table_name="trace_payload_access_events",
    )
    op.drop_index(
        "ix_trace_payload_access_events_payload_id",
        table_name="trace_payload_access_events",
    )
    op.drop_table("trace_payload_access_events")

    op.drop_index("ix_trace_payloads_retention_expires_at", table_name="trace_payloads")
    op.drop_index("ix_trace_payloads_scope", table_name="trace_payloads")
    op.drop_index("ix_trace_payloads_payload_kind", table_name="trace_payloads")
    op.drop_index("ix_trace_payloads_workflow_node_run_id", table_name="trace_payloads")
    op.drop_index("ix_trace_payloads_workflow_run_id", table_name="trace_payloads")
    op.drop_table("trace_payloads")

    op.drop_index(
        "ix_workflow_node_runs_parent_node_run_id", table_name="workflow_node_runs"
    )
    op.drop_constraint(
        "fk_workflow_node_runs_parent_node_run_id",
        "workflow_node_runs",
        type_="foreignkey",
    )
    op.drop_column("workflow_node_runs", "retry_count")
    op.drop_column("workflow_node_runs", "sequence")
    op.drop_column("workflow_node_runs", "parent_node_run_id")
    op.drop_column("workflow_node_runs", "redaction_policy_id")
    op.drop_column("workflow_node_runs", "pii_detected")
    op.drop_column("workflow_node_runs", "redaction_applied")
    op.drop_column("workflow_node_runs", "trace_metadata")
    op.drop_column("workflow_node_runs", "duration")

    op.drop_index("ix_workflow_runs_request_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_correlation_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_app_id", table_name="workflow_runs")
    op.drop_constraint("fk_workflow_runs_app_id_apps", "workflow_runs", type_="foreignkey")
    op.drop_column("workflow_runs", "payload_storage_mode")
    op.drop_column("workflow_runs", "visibility_policy_id")
    op.drop_column("workflow_runs", "retention_policy_id")
    op.drop_column("workflow_runs", "redaction_policy_id")
    op.drop_column("workflow_runs", "pii_detected")
    op.drop_column("workflow_runs", "redaction_applied")
    op.drop_column("workflow_runs", "trace_metadata")
    op.drop_column("workflow_runs", "workflow_task_id")
    op.drop_column("workflow_runs", "request_id")
    op.drop_column("workflow_runs", "correlation_id")
    op.drop_column("workflow_runs", "app_id")

    op.drop_index("ix_trace_visibility_policies_scope", table_name="trace_visibility_policies")
    op.drop_table("trace_visibility_policies")
    op.drop_index("ix_trace_retention_policies_scope", table_name="trace_retention_policies")
    op.drop_table("trace_retention_policies")
    op.drop_index("ix_trace_redaction_policies_scope", table_name="trace_redaction_policies")
    op.drop_table("trace_redaction_policies")
