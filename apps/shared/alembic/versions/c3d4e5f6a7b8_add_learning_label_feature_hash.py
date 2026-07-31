"""Persist safe routing feature hashes for accepted Judge decision reuse.

Revision ID: c3d4e5f6a7b8
Revises: f5b6c7d8e9fa
Create Date: 2026-07-19 13:40:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "f5b6c7d8e9fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_node_model_routing_learning_labels",
        sa.Column("routing_feature_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_node_model_routing_learning_labels", "routing_feature_hash")
