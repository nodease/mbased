"""add stable public replay authorization scope

Revision ID: af4a5b6c7d83
Revises: ae3f4a5b6c72
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "af4a5b6c7d83"
down_revision: str | Sequence[str] | None = "ae3f4a5b6c72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PURGE_SCOPE_CHECK = (
    "((deployment_id IS NULL AND deployment_version IS NULL "
    "AND audience_kind IS NULL AND app_id IS NULL) OR "
    "(deployment_id IS NOT NULL AND audience_kind IS NOT NULL AND "
    "((audience_kind = 'public_chatbot' "
    "AND deployment_version IS NOT NULL AND deployment_version > 0) OR "
    "(audience_kind = 'authenticated_internal_chatbot' AND "
    "(deployment_version IS NULL OR deployment_version > 0)))))"
)

_LEGACY_PURGE_SCOPE_CHECK = (
    "((deployment_id IS NULL AND deployment_version IS NULL "
    "AND audience_kind IS NULL) OR "
    "(deployment_id IS NOT NULL AND audience_kind IS NOT NULL AND "
    "((audience_kind = 'public_chatbot' "
    "AND deployment_version IS NOT NULL AND deployment_version > 0) OR "
    "(audience_kind = 'authenticated_internal_chatbot' AND "
    "(deployment_version IS NULL OR deployment_version > 0)))))"
)


def upgrade() -> None:
    op.add_column(
        "conversation_purge_jobs",
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE conversation_purge_jobs AS job
        SET app_id = session.app_id
        FROM conversation_sessions AS session
        WHERE job.session_id = session.id
          AND job.organization_id = session.organization_id
          AND job.app_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE conversation_purge_jobs AS job
        SET app_id = deployment.app_id
        FROM workflow_deployments AS deployment
        WHERE job.deployment_id = deployment.id
          AND job.app_id IS NULL
        """
    )
    op.drop_constraint(
        "ck_conv_purge_scope_snapshot",
        "conversation_purge_jobs",
        type_="check",
    )
    # A legacy job whose App and Session were already removed remains nullable
    # and therefore fail-closed. Every new domain write supplies app_id.
    op.create_check_constraint(
        "ck_conv_purge_scope_snapshot",
        "conversation_purge_jobs",
        _PURGE_SCOPE_CHECK,
    )

    op.add_column(
        "conversation_idempotency_records",
        sa.Column(
            "authorization_app_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "conversation_idempotency_records",
        sa.Column(
            "authorization_verifier_key_version",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "conversation_idempotency_records",
        sa.Column(
            "authorization_verifier_hash",
            sa.String(length=128),
            nullable=True,
        ),
    )
    # Backfill only an unambiguous still-live delete scope. Already-purged
    # legacy requests cannot be reconstructed safely and remain fail-closed.
    op.execute(
        """
        UPDATE conversation_idempotency_records AS record
        SET authorization_app_id = session.app_id,
            authorization_verifier_key_version = access_grant.verifier_key_version,
            authorization_verifier_hash = access_grant.verifier_hash
        FROM conversation_purge_jobs AS job,
             conversation_sessions AS session,
             conversation_access_grants AS access_grant
        WHERE record.operation = 'conversation.delete'
          AND record.resource_type = 'conversation_purge_job'
          AND record.resource_reference = job.id::text
          AND job.session_id = session.id
          AND job.organization_id = session.organization_id
          AND access_grant.session_id = session.id
          AND access_grant.organization_id = session.organization_id
          AND record.authorization_app_id IS NULL
          AND (
              SELECT count(*)
              FROM conversation_access_grants AS candidate
              WHERE candidate.session_id = session.id
                AND candidate.organization_id = session.organization_id
          ) = 1
        """
    )
    op.create_check_constraint(
        "ck_conv_idempotency_authorization_scope",
        "conversation_idempotency_records",
        "((authorization_app_id IS NULL "
        "AND authorization_verifier_key_version IS NULL "
        "AND authorization_verifier_hash IS NULL) OR "
        "(authorization_app_id IS NOT NULL "
        "AND authorization_verifier_key_version IS NOT NULL "
        "AND authorization_verifier_hash IS NOT NULL "
        "AND length(authorization_verifier_hash) = 64))",
    )
    op.create_index(
        "ix_conv_idempotency_authorized_replay",
        "conversation_idempotency_records",
        [
            "organization_id",
            "authorization_app_id",
            "operation",
            "idempotency_key_hash",
            "authorization_verifier_key_version",
            "authorization_verifier_hash",
        ],
        postgresql_where=sa.text("authorization_app_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conv_idempotency_authorized_replay",
        table_name="conversation_idempotency_records",
    )
    op.drop_constraint(
        "ck_conv_idempotency_authorization_scope",
        "conversation_idempotency_records",
        type_="check",
    )
    op.drop_column(
        "conversation_idempotency_records",
        "authorization_verifier_hash",
    )
    op.drop_column(
        "conversation_idempotency_records",
        "authorization_verifier_key_version",
    )
    op.drop_column(
        "conversation_idempotency_records",
        "authorization_app_id",
    )

    op.drop_constraint(
        "ck_conv_purge_scope_snapshot",
        "conversation_purge_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_conv_purge_scope_snapshot",
        "conversation_purge_jobs",
        _LEGACY_PURGE_SCOPE_CHECK,
    )
    op.drop_column("conversation_purge_jobs", "app_id")
