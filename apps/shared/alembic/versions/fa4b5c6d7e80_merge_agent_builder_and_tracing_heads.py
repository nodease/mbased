"""Merge Agent Builder and tracing migration heads

Revision ID: fa4b5c6d7e80
Revises: fa3b4c5d6e70, c0d1e2f3a4b5
Create Date: 2026-07-07 22:35:00.000000

"""

from typing import Sequence, Union

revision: str = "fa4b5c6d7e80"
down_revision: Union[str, Sequence[str], None] = (
    "fa3b4c5d6e70",
    "c0d1e2f3a4b5",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
