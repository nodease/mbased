"""Add Conversation Memory runtime persistence.

Revision ID: b18c9d0e1f23
Revises: ac2d3e4f5061
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b18c9d0e1f23"
down_revision: str | Sequence[str] | None = "ac2d3e4f5061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONVERSATION_MEMORY_RUNTIME_DOWNGRADE_GUARD = (
    "Conversation Memory runtime rows or bindings must be removed before downgrade"
)

_ADMISSION_STATE_FIELDS_CHECK = (
    "(state = 'admitted' AND lease_owner IS NULL "
    "AND lease_deadline IS NULL AND attempt_id IS NULL "
    "AND result_entry_id IS NULL AND result_digest IS NULL "
    "AND safe_failure_reason IS NULL AND terminal_at IS NULL "
    "AND retention_expires_at IS NULL) OR "
    "(state = 'leased' AND lease_owner IS NOT NULL "
    "AND lease_deadline IS NOT NULL AND attempt_id IS NOT NULL "
    "AND result_entry_id IS NULL AND result_digest IS NULL "
    "AND safe_failure_reason IS NULL AND terminal_at IS NULL "
    "AND retention_expires_at IS NULL) OR "
    "(state = 'completed' AND lease_owner IS NOT NULL "
    "AND attempt_id IS NOT NULL AND result_entry_id IS NOT NULL "
    "AND length(result_digest) = 64 AND safe_failure_reason IS NULL "
    "AND terminal_at IS NOT NULL AND retention_expires_at IS NOT NULL "
    "AND retention_expires_at > terminal_at) OR "
    "(state IN ('failed', 'outcome_unknown') "
    "AND lease_owner IS NOT NULL AND attempt_id IS NOT NULL "
    "AND result_entry_id IS NULL AND result_digest IS NULL "
    "AND safe_failure_reason IS NOT NULL AND terminal_at IS NOT NULL "
    "AND retention_expires_at IS NOT NULL AND retention_expires_at > terminal_at)"
)


def upgrade() -> None:
    op.add_column(
        "conversation_turns",
        sa.Column(
            "request_fingerprint_key_version",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "conversation_turns",
        sa.Column(
            "access_grant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_conv_turns_access_grant",
        "conversation_turns",
        "conversation_access_grants",
        ["access_grant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_conversation_turns_access_grant_id",
        "conversation_turns",
        ["access_grant_id"],
    )
    op.add_column(
        "conversation_memory_entries",
        sa.Column(
            "dependency_proof_version",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_mem_entries_dependency_proof_version",
        "conversation_memory_entries",
        "dependency_proof_version IS NULL "
        "OR length(dependency_proof_version) BETWEEN 1 AND 64",
    )

    op.create_table(
        "conversation_workflow_execution_admissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("dispatch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_version", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "memory_contract_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("mapping_version", sa.String(length=64), nullable=False),
        sa.Column("memory_policy_version", sa.String(length=64), nullable=False),
        sa.Column("storage_generation", sa.Integer(), nullable=False),
        sa.Column(
            "minimum_worker_capability",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "state",
            sa.String(length=24),
            server_default=sa.text("'admitted'"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column(
            "lease_generation",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("lease_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result_digest", sa.String(length=64), nullable=True),
        sa.Column("safe_failure_reason", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "deployment_version >= 1 AND storage_generation >= 1 "
            "AND version >= 1 AND lease_generation >= 0",
            name="ck_conv_workflow_admission_versions",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_conv_workflow_admission_fingerprint",
        ),
        sa.CheckConstraint(
            "state IN ('admitted', 'leased', 'completed', 'failed', "
            "'outcome_unknown')",
            name="ck_conv_workflow_admission_state",
        ),
        sa.CheckConstraint(
            _ADMISSION_STATE_FIELDS_CHECK,
            name="ck_conv_workflow_admission_state_fields",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_conv_workflow_admission_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_conv_workflow_admission_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["app_id"],
            ["apps.id"],
            name="fk_conv_workflow_admission_app",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_conv_workflow_execution_admissions",
        ),
        sa.UniqueConstraint(
            "execution_id",
            name="uq_conv_workflow_admission_execution",
        ),
    )
    op.create_index(
        "uq_conv_workflow_admission_dispatch",
        "conversation_workflow_execution_admissions",
        ["organization_id", "dispatch_id"],
        unique=True,
    )
    op.create_index(
        "ix_conv_workflow_admission_session_terminal",
        "conversation_workflow_execution_admissions",
        ["organization_id", "session_id", "terminal_at"],
    )
    op.create_index(
        "ix_conv_workflow_admission_lease",
        "conversation_workflow_execution_admissions",
        ["state", "lease_deadline"],
    )
    op.create_index(
        "ix_conv_workflow_admission_retention",
        "conversation_workflow_execution_admissions",
        ["retention_expires_at", "id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE conversation_workflow_execution_admissions, "
            "conversation_turns, conversation_memory_entries "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    has_runtime_data = bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM conversation_workflow_execution_admissions "
            "UNION ALL SELECT 1 FROM conversation_turns "
            "WHERE request_fingerprint_key_version IS NOT NULL "
            "OR access_grant_id IS NOT NULL "
            "UNION ALL SELECT 1 FROM conversation_memory_entries "
            "WHERE dependency_proof_version IS NOT NULL"
            ")"
        )
    ).scalar()
    if has_runtime_data:
        raise RuntimeError(CONVERSATION_MEMORY_RUNTIME_DOWNGRADE_GUARD)

    op.drop_index(
        "ix_conv_workflow_admission_retention",
        table_name="conversation_workflow_execution_admissions",
    )
    op.drop_index(
        "ix_conv_workflow_admission_lease",
        table_name="conversation_workflow_execution_admissions",
    )
    op.drop_index(
        "ix_conv_workflow_admission_session_terminal",
        table_name="conversation_workflow_execution_admissions",
    )
    op.drop_index(
        "uq_conv_workflow_admission_dispatch",
        table_name="conversation_workflow_execution_admissions",
    )
    op.drop_table("conversation_workflow_execution_admissions")

    op.drop_constraint(
        "ck_mem_entries_dependency_proof_version",
        "conversation_memory_entries",
        type_="check",
    )
    op.drop_column("conversation_memory_entries", "dependency_proof_version")
    op.drop_index(
        "ix_conversation_turns_access_grant_id",
        table_name="conversation_turns",
    )
    op.drop_constraint(
        "fk_conv_turns_access_grant",
        "conversation_turns",
        type_="foreignkey",
    )
    op.drop_column("conversation_turns", "access_grant_id")
    op.drop_column("conversation_turns", "request_fingerprint_key_version")
