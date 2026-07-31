"""Merge Agent Builder and internal chatbot repair migration heads.

Revision ID: c1e5f4a3c2d4
Revises: a30e1f2a43b, b9e5f4a3c2d3

Both parent revisions are already independently applicable.  This merge keeps
their schema history intact while restoring a single Alembic head.
"""

from typing import Sequence, Union

revision: str = "c1e5f4a3c2d4"
down_revision: Union[str, Sequence[str], None] = (
    "a30e1f2a43b",
    "b9e5f4a3c2d3",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
