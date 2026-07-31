"""Add verifier-based App auth secret lifecycle state.

Revision ID: b0c1d2e3f4a5
Revises: ac2d3e4f5061
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: str | Sequence[str] | None = "ac2d3e4f5061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERIFIER_DOMAIN = b"nodease.app-auth-secret.v1\x00"
_VERIFIER_VERSION = 1
_MAX_CANDIDATE_BYTES = 512


def _legacy_verifier(raw_secret: object) -> str:
    if not isinstance(raw_secret, str):
        raise RuntimeError("App auth secret backfill found an invalid legacy value")
    try:
        encoded = raw_secret.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise RuntimeError(
            "App auth secret backfill found a non-ASCII legacy value"
        ) from exc
    if not 1 <= len(encoded) <= _MAX_CANDIDATE_BYTES:
        raise RuntimeError("App auth secret backfill found an invalid legacy length")
    return hashlib.sha256(_VERIFIER_DOMAIN + encoded).hexdigest()


def _backfill_legacy_secrets() -> None:
    bind = op.get_bind()
    last_id = None
    while True:
        if last_id is None:
            batch = bind.execute(
                sa.text(
                    "SELECT id, auth_secret, "
                    "COALESCE(updated_at, created_at, NOW()) AS rotated_at "
                    "FROM apps ORDER BY id LIMIT :batch_size"
                ),
                {"batch_size": 500},
            ).all()
        else:
            batch = bind.execute(
                sa.text(
                    "SELECT id, auth_secret, "
                    "COALESCE(updated_at, created_at, NOW()) AS rotated_at "
                    "FROM apps WHERE id > :last_id "
                    "ORDER BY id LIMIT :batch_size"
                ),
                {"last_id": last_id, "batch_size": 500},
            ).all()
        if not batch:
            break
        updates = [
            {
                "app_id": row.id,
                "verifier": _legacy_verifier(row.auth_secret),
                "verifier_version": _VERIFIER_VERSION,
                "generation": 1,
                "rotated_at": row.rotated_at,
            }
            for row in batch
        ]
        bind.execute(
            sa.text(
                "UPDATE apps SET auth_secret_verifier = :verifier, "
                "auth_secret_verifier_version = :verifier_version, "
                "auth_secret_generation = :generation, "
                "auth_secret_rotated_at = :rotated_at WHERE id = :app_id"
            ),
            updates,
        )
        last_id = batch[-1].id


def upgrade() -> None:
    op.add_column(
        "apps", sa.Column("auth_secret_verifier", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "apps", sa.Column("auth_secret_verifier_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "apps",
        sa.Column(
            "auth_secret_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "apps",
        sa.Column("auth_secret_previous_verifier", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "apps",
        sa.Column("auth_secret_previous_verifier_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "apps",
        sa.Column(
            "auth_secret_previous_valid_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "apps",
        sa.Column("auth_secret_rotated_at", sa.DateTime(timezone=True), nullable=True),
    )

    _backfill_legacy_secrets()
    op.alter_column("apps", "auth_secret", existing_type=sa.String(), nullable=True)

    op.create_check_constraint(
        "ck_apps_auth_secret_generation_nonnegative",
        "apps",
        "auth_secret_generation >= 0",
    )
    op.create_check_constraint(
        "ck_apps_auth_secret_current_state",
        "apps",
        "(auth_secret_generation = 0 AND auth_secret_verifier IS NULL "
        "AND auth_secret_verifier_version IS NULL AND auth_secret_rotated_at IS NULL "
        "AND auth_secret_previous_verifier IS NULL "
        "AND auth_secret_previous_verifier_version IS NULL "
        "AND auth_secret_previous_valid_until IS NULL) OR "
        "(auth_secret_generation > 0 AND auth_secret_verifier IS NOT NULL "
        "AND auth_secret_verifier_version IS NOT NULL "
        "AND auth_secret_rotated_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_apps_auth_secret_previous_state",
        "apps",
        "(auth_secret_previous_verifier IS NULL "
        "AND auth_secret_previous_verifier_version IS NULL "
        "AND auth_secret_previous_valid_until IS NULL) OR "
        "(auth_secret_previous_verifier IS NOT NULL "
        "AND auth_secret_previous_verifier_version IS NOT NULL "
        "AND auth_secret_previous_valid_until IS NOT NULL)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    verifier_only_row = bind.execute(
        sa.text("SELECT 1 FROM apps WHERE auth_secret IS NULL LIMIT 1")
    ).scalar()
    if verifier_only_row is not None:
        raise RuntimeError(
            "Cannot downgrade while verifier-only App auth secret rows exist"
        )

    op.drop_constraint("ck_apps_auth_secret_previous_state", "apps", type_="check")
    op.drop_constraint("ck_apps_auth_secret_current_state", "apps", type_="check")
    op.drop_constraint(
        "ck_apps_auth_secret_generation_nonnegative", "apps", type_="check"
    )
    op.alter_column("apps", "auth_secret", existing_type=sa.String(), nullable=False)
    op.drop_column("apps", "auth_secret_rotated_at")
    op.drop_column("apps", "auth_secret_previous_valid_until")
    op.drop_column("apps", "auth_secret_previous_verifier_version")
    op.drop_column("apps", "auth_secret_previous_verifier")
    op.drop_column("apps", "auth_secret_generation")
    op.drop_column("apps", "auth_secret_verifier_version")
    op.drop_column("apps", "auth_secret_verifier")
