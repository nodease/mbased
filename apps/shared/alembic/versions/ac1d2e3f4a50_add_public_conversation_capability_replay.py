"""Add bounded public Conversation capability replay storage.

Revision ID: ac1d2e3f4a50
Revises: c3d4e5f6a7b8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ac1d2e3f4a50"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_conv_idempotency_id_org",
        "conversation_idempotency_records",
        ["id", "organization_id"],
    )
    op.create_table(
        "conversation_secret_replays",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "idempotency_record_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column(
            "associated_data_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "purpose IN ('access_grant', 'purge_receipt') "
            "AND length(associated_data_digest) = 64",
            name="ck_conv_secret_replay_fields",
        ),
        sa.ForeignKeyConstraint(
            ["idempotency_record_id", "organization_id"],
            [
                "conversation_idempotency_records.id",
                "conversation_idempotency_records.organization_id",
            ],
            name="fk_conv_secret_replay_idempotency_org",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_record_id",
            name="uq_conv_secret_replay_idempotency",
        ),
    )
    op.create_index(
        "ix_conv_secret_replays_expiry",
        "conversation_secret_replays",
        ["expires_at"],
    )
    op.execute("ALTER TYPE audit_actor_type ADD VALUE IF NOT EXISTS 'public'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum label in place. Refuse before any
    # destructive DDL when either delivered history or a durable pending
    # outbox event still depends on the public actor label.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM audit_logs WHERE actor_type::text = 'public'
            ) OR EXISTS (
                SELECT 1
                FROM audit_event_outbox
                WHERE payload ->> 'actor_type' = 'public'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade public audit actor type while public audit events exist';
            END IF;
        END $$;
        """
    )
    op.drop_index(
        "ix_conv_secret_replays_expiry",
        table_name="conversation_secret_replays",
    )
    op.drop_table("conversation_secret_replays")
    op.drop_constraint(
        "uq_conv_idempotency_id_org",
        "conversation_idempotency_records",
        type_="unique",
    )
    op.execute(
        "ALTER TABLE audit_logs ALTER COLUMN actor_type TYPE VARCHAR(16) "
        "USING actor_type::text"
    )
    op.execute("DROP TYPE audit_actor_type")
    op.execute("CREATE TYPE audit_actor_type AS ENUM ('user', 'admin', 'system')")
    op.execute(
        "ALTER TABLE audit_logs ALTER COLUMN actor_type TYPE audit_actor_type "
        "USING actor_type::text::audit_actor_type"
    )
