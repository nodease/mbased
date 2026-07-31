"""Merge Agent Builder protocol and security alert migration heads.

Revision ID: a29d0e1f2a43
Revises: a28d9e0f1a32, c7f8a9b0d123
"""

from typing import Sequence, Union

revision: str = "a29d0e1f2a43"
down_revision: Union[str, Sequence[str], None] = (
    "a28d9e0f1a32",
    "c7f8a9b0d123",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join existing additive branches without changing schema."""


def downgrade() -> None:
    """Allow Alembic to split the revision graph on downgrade."""
