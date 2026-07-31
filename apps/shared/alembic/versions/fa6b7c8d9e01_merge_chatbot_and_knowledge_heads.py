"""Merge chatbot deployment and knowledge migration heads

Revision ID: fa6b7c8d9e01
Revises: d3e4f5a6b7c8, fa5b6c7d8e90
Create Date: 2026-07-08 12:00:00.000000

"""

from typing import Sequence, Union

revision: str = "fa6b7c8d9e01"
down_revision: Union[str, Sequence[str], None] = (
    "d3e4f5a6b7c8",
    "fa5b6c7d8e90",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
