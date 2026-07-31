"""Repair knowledge collection organization-scoped foreign key target.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-18 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONSTRAINT_NAME = "uq_knowledge_collections_id_organization_id"
TABLE_NAME = "knowledge_collections"
CONSTRAINT_COLUMNS = {"id", "organization_id"}


def _has_scope_unique_constraint() -> bool:
    constraints = sa.inspect(op.get_bind()).get_unique_constraints(TABLE_NAME)
    return any(
        set(constraint.get("column_names") or ()) == CONSTRAINT_COLUMNS
        for constraint in constraints
    )


def upgrade() -> None:
    # Older databases can be marked past the original migration while missing
    # this prerequisite for the composite collection foreign key.
    if not _has_scope_unique_constraint():
        op.create_unique_constraint(
            CONSTRAINT_NAME,
            TABLE_NAME,
            ["id", "organization_id"],
        )


def downgrade() -> None:
    # The original sync-job migration owns this constraint. Keep it when
    # downgrading this repair so the preceding schema remains valid.
    pass
