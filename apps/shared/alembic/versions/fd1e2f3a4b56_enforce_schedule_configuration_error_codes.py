"""Enforce safe schedule configuration quarantine codes.

Revision ID: fd1e2f3a4b56
Revises: fc0d1e2f3a45
"""

from typing import Sequence, Union

from apps.shared.alembic.schedule_dispatch_downgrade import (
    assert_schedule_configuration_quarantine_downgrade_is_safe,
    assert_schedule_dispatch_downgrade_is_safe,
)

from alembic import op

revision: str = "fd1e2f3a4b56"
down_revision: Union[str, Sequence[str], None] = "fc0d1e2f3a45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE schedules SET configuration_error_code = "
        "'schedule_configuration_invalid' "
        "WHERE configuration_error_code IS NOT NULL "
        "AND configuration_error_code <> 'schedule_configuration_invalid'"
    )
    op.create_check_constraint(
        "ck_schedules_configuration_error_code",
        "schedules",
        "configuration_error_code IS NULL OR "
        "configuration_error_code IN ('schedule_configuration_invalid')",
    )


def downgrade() -> None:
    assert_schedule_dispatch_downgrade_is_safe(op.get_bind())
    assert_schedule_configuration_quarantine_downgrade_is_safe(op.get_bind())
    op.drop_constraint(
        "ck_schedules_configuration_error_code",
        "schedules",
        type_="check",
    )
