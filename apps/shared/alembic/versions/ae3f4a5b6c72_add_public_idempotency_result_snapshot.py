"""add typed public idempotency result snapshot

Revision ID: ae3f4a5b6c72
Revises: ad2e3f4a5b61
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ae3f4a5b6c72"
down_revision: str | Sequence[str] | None = "ad2e3f4a5b61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_idempotency_records",
        sa.Column("result_lifecycle", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "conversation_idempotency_records",
        sa.Column("result_lifecycle_revision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "conversation_idempotency_records",
        sa.Column("result_memory_contract_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "conversation_idempotency_records",
        sa.Column("result_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversation_idempotency_records",
        sa.Column("result_previous_lifecycle", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "conversation_idempotency_records",
        sa.Column("result_previous_lifecycle_revision", sa.Integer(), nullable=True),
    )
    # Existing bounded records remain nullable because their original response
    # cannot be reconstructed safely from mutable resources after deployment.
    op.create_check_constraint(
        "ck_conv_idempotency_result_snapshot",
        "conversation_idempotency_records",
        "((result_lifecycle IS NULL "
        "AND result_lifecycle_revision IS NULL "
        "AND result_memory_contract_version IS NULL "
        "AND result_expires_at IS NULL "
        "AND result_previous_lifecycle IS NULL "
        "AND result_previous_lifecycle_revision IS NULL) OR "
        "(result_lifecycle IN "
        "('active', 'closed', 'delete_pending', 'deleted', 'expired') "
        "AND result_lifecycle_revision > 0 "
        "AND ((result_memory_contract_version IS NULL "
        "AND result_expires_at IS NULL) OR "
        "(result_memory_contract_version IS NOT NULL "
        "AND result_expires_at IS NOT NULL)) "
        "AND ((result_previous_lifecycle IS NULL "
        "AND result_previous_lifecycle_revision IS NULL) OR "
        "(result_previous_lifecycle IN "
        "('active', 'closed', 'delete_pending', 'deleted', 'expired') "
        "AND result_previous_lifecycle_revision > 0))))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_conv_idempotency_result_snapshot",
        "conversation_idempotency_records",
        type_="check",
    )
    op.drop_column("conversation_idempotency_records", "result_previous_lifecycle_revision")
    op.drop_column("conversation_idempotency_records", "result_previous_lifecycle")
    op.drop_column("conversation_idempotency_records", "result_expires_at")
    op.drop_column("conversation_idempotency_records", "result_memory_contract_version")
    op.drop_column("conversation_idempotency_records", "result_lifecycle_revision")
    op.drop_column("conversation_idempotency_records", "result_lifecycle")
