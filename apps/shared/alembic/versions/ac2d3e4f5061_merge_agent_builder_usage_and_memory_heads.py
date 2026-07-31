"""Merge Agent Builder usage and Conversation Memory heads.

Revision ID: ac2d3e4f5061
Revises: a8c9d0e1f2a3, ab1c2d3e4f50
"""

from collections.abc import Sequence

revision: str = "ac2d3e4f5061"
down_revision: str | Sequence[str] | None = (
    "a8c9d0e1f2a3",
    "ab1c2d3e4f50",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
