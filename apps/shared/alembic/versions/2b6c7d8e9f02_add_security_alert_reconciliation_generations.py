"""Add bounded Security Alert reconciliation generations.

Revision ID: 2b6c7d8e9f02
Revises: 1a5b6c7d8e91
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "2b6c7d8e9f02"
down_revision: Union[str, Sequence[str], None] = "1a5b6c7d8e91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "security_alert_reconciliation_watermarks",
        sa.Column(
            "reconciliation_generation",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_security_alert_reconcile_generation_nonnegative",
        "security_alert_reconciliation_watermarks",
        "reconciliation_generation >= 0",
    )
    op.add_column(
        "security_alert_reconciliation_receipts",
        sa.Column(
            "discovered_generation",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "security_alert_reconciliation_receipts",
        sa.Column(
            "evaluated_generation",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_security_alert_receipt_generations_nonnegative",
        "security_alert_reconciliation_receipts",
        "discovered_generation >= 0 AND evaluated_generation >= 0",
    )
    op.create_check_constraint(
        "ck_security_alert_receipt_evaluation_order",
        "security_alert_reconciliation_receipts",
        "evaluated_generation >= discovered_generation",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_security_alert_receipt_evaluation_order",
        "security_alert_reconciliation_receipts",
        type_="check",
    )
    op.drop_constraint(
        "ck_security_alert_receipt_generations_nonnegative",
        "security_alert_reconciliation_receipts",
        type_="check",
    )
    op.drop_column(
        "security_alert_reconciliation_receipts",
        "evaluated_generation",
    )
    op.drop_column(
        "security_alert_reconciliation_receipts",
        "discovered_generation",
    )
    op.drop_constraint(
        "ck_security_alert_reconcile_generation_nonnegative",
        "security_alert_reconciliation_watermarks",
        type_="check",
    )
    op.drop_column(
        "security_alert_reconciliation_watermarks",
        "reconciliation_generation",
    )
