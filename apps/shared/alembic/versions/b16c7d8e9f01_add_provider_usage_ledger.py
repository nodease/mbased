"""Add durable provider usage ledger and compatibility projection identity.

Revision ID: b16c7d8e9f01
Revises: c06d7e8f9a15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b16c7d8e9f01"
down_revision: str | Sequence[str] | None = "c06d7e8f9a15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_usage_operations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_version", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column(
            "container_path",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("node_invocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "execution_admission_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("provider_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("capability_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capability_revision", sa.Integer(), nullable=False),
        sa.Column("capability_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_api_id", sa.String(length=255), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_revision", sa.String(length=64), nullable=False),
        sa.Column("relation_revision", sa.String(length=64), nullable=False),
        sa.Column("egress_revision", sa.String(length=64), nullable=False),
        sa.Column("pricing_revision", sa.String(length=64), nullable=False),
        sa.Column("input_price_per_1k", sa.Numeric(20, 9), nullable=False),
        sa.Column("output_price_per_1k", sa.Numeric(20, 9), nullable=False),
        sa.Column("input_token_cap", sa.Integer(), nullable=False),
        sa.Column("output_token_cap", sa.Integer(), nullable=False),
        sa.Column("cost_cap_microusd", sa.BigInteger(), nullable=False),
        sa.Column("admitted_input_tokens", sa.Integer(), nullable=False),
        sa.Column("admitted_output_tokens", sa.Integer(), nullable=False),
        sa.Column("execution_subject_kind", sa.String(length=32), nullable=False),
        sa.Column("execution_subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "credential_principal_kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
        sa.Column(
            "credential_principal_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("billing_principal_kind", sa.String(length=32), nullable=False),
        sa.Column("billing_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_actor_kind", sa.String(length=32), nullable=False),
        sa.Column("audit_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'intent'"),
        ),
        sa.Column(
            "state_version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("safe_reason_code", sa.String(length=64), nullable=True),
        sa.Column(
            "intent_created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("provider_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "usage_revision", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "cost_optimizer_candidate_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "projection_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "projected_usage_log_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("projected_usage_revision", sa.Integer(), nullable=True),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "projection_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "projection_next_attempt_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("projection_reason_code", sa.String(length=64), nullable=True),
        sa.Column("audit_event_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_provider_usage_operation_organization",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "provider_attempt_id",
            "purpose",
            name="uq_provider_usage_operation_attempt",
        ),
        sa.UniqueConstraint(
            "audit_event_id",
            name="uq_provider_usage_operation_audit_event",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "purpose IN ('main_generation', 'memory_summary') "
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
        sa.CheckConstraint(
            "jsonb_typeof(container_path) = 'array'",
            name="ck_provider_usage_operation_location",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "(state = 'intent' AND provider_started_at IS NULL "
            "AND terminal_at IS NULL AND audit_event_id IS NULL) "
            "OR (state = 'provider_started' AND provider_started_at IS NOT NULL "
            "AND terminal_at IS NULL AND audit_event_id IS NULL) "
            "OR (state IN ('succeeded', 'failed_definitive', 'outcome_unknown') "
            "AND provider_started_at IS NOT NULL AND terminal_at IS NOT NULL "
            "AND audit_event_id IS NOT NULL)",
            name="ck_provider_usage_operation_timestamps",
        ),
        sa.CheckConstraint(
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
    )
    op.create_index(
        "ix_provider_usage_operation_projection",
        "provider_usage_operations",
        ["organization_id", "state", "projection_status", "intent_created_at"],
    )
    op.create_index(
        "ix_provider_usage_operation_budget_period",
        "provider_usage_operations",
        ["organization_id", "workflow_id", "provider_started_at", "state"],
    )
    op.create_index(
        "ix_provider_usage_operation_workflow_budget_period",
        "provider_usage_operations",
        ["workflow_id", "provider_started_at"],
        postgresql_where=sa.text(
            "provider_started_at IS NOT NULL "
            "AND purpose IN ('main_generation', 'memory_summary') "
            "AND state IN ('provider_started', 'succeeded', 'outcome_unknown')"
        ),
    )
    op.create_index(
        "ix_provider_usage_operation_subject_cost_period",
        "provider_usage_operations",
        ["execution_subject_id", "provider_started_at"],
        postgresql_where=sa.text(
            "execution_subject_kind = 'user' "
            "AND execution_subject_id IS NOT NULL "
            "AND provider_started_at IS NOT NULL "
            "AND purpose IN ('main_generation', 'memory_summary') "
            "AND state = 'succeeded'"
        ),
    )
    op.create_index(
        "ix_provider_usage_operation_projection_recovery",
        "provider_usage_operations",
        ["provider_started_at", "id"],
        postgresql_where=sa.text(
            "state = 'succeeded' AND projection_status IN "
            "('pending', 'retryable_failure', 'awaiting_workflow_run')"
        ),
    )
    op.create_index(
        "ix_provider_usage_operation_started_recovery",
        "provider_usage_operations",
        ["provider_started_at", "id"],
        postgresql_where=sa.text("state = 'provider_started'"),
    )

    op.create_table(
        "provider_usage_corrections",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correction_key", sa.String(length=128), nullable=False),
        sa.Column("base_usage_revision", sa.Integer(), nullable=False),
        sa.Column("resulting_usage_revision", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["provider_usage_operations.id"],
            name="fk_provider_usage_correction_operation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "correction_key",
            name="uq_provider_usage_correction_key",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "resulting_usage_revision",
            name="uq_provider_usage_correction_revision",
        ),
        sa.CheckConstraint(
            "base_usage_revision >= 1 "
            "AND resulting_usage_revision = base_usage_revision + 1 "
            "AND prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND total_cost_microusd >= 0 AND latency_ms >= 0 "
            "AND source IN ('provider_reconciliation', 'billing_import', 'operator') "
            "AND reason_code IN ('provider_reported_usage', 'billing_reconciliation')",
            name="ck_provider_usage_correction_values",
        ),
    )

    op.add_column(
        "llm_usage_logs",
        sa.Column(
            "provider_usage_operation_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.add_column(
        "llm_usage_logs",
        sa.Column("provider_usage_revision", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_llm_usage_logs_provider_usage_projection",
        "llm_usage_logs",
        "(provider_usage_operation_id IS NULL AND provider_usage_revision IS NULL) "
        "OR (provider_usage_operation_id IS NOT NULL "
        "AND provider_usage_revision IS NOT NULL AND provider_usage_revision >= 1)",
    )
    op.create_index(
        "uq_llm_usage_logs_provider_usage_operation",
        "llm_usage_logs",
        ["provider_usage_operation_id"],
        unique=True,
    )
    op.drop_constraint(
        "llm_usage_logs_workflow_id_fkey",
        "llm_usage_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "llm_usage_logs_workflow_id_fkey",
        "llm_usage_logs",
        "workflows",
        ["workflow_id"],
        ["id"],
        ondelete="SET NULL",
    )


def _require_empty_canonical_ledger_for_downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE provider_usage_operations IN ACCESS EXCLUSIVE MODE")
    )
    has_canonical_rows = connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM provider_usage_operations)")
    ).scalar_one()
    if has_canonical_rows:
        raise RuntimeError(
            "provider usage ledger downgrade requires an empty canonical ledger"
        )


def downgrade() -> None:
    _require_empty_canonical_ledger_for_downgrade()
    op.drop_constraint(
        "llm_usage_logs_workflow_id_fkey",
        "llm_usage_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "llm_usage_logs_workflow_id_fkey",
        "llm_usage_logs",
        "workflows",
        ["workflow_id"],
        ["id"],
    )
    op.drop_index(
        "uq_llm_usage_logs_provider_usage_operation", table_name="llm_usage_logs"
    )
    op.drop_constraint(
        "ck_llm_usage_logs_provider_usage_projection",
        "llm_usage_logs",
        type_="check",
    )
    op.drop_column("llm_usage_logs", "provider_usage_revision")
    op.drop_column("llm_usage_logs", "provider_usage_operation_id")
    op.drop_table("provider_usage_corrections")
    op.drop_index(
        "ix_provider_usage_operation_started_recovery",
        table_name="provider_usage_operations",
    )
    op.drop_index(
        "ix_provider_usage_operation_projection_recovery",
        table_name="provider_usage_operations",
    )
    op.drop_index(
        "ix_provider_usage_operation_subject_cost_period",
        table_name="provider_usage_operations",
    )
    op.drop_index(
        "ix_provider_usage_operation_workflow_budget_period",
        table_name="provider_usage_operations",
    )
    op.drop_index(
        "ix_provider_usage_operation_budget_period",
        table_name="provider_usage_operations",
    )
    op.drop_index(
        "ix_provider_usage_operation_projection",
        table_name="provider_usage_operations",
    )
    op.drop_table("provider_usage_operations")
