"""Add Agent Builder session request draft tables

Revision ID: fa3b4c5d6e70
Revises: fa2b3c4d5e6f
Create Date: 2026-07-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fa3b4c5d6e70"
down_revision: Union[str, Sequence[str], None] = "fa2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_builder_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_builder_sessions_user_org",
        "agent_builder_sessions",
        ["user_id", "organization_id"],
    )
    op.create_index(
        "ix_agent_builder_sessions_organization_id",
        "agent_builder_sessions",
        ["organization_id"],
    )
    op.create_index(
        "ix_agent_builder_sessions_user_id",
        "agent_builder_sessions",
        ["user_id"],
    )

    op.create_table(
        "agent_builder_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message_summary", sa.Text(), nullable=True),
        sa.Column(
            "structured_request",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "response_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_builder_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_builder_requests_session_status",
        "agent_builder_requests",
        ["session_id", "status"],
    )
    op.create_index("ix_agent_builder_requests_session_id", "agent_builder_requests", ["session_id"])
    op.create_index("ix_agent_builder_requests_organization_id", "agent_builder_requests", ["organization_id"])
    op.create_index("ix_agent_builder_requests_user_id", "agent_builder_requests", ["user_id"])
    op.create_index("ix_agent_builder_requests_status", "agent_builder_requests", ["status"])

    op.create_table(
        "agent_builder_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_mode", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("preview_graph", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "node_detail_previews",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("validation_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "draft_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("base_graph_hash", sa.String(length=128), nullable=True),
        sa.Column("base_workflow_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="ready", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(
            ["request_id"], ["agent_builder_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_builder_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_agent_builder_drafts_session_status",
        "agent_builder_drafts",
        ["session_id", "status"],
    )
    op.create_index("ix_agent_builder_drafts_session_id", "agent_builder_drafts", ["session_id"])
    op.create_index("ix_agent_builder_drafts_organization_id", "agent_builder_drafts", ["organization_id"])
    op.create_index("ix_agent_builder_drafts_user_id", "agent_builder_drafts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_builder_drafts_user_id", table_name="agent_builder_drafts")
    op.drop_index(
        "ix_agent_builder_drafts_organization_id", table_name="agent_builder_drafts"
    )
    op.drop_index("ix_agent_builder_drafts_session_id", table_name="agent_builder_drafts")
    op.drop_index(
        "ix_agent_builder_drafts_session_status", table_name="agent_builder_drafts"
    )
    op.drop_table("agent_builder_drafts")

    op.drop_index("ix_agent_builder_requests_status", table_name="agent_builder_requests")
    op.drop_index("ix_agent_builder_requests_user_id", table_name="agent_builder_requests")
    op.drop_index(
        "ix_agent_builder_requests_organization_id", table_name="agent_builder_requests"
    )
    op.drop_index(
        "ix_agent_builder_requests_session_id", table_name="agent_builder_requests"
    )
    op.drop_index(
        "ix_agent_builder_requests_session_status", table_name="agent_builder_requests"
    )
    op.drop_table("agent_builder_requests")

    op.drop_index("ix_agent_builder_sessions_user_id", table_name="agent_builder_sessions")
    op.drop_index(
        "ix_agent_builder_sessions_organization_id", table_name="agent_builder_sessions"
    )
    op.drop_index(
        "ix_agent_builder_sessions_user_org", table_name="agent_builder_sessions"
    )
    op.drop_table("agent_builder_sessions")
