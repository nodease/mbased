"""add durable public purge scope snapshot

Revision ID: ad2e3f4a5b61
Revises: ac1d2e3f4a50
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ad2e3f4a5b61"
down_revision: str | Sequence[str] | None = "ac1d2e3f4a50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_purge_jobs",
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "conversation_purge_jobs",
        sa.Column("deployment_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "conversation_purge_jobs",
        sa.Column("audience_kind", sa.String(length=48), nullable=True),
    )
    # Existing jobs can be backfilled while the live session still exists.
    # Already-purged legacy jobs remain an explicit unscoped legacy tombstone;
    # new application writes always persist the complete snapshot.
    op.execute(
        """
        UPDATE conversation_purge_jobs AS job
        SET deployment_id = session.deployment_id,
            deployment_version = session.deployment_version,
            audience_kind = session.audience_kind
        FROM conversation_sessions AS session
        WHERE job.session_id = session.id
          AND job.organization_id = session.organization_id
          AND job.deployment_id IS NULL
        """
    )
    op.create_check_constraint(
        "ck_conv_purge_scope_snapshot",
        "conversation_purge_jobs",
        "((deployment_id IS NULL AND deployment_version IS NULL "
        "AND audience_kind IS NULL) OR "
        "(deployment_id IS NOT NULL AND audience_kind IS NOT NULL AND "
        "((audience_kind = 'public_chatbot' "
        "AND deployment_version IS NOT NULL AND deployment_version > 0) OR "
        "(audience_kind = 'authenticated_internal_chatbot' AND "
        "(deployment_version IS NULL OR deployment_version > 0)))))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_conv_purge_scope_snapshot",
        "conversation_purge_jobs",
        type_="check",
    )
    op.drop_column("conversation_purge_jobs", "audience_kind")
    op.drop_column("conversation_purge_jobs", "deployment_version")
    op.drop_column("conversation_purge_jobs", "deployment_id")
