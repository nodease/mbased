"""Merge schedule dispatch and model routing migration heads.

Revision ID: fe2f3a4b5c67
Revises: fd1e2f3a4b56, fb8c9d0e1f23
"""

from typing import Sequence, Union

from apps.shared.alembic.schedule_dispatch_downgrade import (
    assert_schedule_configuration_quarantine_downgrade_is_safe,
    assert_schedule_dispatch_downgrade_is_safe,
)

from alembic import op

revision: str = "fe2f3a4b5c67"
down_revision: Union[str, Sequence[str], None] = (
    "fd1e2f3a4b56",
    "fb8c9d0e1f23",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join independent additive schema branches without DDL."""


def downgrade() -> None:
    """Guard the graph split before entering either parent branch."""
    assert_schedule_dispatch_downgrade_is_safe(op.get_bind())
    assert_schedule_configuration_quarantine_downgrade_is_safe(op.get_bind())
