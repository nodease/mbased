"""Add LLM credential encryption envelope metadata.

Revision ID: c2e8f4a91d67
Revises: ab1c2d3e4f50
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2e8f4a91d67"
down_revision: str | Sequence[str] | None = "b0c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_credentials",
        sa.Column("encryption_key_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "llm_credentials",
        sa.Column("encryption_algorithm", sa.String(length=32), nullable=True),
    )
    op.create_check_constraint(
        "ck_llm_credentials_encryption_metadata_pair",
        "llm_credentials",
        "(encryption_key_version IS NULL) = (encryption_algorithm IS NULL)",
    )
    op.create_index(
        "ix_llm_credentials_encryption_key_version",
        "llm_credentials",
        ["encryption_key_version"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    encrypted_row_exists = connection.execute(
        sa.text(
            "SELECT 1 FROM llm_credentials "
            "WHERE encryption_key_version IS NOT NULL "
            "OR encryption_algorithm IS NOT NULL LIMIT 1"
        )
    ).first()
    if encrypted_row_exists is not None:
        raise RuntimeError(
            "Cannot remove LLM credential encryption metadata while encrypted rows exist."
        )

    op.drop_index(
        "ix_llm_credentials_encryption_key_version",
        table_name="llm_credentials",
    )
    op.drop_constraint(
        "ck_llm_credentials_encryption_metadata_pair",
        "llm_credentials",
        type_="check",
    )
    op.drop_column("llm_credentials", "encryption_algorithm")
    op.drop_column("llm_credentials", "encryption_key_version")
