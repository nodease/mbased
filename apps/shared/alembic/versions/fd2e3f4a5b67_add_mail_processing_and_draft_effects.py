"""Add durable Mail message processing and Gmail draft effects.

Revision ID: fd2e3f4a5b67
Revises: fc1d2e3f4a5b
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fd2e3f4a5b67"
down_revision: Union[str, Sequence[str], None] = "fc1d2e3f4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mail_credentials",
        sa.Column("oauth_refresh_lease_owner_hash", sa.String(length=64)),
    )
    op.add_column(
        "mail_credentials",
        sa.Column("oauth_refresh_lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_mail_credentials_oauth_refresh_lease_pair",
        "mail_credentials",
        "(oauth_refresh_lease_owner_hash IS NULL) = "
        "(oauth_refresh_lease_expires_at IS NULL)",
    )
    op.create_table(
        "mail_message_processings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_node_id", sa.String(length=255), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("message_identity_hash", sa.String(length=64), nullable=False),
        sa.Column("encrypted_source_reference", sa.Text(), nullable=False),
        sa.Column("source_key_version", sa.String(length=64), nullable=False),
        sa.Column("source_algorithm", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column("lease_owner_hash", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("safe_reason_code", sa.String(length=128), nullable=True),
        sa.Column("required_effect_contract_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ack_pending', 'succeeded', "
            "'failed', 'outcome_unknown')",
            name="ck_mail_message_processings_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_mail_message_processings_attempt_count",
        ),
        sa.CheckConstraint(
            "(lease_owner_hash IS NULL) = (lease_expires_at IS NULL)",
            name="ck_mail_message_processings_lease_pair",
        ),
        sa.CheckConstraint(
            "((status IN ('pending', 'processing', 'ack_pending') "
            "AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'outcome_unknown') "
            "AND completed_at IS NOT NULL "
            "AND lease_owner_hash IS NULL AND lease_expires_at IS NULL))",
            name="ck_mail_message_processings_status_shape",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id", "organization_id"],
            ["mail_credentials.id", "mail_credentials.organization_id"],
            name="fk_mail_message_processings_credential_org",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["workflow_deployments.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "workflow_id",
            "source_node_id",
            "credential_id",
            "provider",
            "message_identity_hash",
            name="uq_mail_message_processings_logical_message",
        ),
    )
    for column in (
        "organization_id",
        "workflow_id",
        "deployment_id",
        "credential_id",
        "status",
    ):
        op.create_index(
            op.f(f"ix_mail_message_processings_{column}"),
            "mail_message_processings",
            [column],
            unique=False,
        )

    op.create_table(
        "mail_draft_effects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("processing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("operation_key_hash", sa.String(length=64), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column("encrypted_draft_reference", sa.Text(), nullable=True),
        sa.Column("draft_key_version", sa.String(length=64), nullable=True),
        sa.Column("draft_algorithm", sa.String(length=32), nullable=True),
        sa.Column("lease_owner_hash", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("safe_reason_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'succeeded', "
            "'failed_before_effect', 'outcome_unknown', 'exhausted')",
            name="ck_mail_draft_effects_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_mail_draft_effects_attempt_count"
        ),
        sa.CheckConstraint(
            "(lease_owner_hash IS NULL) = (lease_expires_at IS NULL)",
            name="ck_mail_draft_effects_lease_pair",
        ),
        sa.CheckConstraint(
            "(encrypted_draft_reference IS NULL AND draft_key_version IS NULL "
            "AND draft_algorithm IS NULL) OR "
            "(encrypted_draft_reference IS NOT NULL AND draft_key_version IS NOT NULL "
            "AND draft_algorithm IS NOT NULL)",
            name="ck_mail_draft_effects_reference_envelope",
        ),
        sa.CheckConstraint(
            "((status = 'pending' AND lease_owner_hash IS NULL "
            "AND completed_at IS NULL AND next_attempt_at IS NULL "
            "AND encrypted_draft_reference IS NULL) OR "
            "(status = 'claimed' AND lease_owner_hash IS NOT NULL "
            "AND completed_at IS NULL AND next_attempt_at IS NULL "
            "AND encrypted_draft_reference IS NULL) OR "
            "(status = 'failed_before_effect' AND lease_owner_hash IS NULL "
            "AND completed_at IS NULL AND next_attempt_at IS NOT NULL "
            "AND encrypted_draft_reference IS NULL) OR "
            "(status = 'succeeded' AND lease_owner_hash IS NULL "
            "AND completed_at IS NOT NULL AND next_attempt_at IS NULL "
            "AND encrypted_draft_reference IS NOT NULL) OR "
            "(status IN ('outcome_unknown', 'exhausted') "
            "AND lease_owner_hash IS NULL AND completed_at IS NOT NULL "
            "AND next_attempt_at IS NULL "
            "AND encrypted_draft_reference IS NULL))",
            name="ck_mail_draft_effects_status_shape",
        ),
        sa.ForeignKeyConstraint(
            ["processing_id"],
            ["mail_message_processings.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "processing_id",
            "node_id",
            "operation_key_hash",
            name="uq_mail_draft_effects_operation",
        ),
    )
    op.create_index(
        op.f("ix_mail_draft_effects_processing_id"),
        "mail_draft_effects",
        ["processing_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mail_draft_effects_status"),
        "mail_draft_effects",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_mail_draft_effects_status"), table_name="mail_draft_effects")
    op.drop_index(
        op.f("ix_mail_draft_effects_processing_id"),
        table_name="mail_draft_effects",
    )
    op.drop_table("mail_draft_effects")
    for column in (
        "status",
        "credential_id",
        "deployment_id",
        "workflow_id",
        "organization_id",
    ):
        op.drop_index(
            op.f(f"ix_mail_message_processings_{column}"),
            table_name="mail_message_processings",
        )
    op.drop_table("mail_message_processings")
    op.drop_constraint(
        "ck_mail_credentials_oauth_refresh_lease_pair",
        "mail_credentials",
        type_="check",
    )
    op.drop_column("mail_credentials", "oauth_refresh_lease_expires_at")
    op.drop_column("mail_credentials", "oauth_refresh_lease_owner_hash")
