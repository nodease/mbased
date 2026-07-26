"""Add content-free public conversation execution journal.

Revision ID: b20e1f2a3b45
Revises: b19d0e1f2a34
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b20e1f2a3b45"
down_revision: str | Sequence[str] | None = "b19d0e1f2a34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONVERSATION_EXECUTION_JOURNAL_DOWNGRADE_GUARD = (
    "Conversation execution journal rows must be removed before downgrade"
)


def upgrade() -> None:
    op.create_table(
        "conversation_workflow_execution_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "admission_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_version", sa.Integer(), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column(
            "actor_type",
            sa.String(length=16),
            server_default=sa.text("'public'"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("safe_failure_reason", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "deployment_version >= 1",
            name="ck_conv_workflow_event_deployment_version",
        ),
        sa.CheckConstraint(
            "actor_type = 'public'",
            name="ck_conv_workflow_event_public_actor",
        ),
        sa.CheckConstraint(
            "event_type IN ('execution_admitted', 'execution_running', "
            "'execution_completed', 'execution_failed', "
            "'execution_outcome_unknown')",
            name="ck_conv_workflow_event_type",
        ),
        sa.CheckConstraint(
            "(event_type IN ('execution_failed', "
            "'execution_outcome_unknown') "
            "AND safe_failure_reason IS NOT NULL) OR "
            "(event_type NOT IN ('execution_failed', "
            "'execution_outcome_unknown') AND safe_failure_reason IS NULL)",
            name="ck_conv_workflow_event_failure_reason",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_conv_workflow_event_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["admission_id"],
            ["conversation_workflow_execution_admissions.id"],
            name="fk_conv_workflow_event_admission",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["app_id"],
            ["apps.id"],
            name="fk_conv_workflow_event_app",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_conv_workflow_event_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["workflow_deployments.id"],
            name="fk_conv_workflow_event_deployment",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_conversation_workflow_execution_events",
        ),
        sa.UniqueConstraint(
            "admission_id",
            "event_type",
            name="uq_conv_workflow_event_admission_type",
        ),
    )
    op.create_index(
        "ix_conv_workflow_event_execution_created",
        "conversation_workflow_execution_events",
        ["execution_id", "created_at"],
    )
    op.create_index(
        "ix_conv_workflow_event_org_created",
        "conversation_workflow_execution_events",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE conversation_workflow_execution_events "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    has_events = bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM conversation_workflow_execution_events"
            ")"
        )
    ).scalar()
    if has_events:
        raise RuntimeError(CONVERSATION_EXECUTION_JOURNAL_DOWNGRADE_GUARD)

    op.drop_index(
        "ix_conv_workflow_event_org_created",
        table_name="conversation_workflow_execution_events",
    )
    op.drop_index(
        "ix_conv_workflow_event_execution_created",
        table_name="conversation_workflow_execution_events",
    )
    op.drop_table("conversation_workflow_execution_events")
