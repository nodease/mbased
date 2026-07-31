"""Merge Agent Builder and knowledge/workflow budget migration heads

Revision ID: fa5b6c7d8e90
Revises: fa4b5c6d7e80, c4d5e6f7a8b9
Create Date: 2026-07-07 22:40:00.000000

"""

from typing import Sequence, Union

revision: str = "fa5b6c7d8e90"
down_revision: Union[str, Sequence[str], None] = (
    "fa4b5c6d7e80",
    "c4d5e6f7a8b9",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
