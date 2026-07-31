"""Repair missing Security Alert episode columns in stamped databases.

Revision ID: b8e5f4a3c2d2
Revises: b7e5f4a3c2d1

Some local databases were stamped beyond the original episode-tracking
migration without applying its DDL. This migration is intentionally
idempotent so correctly migrated environments remain unchanged.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b8e5f4a3c2d2"
down_revision: Union[str, Sequence[str], None] = "b7e5f4a3c2d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE security_alerts "
        "ADD COLUMN IF NOT EXISTS episode_count INTEGER"
    )
    op.execute(
        "UPDATE security_alerts "
        "SET episode_count = 1 "
        "WHERE episode_count IS NULL"
    )
    op.execute(
        "ALTER TABLE security_alerts "
        "ALTER COLUMN episode_count SET DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE security_alerts "
        "ALTER COLUMN episode_count SET NOT NULL"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_security_alerts_episode_count_positive'
                  AND conrelid = 'security_alerts'::regclass
            ) THEN
                ALTER TABLE security_alerts
                ADD CONSTRAINT ck_security_alerts_episode_count_positive
                CHECK (episode_count >= 1);
            END IF;
        END $$;
        """
    )

    op.execute(
        "ALTER TABLE security_alerts "
        "ADD COLUMN IF NOT EXISTS last_episode_started_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "UPDATE security_alerts "
        "SET last_episode_started_at = COALESCE(first_detected_at, NOW()) "
        "WHERE last_episode_started_at IS NULL"
    )
    op.execute(
        "ALTER TABLE security_alerts "
        "ALTER COLUMN last_episode_started_at SET DEFAULT NOW()"
    )
    op.execute(
        "ALTER TABLE security_alerts "
        "ALTER COLUMN last_episode_started_at SET NOT NULL"
    )


def downgrade() -> None:
    # b7e5f4a3c2d1's corrected ancestry already includes these columns.
    # Keep the repair idempotent when traversing back to that logical schema.
    pass
