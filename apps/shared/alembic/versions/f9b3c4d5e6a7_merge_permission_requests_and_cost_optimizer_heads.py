"""Merge permission requests and cost optimizer heads

Revision ID: f9b3c4d5e6a7
Revises: b3c4d5e6f7a8, f9a0b1c2d3e4
Create Date: 2026-07-05 21:08:00.000000

"""

from typing import Sequence, Union

revision: str = "f9b3c4d5e6a7"
down_revision: Union[str, Sequence[str], None] = (
    "b3c4d5e6f7a8",
    "f9a0b1c2d3e4",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
