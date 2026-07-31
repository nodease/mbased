"""Add Security Alert episode tracking.

Revision ID: fe4a5b6c7d89
Revises: fd0e1f2a3b4c
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "fe4a5b6c7d89"
down_revision: Union[str, Sequence[str], None] = "fd0e1f2a3b4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "security_alerts",
        sa.Column(
            "episode_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "security_alerts",
        sa.Column(
            "last_episode_started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE security_alerts "
            "SET last_episode_started_at = first_detected_at"
        )
    )
    op.create_check_constraint(
        "ck_security_alerts_episode_count_positive",
        "security_alerts",
        "episode_count >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_security_alerts_episode_count_positive",
        "security_alerts",
        type_="check",
    )
    op.drop_column("security_alerts", "last_episode_started_at")
    op.drop_column("security_alerts", "episode_count")
