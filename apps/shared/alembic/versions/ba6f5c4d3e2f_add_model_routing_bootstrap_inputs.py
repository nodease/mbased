"""Add cohort examples as model routing validation inputs.

Revision ID: ba6f5c4d3e2f
Revises: c2e8f4a91d67
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "ba6f5c4d3e2f"
down_revision: Union[str, Sequence[str], None] = "c2e8f4a91d67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "llm_node_model_routing_validation_items",
        "observation_id",
        existing_type=sa.UUID(),
        nullable=True,
    )
    op.add_column(
        "llm_node_model_routing_validation_items",
        sa.Column("cohort_example_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_model_routing_validation_item_example",
        "llm_node_model_routing_validation_items",
        "llm_node_model_routing_cohort_examples",
        ["cohort_example_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_model_routing_validation_item_example_request",
        "llm_node_model_routing_validation_items",
        ["batch_id", "cohort_id", "cohort_example_id", "model_id"],
    )
    op.create_check_constraint(
        "ck_model_routing_validation_item_one_input_source",
        "llm_node_model_routing_validation_items",
        "(observation_id IS NOT NULL) <> (cohort_example_id IS NOT NULL)",
    )


def downgrade() -> None:
    # 이전 schema는 observation_id가 필수다. 합성 대표 예시만 참조하는 bootstrap
    # item은 되돌린 schema로 표현할 수 없으므로 column/constraint 복구 전에 제거한다.
    op.execute(
        sa.text(
            "DELETE FROM llm_node_model_routing_validation_items "
            "WHERE cohort_example_id IS NOT NULL"
        )
    )
    op.drop_constraint(
        "ck_model_routing_validation_item_one_input_source",
        "llm_node_model_routing_validation_items",
        type_="check",
    )
    op.drop_constraint(
        "uq_model_routing_validation_item_example_request",
        "llm_node_model_routing_validation_items",
        type_="unique",
    )
    op.drop_constraint(
        "fk_model_routing_validation_item_example",
        "llm_node_model_routing_validation_items",
        type_="foreignkey",
    )
    op.drop_column(
        "llm_node_model_routing_validation_items",
        "cohort_example_id",
    )
    op.alter_column(
        "llm_node_model_routing_validation_items",
        "observation_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
