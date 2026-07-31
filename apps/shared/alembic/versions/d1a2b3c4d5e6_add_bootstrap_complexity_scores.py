"""Add continuous request complexity scores to routing bootstrap samples.

Revision ID: d1a2b3c4d5e6
Revises: c6f8a1b2d3e4
Create Date: 2026-07-17 16:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "c6f8a1b2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_node_model_routing_bootstrap_samples",
        sa.Column(
            "complexity_score",
            sa.Numeric(5, 2),
            nullable=False,
            server_default=sa.text("50"),
            comment="회귀형 요청 복잡도 점수(0~100)",
        ),
    )
    op.alter_column(
        "llm_node_model_routing_bootstrap_samples",
        "complexity_score",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column(
        "llm_node_model_routing_bootstrap_samples",
        "complexity_score",
    )
