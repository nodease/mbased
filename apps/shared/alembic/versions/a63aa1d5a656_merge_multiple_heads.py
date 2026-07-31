"""merge multiple heads

Revision ID: a63aa1d5a656
Revises: b6a7c8d9e0f1, c0d1e2f3a4b5, a1b2c3d4e5f6
Create Date: 2026-06-26 11:17:59.060889

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'a63aa1d5a656'
down_revision: Union[str, Sequence[str], None] = (
    'b6a7c8d9e0f1',
    'c0d1e2f3a4b5',
    'a1b2c3d4e5f6',
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
