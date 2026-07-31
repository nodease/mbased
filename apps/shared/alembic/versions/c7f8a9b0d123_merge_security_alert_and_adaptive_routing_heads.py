"""Merge Security Alert and adaptive routing migration heads.

Revision ID: c7f8a9b0d123
Revises: 2b6c7d8e9f02, a6f4d2c8e1b7
"""

from typing import Sequence, Union

from apps.shared.alembic.schedule_dispatch_downgrade import (
    assert_schedule_configuration_quarantine_downgrade_is_safe,
    assert_schedule_dispatch_downgrade_is_safe,
)

from alembic import op

revision: str = "c7f8a9b0d123"
down_revision: Union[str, Sequence[str], None] = (
    "2b6c7d8e9f02",
    "a6f4d2c8e1b7",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join independent additive schema branches without DDL."""


def downgrade() -> None:
    """Guard the graph split before entering either parent branch."""
    assert_schedule_dispatch_downgrade_is_safe(op.get_bind())
    assert_schedule_configuration_quarantine_downgrade_is_safe(op.get_bind())
