"""Add Security Alert query indexes.

Revision ID: a17c8d9e0f21
Revises: a06b7c8d9e10
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a17c8d9e0f21"
down_revision: Union[str, Sequence[str], None] = "a06b7c8d9e10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_security_alerts_org_status_detected",
        "security_alerts",
        ["organization_id", "status", "last_detected_at", "id"],
    )
    op.create_index(
        "ix_security_alerts_org_severity_detected",
        "security_alerts",
        ["organization_id", "severity", "last_detected_at", "id"],
    )
    op.create_index(
        "ix_security_alerts_org_rule_detected",
        "security_alerts",
        ["organization_id", "rule_id", "last_detected_at", "id"],
    )
    op.create_index(
        "ix_security_alerts_org_actor_detected",
        "security_alerts",
        ["organization_id", "subject_actor_id", "last_detected_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_security_alerts_org_actor_detected", table_name="security_alerts")
    op.drop_index("ix_security_alerts_org_rule_detected", table_name="security_alerts")
    op.drop_index(
        "ix_security_alerts_org_severity_detected",
        table_name="security_alerts",
    )
    op.drop_index("ix_security_alerts_org_status_detected", table_name="security_alerts")
