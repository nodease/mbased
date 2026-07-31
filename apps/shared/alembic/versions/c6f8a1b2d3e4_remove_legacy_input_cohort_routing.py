"""Remove retired input-cohort model-routing storage.

Revision ID: c6f8a1b2d3e4
Revises: bd9e0f1a2b35
Create Date: 2026-07-17 12:00:00.000000

The active runtime now uses bootstrap difficulty classification, global model
profiles, and per-policy operational performance. Semantic input cohorts,
embeddings, replay batches, and their capacity setting are no longer read.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c6f8a1b2d3e4"
down_revision: Union[str, Sequence[str], None] = "bd9e0f1a2b35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEGACY_TABLES = (
    "llm_node_model_routing_validation_cost_events",
    "llm_node_model_routing_validation_items",
    "llm_node_model_routing_validation_budget_months",
    "llm_node_model_routing_validation_batches",
    "llm_node_model_routing_model_evidence",
    "llm_node_model_routing_observations",
    "llm_node_model_routing_cohort_examples",
    "llm_node_model_routing_cohorts",
)


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _policy_column_names() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "llm_node_model_routing_policies"
        )
    }


def upgrade() -> None:
    # Old persisted policies can contain semantic_router rules whose cohort rows
    # are being removed. Deleting only those policy rows makes the next runtime
    # resolve a fresh bootstrap/prior policy instead of evaluating stale routes.
    op.execute(
        sa.text(
            """
            DELETE FROM llm_node_model_routing_policies
            WHERE active_policy ? 'semantic_router'
               OR active_policy ->> 'strategy_id' = 'semantic_cohort_v1'
            """
        )
    )
    for table_name in _LEGACY_TABLES:
        if _has_table(table_name):
            op.drop_table(table_name)

    if "max_cohorts" in _policy_column_names():
        op.drop_column("llm_node_model_routing_policies", "max_cohorts")


def downgrade() -> None:
    # This migration removes persisted cohort evidence and its tables. Adding
    # only max_cohorts back would leave an older application revision with
    # missing tables, so require a database restore for a real rollback.
    raise NotImplementedError(
        "The retired input-cohort routing data was dropped. Restore a database "
        "backup before downgrading to an application revision that requires it."
    )
