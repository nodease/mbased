"""Separate model routing learners from deployment policies.

Revision ID: c06d7e8f9a15
Revises: ae2f3a4b5c6d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c06d7e8f9a15"
down_revision: str | Sequence[str] | None = "ae2f3a4b5c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_node_model_routing_learners",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("task_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("judge_contract_hash", sa.String(length=64), nullable=False),
        sa.Column("judge_rubric_version", sa.String(length=128), nullable=False),
        sa.Column("feature_schema_version", sa.String(length=128), nullable=False),
        sa.Column("encoder_model_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "candidate_artifact",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("judged_request_count", sa.Integer(), nullable=False),
        sa.Column(
            "selected_model_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "evaluation_window",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "recent_evaluation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("local_confidence_threshold", sa.Numeric(8, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organization.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "workflow_id",
            "node_id",
            "task_fingerprint",
            "judge_contract_hash",
            name="uq_model_routing_learner_identity",
        ),
    )
    op.create_index(
        "ix_model_routing_learner_workflow_node_status",
        "llm_node_model_routing_learners",
        ["workflow_id", "node_id", "status"],
        unique=False,
    )

    op.create_table(
        "llm_node_model_routing_learner_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("learner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "artifact", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "evaluation_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("publish_reason", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["learner_id"],
            ["llm_node_model_routing_learners.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "learner_id", "version", name="uq_model_routing_learner_version"
        ),
        sa.UniqueConstraint(
            "learner_id",
            "artifact_hash",
            name="uq_model_routing_learner_artifact_hash",
        ),
    )
    op.create_index(
        "ix_llm_node_model_routing_learner_versions_learner_id",
        "llm_node_model_routing_learner_versions",
        ["learner_id"],
        unique=False,
    )

    op.add_column(
        "llm_node_model_routing_policies",
        sa.Column("learner_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "llm_node_model_routing_policies",
        sa.Column(
            "active_learner_version_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_model_routing_policy_learner",
        "llm_node_model_routing_policies",
        "llm_node_model_routing_learners",
        ["learner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_model_routing_policy_active_learner_version",
        "llm_node_model_routing_policies",
        "llm_node_model_routing_learner_versions",
        ["active_learner_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_llm_node_model_routing_policies_learner_id",
        "llm_node_model_routing_policies",
        ["learner_id"],
        unique=False,
    )
    op.create_index(
        "ix_llm_node_model_routing_policies_active_learner_version_id",
        "llm_node_model_routing_policies",
        ["active_learner_version_id"],
        unique=False,
    )

    # 구형 label/artifact는 새 학습 계약으로 복원할 수 없으므로 명시적으로 폐기한다.
    op.execute("DELETE FROM llm_node_model_routing_learning_labels")
    op.execute(
        """
        UPDATE llm_node_model_routing_policies
        SET active_policy = active_policy - 'learning'
        WHERE active_policy ? 'learning'
        """
    )
    op.drop_index(
        "ix_model_routing_learning_label_policy_processed",
        table_name="llm_node_model_routing_learning_labels",
    )
    op.drop_index(
        "ix_model_routing_learning_label_policy_status",
        table_name="llm_node_model_routing_learning_labels",
    )
    op.drop_constraint(
        "uq_model_routing_learning_label_policy_run_node",
        "llm_node_model_routing_learning_labels",
        type_="unique",
    )
    op.drop_constraint(
        "llm_node_model_routing_learning_labels_policy_id_fkey",
        "llm_node_model_routing_learning_labels",
        type_="foreignkey",
    )
    op.alter_column(
        "llm_node_model_routing_learning_labels",
        "policy_id",
        new_column_name="source_policy_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_model_routing_learning_label_source_policy",
        "llm_node_model_routing_learning_labels",
        "llm_node_model_routing_policies",
        ["source_policy_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "llm_node_model_routing_learning_labels",
        sa.Column("learner_id", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_foreign_key(
        "fk_model_routing_learning_label_learner",
        "llm_node_model_routing_learning_labels",
        "llm_node_model_routing_learners",
        ["learner_id"],
        ["id"],
        ondelete="CASCADE",
    )
    for name in ("execution_succeeded", "fallback_used"):
        op.add_column(
            "llm_node_model_routing_learning_labels",
            sa.Column(name, sa.Boolean(), nullable=True),
        )
    for name in ("schema_status", "downstream_status"):
        op.add_column(
            "llm_node_model_routing_learning_labels",
            sa.Column(name, sa.String(length=32), nullable=True),
        )
    op.create_unique_constraint(
        "uq_model_routing_learning_label_learner_run_node",
        "llm_node_model_routing_learning_labels",
        ["learner_id", "workflow_run_id", "node_id"],
    )
    op.create_index(
        "ix_model_routing_learning_label_learner_status",
        "llm_node_model_routing_learning_labels",
        ["learner_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_model_routing_learning_label_learner_processed",
        "llm_node_model_routing_learning_labels",
        ["learner_id", "learning_processed_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    # 신규 label은 필수 learner와 선택적 source policy를 사용하므로 구형 필수
    # policy_id 계약으로 무손실 복원할 수 없다. upgrade와 같은 원칙으로 폐기한다.
    op.execute("DELETE FROM llm_node_model_routing_learning_labels")
    op.drop_index(
        "ix_model_routing_learning_label_learner_processed",
        table_name="llm_node_model_routing_learning_labels",
    )
    op.drop_index(
        "ix_model_routing_learning_label_learner_status",
        table_name="llm_node_model_routing_learning_labels",
    )
    op.drop_constraint(
        "uq_model_routing_learning_label_learner_run_node",
        "llm_node_model_routing_learning_labels",
        type_="unique",
    )
    op.drop_constraint(
        "fk_model_routing_learning_label_learner",
        "llm_node_model_routing_learning_labels",
        type_="foreignkey",
    )
    op.drop_column("llm_node_model_routing_learning_labels", "learner_id")
    for name in (
        "downstream_status",
        "schema_status",
        "fallback_used",
        "execution_succeeded",
    ):
        op.drop_column("llm_node_model_routing_learning_labels", name)
    op.drop_constraint(
        "fk_model_routing_learning_label_source_policy",
        "llm_node_model_routing_learning_labels",
        type_="foreignkey",
    )
    op.alter_column(
        "llm_node_model_routing_learning_labels",
        "source_policy_id",
        new_column_name="policy_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        "llm_node_model_routing_learning_labels_policy_id_fkey",
        "llm_node_model_routing_learning_labels",
        "llm_node_model_routing_policies",
        ["policy_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_model_routing_learning_label_policy_run_node",
        "llm_node_model_routing_learning_labels",
        ["policy_id", "workflow_run_id", "node_id"],
    )
    op.create_index(
        "ix_model_routing_learning_label_policy_status",
        "llm_node_model_routing_learning_labels",
        ["policy_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_model_routing_learning_label_policy_processed",
        "llm_node_model_routing_learning_labels",
        ["policy_id", "learning_processed_at", "created_at"],
        unique=False,
    )

    op.drop_index(
        "ix_llm_node_model_routing_policies_active_learner_version_id",
        table_name="llm_node_model_routing_policies",
    )
    op.drop_index(
        "ix_llm_node_model_routing_policies_learner_id",
        table_name="llm_node_model_routing_policies",
    )
    op.drop_constraint(
        "fk_model_routing_policy_active_learner_version",
        "llm_node_model_routing_policies",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_model_routing_policy_learner",
        "llm_node_model_routing_policies",
        type_="foreignkey",
    )
    op.drop_column("llm_node_model_routing_policies", "active_learner_version_id")
    op.drop_column("llm_node_model_routing_policies", "learner_id")
    op.drop_index(
        "ix_llm_node_model_routing_learner_versions_learner_id",
        table_name="llm_node_model_routing_learner_versions",
    )
    op.drop_table("llm_node_model_routing_learner_versions")
    op.drop_index(
        "ix_model_routing_learner_workflow_node_status",
        table_name="llm_node_model_routing_learners",
    )
    op.drop_table("llm_node_model_routing_learners")
