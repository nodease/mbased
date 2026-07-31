"""Add durable invalid schedule configuration quarantine state.

Revision ID: fb9c0d1e2f34
Revises: fa8b9c0d1e23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from apps.shared.alembic.schedule_dispatch_downgrade import (
    assert_schedule_configuration_quarantine_downgrade_is_safe,
    assert_schedule_dispatch_downgrade_is_safe,
)

from alembic import op

revision: str = "fb9c0d1e2f34"
down_revision: Union[str, Sequence[str], None] = "fa8b9c0d1e23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column("configuration_error_code", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_schedules_configuration_error_code",
        "schedules",
        ["configuration_error_code"],
    )


def downgrade() -> None:
    assert_schedule_dispatch_downgrade_is_safe(op.get_bind())
    assert_schedule_configuration_quarantine_downgrade_is_safe(op.get_bind())
    op.drop_index(
        "ix_schedules_configuration_error_code",
        table_name="schedules",
    )
    op.drop_column("schedules", "configuration_error_code")
