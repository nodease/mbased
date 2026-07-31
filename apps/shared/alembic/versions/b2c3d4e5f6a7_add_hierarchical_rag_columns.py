"""add hierarchical rag columns

Revision ID: b2c3d4e5f6a7
Revises: 9c1d2e3f4a67
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "9c1d2e3f4a67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("parent_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "document_chunks",
        sa.Column("chunk_level", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "document_chunks",
        sa.Column("section_path", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "document_chunks",
        sa.Column("heading", sa.String(length=512), nullable=True),
    )
    op.create_foreign_key(
        "fk_document_chunks_parent_chunk_id",
        "document_chunks",
        "document_chunks",
        ["parent_chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_document_chunks_parent_chunk_id",
        "document_chunks",
        ["parent_chunk_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_knowledge_base_id_chunk_level",
        "document_chunks",
        ["knowledge_base_id", "chunk_level"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_chunks_knowledge_base_id_chunk_level",
        table_name="document_chunks",
    )
    op.drop_index("ix_document_chunks_parent_chunk_id", table_name="document_chunks")
    op.drop_constraint(
        "fk_document_chunks_parent_chunk_id",
        "document_chunks",
        type_="foreignkey",
    )
    op.drop_column("document_chunks", "heading")
    op.drop_column("document_chunks", "section_path")
    op.drop_column("document_chunks", "chunk_level")
    op.drop_column("document_chunks", "parent_chunk_id")
