"""Add global model routing profiles.

Revision ID: bd9e0f1a2b35
Revises: bc8d9e0f1a24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "bd9e0f1a2b35"
down_revision: Union[str, Sequence[str], None] = "bc8d9e0f1a24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_model_routing_global_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("llm_model_id", sa.UUID(), nullable=False),
        sa.Column("capability_tier", sa.String(length=16), nullable=False),
        sa.Column(
            "quality_by_difficulty",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "uncertainty_by_difficulty",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "expected_latency_ms_by_input_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("fallback_rate", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("prior_strength", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("profile_version", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["llm_model_id"], ["llm_models.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "llm_model_id", name="uq_model_routing_global_profile_llm_model"
        ),
    )
    op.create_index(
        "ix_llm_model_routing_global_profiles_llm_model_id",
        "llm_model_routing_global_profiles",
        ["llm_model_id"],
        unique=False,
    )
    op.create_index(
        "ix_model_routing_global_profile_source_active",
        "llm_model_routing_global_profiles",
        ["source", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_routing_global_profile_source_active",
        table_name="llm_model_routing_global_profiles",
    )
    op.drop_index(
        "ix_llm_model_routing_global_profiles_llm_model_id",
        table_name="llm_model_routing_global_profiles",
    )
    op.drop_table("llm_model_routing_global_profiles")
