"""Repair missing INTERNAL_CHATBOT deployment enum value in stamped databases.

Revision ID: b9e5f4a3c2d3
Revises: b8e5f4a3c2d2

Some local databases were stamped beyond the original INTERNAL_CHATBOT enum
migration without receiving the enum value. This repair is idempotent so
correctly migrated environments remain unchanged.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b9e5f4a3c2d3"
down_revision: Union[str, Sequence[str], None] = "b8e5f4a3c2d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE deploymenttype "
        "ADD VALUE IF NOT EXISTS 'INTERNAL_CHATBOT'"
    )


def downgrade() -> None:
    # PostgreSQL enum values are not removed automatically.
    pass
