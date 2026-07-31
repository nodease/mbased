"""add workflow node external effect attempts

Revision ID: fe3f4a5b6c78
Revises: b39e0f1a2b43
"""

from typing import Sequence, Union

import sqlalchemy as sa
from apps.shared.alembic.external_effect_downgrade import (
    assert_external_effect_downgrade_is_safe,
)
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fe3f4a5b6c78"
down_revision: Union[str, Sequence[str], None] = "b39e0f1a2b43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_node_effect_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_invocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("node_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("effect_sequence", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_contract_version", sa.String(length=64), nullable=False),
        sa.Column("provider_replay_capability", sa.String(length=16), nullable=False),
        sa.Column("result_reuse_capability", sa.String(length=16), nullable=False),
        sa.Column("effect_input_digest", sa.String(length=64), nullable=False),
        sa.Column("replay_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("replay_decision", sa.String(length=32), nullable=True),
        sa.Column("claim_owner", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column("key_version", sa.String(length=32), nullable=True),
        sa.Column("key_format_version", sa.String(length=32), nullable=True),
        sa.Column("idempotency_key_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("replay_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("provider_status_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("provider_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "effect_sequence >= 0 AND claim_generation > 0",
            name="ck_workflow_node_effect_attempts_counters",
        ),
        sa.CheckConstraint(
            "provider_replay_capability IN ('supported', 'unsupported', 'unknown') "
            "AND result_reuse_capability IN ('supported', 'unavailable') "
            "AND status IN ('prepared', 'in_flight', 'terminal') "
            "AND (outcome IS NULL OR outcome IN ('succeeded', 'failed_before_effect', 'effect_outcome_unknown')) "
            "AND (replay_decision IS NULL OR replay_decision IN ('reuse_result', 'result_unavailable', 'retry_before_effect', 'replay_same_key', 'stop'))",
            name="ck_workflow_node_effect_attempts_enums",
        ),
        sa.CheckConstraint(
            "((status = 'prepared' AND claim_owner IS NOT NULL AND claim_expires_at IS NOT NULL "
            "AND provider_started_at IS NULL AND outcome IS NULL AND replay_decision IS NULL AND terminal_at IS NULL) "
            "OR (status = 'in_flight' AND claim_owner IS NOT NULL AND claim_expires_at IS NOT NULL "
            "AND provider_started_at IS NOT NULL AND outcome IS NULL AND replay_decision IS NULL AND terminal_at IS NULL) "
            "OR (status = 'terminal' AND claim_owner IS NULL AND claim_expires_at IS NULL "
            "AND outcome IS NOT NULL AND replay_decision IS NOT NULL AND terminal_at IS NOT NULL))",
            name="ck_workflow_node_effect_attempts_status_shape",
        ),
        sa.CheckConstraint(
            "((status <> 'terminal' AND outcome IS NULL AND replay_decision IS NULL) OR "
            "(status = 'terminal' AND (((outcome = 'succeeded' AND provider_started_at IS NOT NULL "
            "AND replay_decision IN ('reuse_result', 'result_unavailable')) OR "
            "(outcome = 'failed_before_effect' AND replay_decision IN ('retry_before_effect', 'stop')) OR "
            "(outcome = 'effect_outcome_unknown' AND provider_started_at IS NOT NULL "
            "AND replay_decision IN ('replay_same_key', 'stop')))))) "
            "AND (replay_decision <> 'replay_same_key' OR provider_replay_capability = 'supported')",
            name="ck_workflow_node_effect_attempts_outcome_decision",
        ),
        sa.CheckConstraint(
            "((provider_replay_capability = 'supported' AND replay_deadline_at IS NOT NULL "
            "AND key_version IS NOT NULL AND key_format_version IS NOT NULL "
            "AND idempotency_key_fingerprint IS NOT NULL) OR "
            "(provider_replay_capability IN ('unsupported', 'unknown') AND replay_deadline_at IS NULL "
            "AND key_version IS NULL AND key_format_version IS NULL "
            "AND idempotency_key_fingerprint IS NULL))",
            name="ck_workflow_node_effect_attempts_key_contract",
        ),
        sa.CheckConstraint(
            "((status = 'terminal' AND replay_decision = 'reuse_result' AND outcome = 'succeeded' "
            "AND result_reuse_capability = 'supported' AND replay_result IS NOT NULL) OR "
            "((status <> 'terminal' OR replay_decision <> 'reuse_result') AND replay_result IS NULL))",
            name="ck_workflow_node_effect_attempts_replay_result",
        ),
        sa.CheckConstraint(
            "status = 'terminal' OR (provider_status_code IS NULL AND error_code IS NULL)",
            name="ck_workflow_node_effect_attempts_provider_summary",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ("
            "'connection_failed', 'invalid_prepared_request', "
            "'provider_call_failed', 'provider_call_finalize_failed', "
            "'provider_key_field_conflict', "
            "'provider_key_request_conflict', 'provider_rejected_request', "
            "'response_lost', 'response_malformed', 'timeout', "
            "'unexpected_provider_status')",
            name="ck_workflow_node_effect_attempts_error_code",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "execution_id",
            "node_invocation_id",
            "effect_sequence",
            name="uq_workflow_node_effect_attempts_slot",
        ),
    )
    op.create_index(
        "ix_workflow_node_effect_attempts_contract",
        "workflow_node_effect_attempts",
        ["provider", "operation", "provider_contract_version"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_node_effect_attempts_hmac_readiness",
        "workflow_node_effect_attempts",
        ["provider", "operation", "provider_contract_version", "key_version"],
        unique=False,
        postgresql_where=sa.text(
            "provider_replay_capability = 'supported' AND "
            "(status IN ('prepared', 'in_flight') OR "
            "(status = 'terminal' AND replay_decision IN ('retry_before_effect', 'replay_same_key')))"
        ),
    )
    op.create_index(
        "ix_workflow_node_effect_attempts_workflow_run_id",
        "workflow_node_effect_attempts",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_node_effect_attempts_node_run_id",
        "workflow_node_effect_attempts",
        ["node_run_id"],
        unique=False,
    )


def downgrade() -> None:
    assert_external_effect_downgrade_is_safe(op.get_bind())
    op.drop_index(
        "ix_workflow_node_effect_attempts_node_run_id",
        table_name="workflow_node_effect_attempts",
    )
    op.drop_index(
        "ix_workflow_node_effect_attempts_workflow_run_id",
        table_name="workflow_node_effect_attempts",
    )
    op.drop_index(
        "ix_workflow_node_effect_attempts_hmac_readiness",
        table_name="workflow_node_effect_attempts",
    )
    op.drop_index(
        "ix_workflow_node_effect_attempts_contract",
        table_name="workflow_node_effect_attempts",
    )
    op.drop_table("workflow_node_effect_attempts")
