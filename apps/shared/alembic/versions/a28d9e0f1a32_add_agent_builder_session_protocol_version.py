"""Add nullable Agent Builder session protocol version.

Revision ID: a28d9e0f1a32
Revises: 0f4a5b6c7d89
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a28d9e0f1a32"
down_revision: Union[str, Sequence[str], None] = "0f4a5b6c7d89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_builder_sessions",
        sa.Column("protocol_version", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_builder_sessions", "protocol_version")
