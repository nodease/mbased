"""Store pre-learning routing evaluation and merge current heads.

Revision ID: b05c6d7e8f94
Revises: af4a5b6c7d83, c4e5f6a7b8c9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b05c6d7e8f94"
down_revision: str | Sequence[str] | None = (
    "af4a5b6c7d83",
    "c4e5f6a7b8c9",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_node_model_routing_learning_labels",
        sa.Column(
            "local_prediction",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    for name in (
        "local_confidence",
        "local_distance_score",
        "local_margin",
    ):
        op.add_column(
            "llm_node_model_routing_learning_labels",
            sa.Column(name, sa.Numeric(8, 6), nullable=True),
        )
    op.add_column(
        "llm_node_model_routing_learning_labels",
        sa.Column("learning_processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 기존 label은 이미 동기 학습에 사용됐으므로 새 worker가 다시 학습하지 않는다.
    op.execute(
        """
        UPDATE llm_node_model_routing_learning_labels
        SET learning_processed_at = COALESCE(finalized_at, created_at)
        WHERE status IN ('accepted', 'rejected')
          AND learning_processed_at IS NULL
        """
    )
    op.create_index(
        "ix_model_routing_learning_label_policy_processed",
        "llm_node_model_routing_learning_labels",
        ["policy_id", "learning_processed_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_routing_learning_label_policy_processed",
        table_name="llm_node_model_routing_learning_labels",
    )
    op.drop_column(
        "llm_node_model_routing_learning_labels",
        "learning_processed_at",
    )
    for name in (
        "local_margin",
        "local_distance_score",
        "local_confidence",
        "local_prediction",
    ):
        op.drop_column("llm_node_model_routing_learning_labels", name)
