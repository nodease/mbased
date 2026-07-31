"""Merge Agent Builder protocol and Security Alert repair heads.

Revision ID: a30e1f2a43b
Revises: a29d0e1f2a43, b8e5f4a3c2d2

The Agent Builder protocol migration merged the historical security-alert
branchpoint but did not include the later security-alert repair revision. This
revision restores one Alembic head without changing the schema.
"""

from typing import Sequence, Union

revision: str = "a30e1f2a43b"
down_revision: Union[str, Sequence[str], None] = (
    "a29d0e1f2a43",
    "b8e5f4a3c2d2",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
