"""Merge schedule dispatch and Knowledge safe-metadata migration heads.

Revision ID: ff4b5c6d7e89
Revises: ff3a4b5c6d78, fa7c8d9e0f12
"""

from typing import Sequence, Union

from apps.shared.alembic.schedule_dispatch_downgrade import (
    assert_schedule_configuration_quarantine_downgrade_is_safe,
    assert_schedule_dispatch_downgrade_is_safe,
)

from alembic import op

revision: str = "ff4b5c6d7e89"
down_revision: Union[str, Sequence[str], None] = (
    "ff3a4b5c6d78",
    "fa7c8d9e0f12",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join independent additive schema branches without DDL."""


def downgrade() -> None:
    """Guard the graph split even though this revision has no DDL."""
    assert_schedule_dispatch_downgrade_is_safe(op.get_bind())
    assert_schedule_configuration_quarantine_downgrade_is_safe(op.get_bind())
