"""Repair user Knowledge Base permission schema for stamped local databases.

Revision ID: fd0e1f2a3b4c
Revises: fc9a1b2c3d4e
Create Date: 2026-07-11 00:00:00.000000

Some local databases were stamped past the original user permission migration
without receiving its DDL. This repair is deliberately additive and idempotent.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fd0e1f2a3b4c"
down_revision: Union[str, Sequence[str], None] = "fc9a1b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    if not _has_table(table_name):
        return False

    return any(
        constraint["name"] == constraint_name
        for constraint in inspect(op.get_bind()).get_unique_constraints(table_name)
    )


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False

    return any(
        index["name"] == index_name
        for index in inspect(op.get_bind()).get_indexes(table_name)
    )


def _create_index_if_missing(index_name: str, columns: list[str]) -> None:
    if not _has_index("user_knowledge_permissions", index_name):
        op.create_index(index_name, "user_knowledge_permissions", columns)


def _create_user_knowledge_permissions_table() -> None:
    op.create_table(
        "user_knowledge_permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grantee_organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("knowledge_base_id", sa.UUID(), nullable=False),
        sa.Column("auth_state", sa.String(length=50), nullable=False),
        sa.Column("assigned_by", sa.UUID(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "flags",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')",
            name="ck_user_knowledge_permissions_auth_state",
        ),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_user_knowledge_permissions_flags_nonnegative",
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["grantee_organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "grantee_organization_id"],
            ["knowledge_bases.id", "knowledge_bases.organization_id"],
            name="fk_user_knowledge_permissions_knowledge_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "knowledge_base_id",
            name="uq_user_knowledge_permissions_org_user_knowledge",
        ),
    )


def upgrade() -> None:
    if not _has_unique_constraint(
        "knowledge_bases", "uq_knowledge_bases_id_organization_id"
    ):
        op.create_unique_constraint(
            "uq_knowledge_bases_id_organization_id",
            "knowledge_bases",
            ["id", "organization_id"],
        )

    if not _has_table("user_knowledge_permissions"):
        _create_user_knowledge_permissions_table()

    _create_index_if_missing("ix_user_knowledge_permissions_assigned_by", ["assigned_by"])
    _create_index_if_missing(
        "ix_user_knowledge_permissions_grantee_organization_id",
        ["grantee_organization_id"],
    )
    _create_index_if_missing(
        "ix_user_knowledge_permissions_knowledge_base_id", ["knowledge_base_id"]
    )
    _create_index_if_missing("ix_user_knowledge_permissions_user_id", ["user_id"])


def downgrade() -> None:
    # This migration repairs schema drift after the original migration was
    # already stamped. Dropping repaired objects would reintroduce the drift.
    pass
