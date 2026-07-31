"""Backfill organization memberships

Revision ID: 9c1d2e3f4a67
Revises: 9c1d2e3f4a66
Create Date: 2026-06-30 00:00:01.000000

"""

import uuid
from typing import Any, Iterator, Mapping, Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c1d2e3f4a67"
down_revision: Union[str, Sequence[str], None] = "9c1d2e3f4a66"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORGANIZATION_MEMBERSHIP_ACTIVE = "active"
ORGANIZATION_AUTH_MEMBER = "member"
ORGANIZATION_AUTH_MANAGER = "manager"
BATCH_SIZE = 1000

metadata = sa.MetaData()

organization_memberships = sa.Table(
    "organization_memberships",
    metadata,
    sa.Column("id", sa.UUID()),
    sa.Column("organization_id", sa.UUID()),
    sa.Column("user_id", sa.UUID()),
    sa.Column("membership_state", sa.String(length=50)),
    sa.Column("organization_auth_state", sa.String(length=50)),
    sa.Column("invited_by", sa.UUID()),
    sa.Column("invited_at", sa.DateTime(timezone=True)),
    sa.Column("accepted_at", sa.DateTime(timezone=True)),
    sa.Column("removed_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
    sa.Column("options", postgresql.JSONB(astext_type=sa.Text())),
    sa.Column("flags", sa.BigInteger()),
)


def _fail_if_rows(bind: Any, label: str, query: str) -> None:
    rows = bind.execute(sa.text(query)).mappings().all()
    if not rows:
        return

    sample = ", ".join(str(dict(row)) for row in rows[:10])
    raise RuntimeError(
        f"{label}: sampled {len(rows)} row(s). sample: {sample}"
    )


def _iter_query_batches(
    bind: Any,
    query: str,
) -> Iterator[Sequence[Mapping[str, Any]]]:
    result = bind.execute(sa.text(query)).mappings()
    while True:
        batch = result.fetchmany(BATCH_SIZE)
        if not batch:
            break
        yield batch


def _precheck_source_data(bind: Any) -> None:
    _fail_if_rows(
        bind,
        "team_memberships references missing organization",
        """
        SELECT tm.id, tm.grantee_organization_id
        FROM team_memberships tm
        LEFT JOIN organization o ON o.id = tm.grantee_organization_id
        WHERE o.id IS NULL
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "team_memberships references missing user",
        """
        SELECT tm.id, tm.user_id
        FROM team_memberships tm
        LEFT JOIN users u ON u.id = tm.user_id
        WHERE u.id IS NULL
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "team_memberships.assigned_at is null",
        """
        SELECT tm.id
        FROM team_memberships tm
        WHERE tm.assigned_at IS NULL
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "organization.created_by references missing user",
        """
        SELECT o.id, o.created_by
        FROM organization o
        LEFT JOIN users u ON u.id = o.created_by
        WHERE u.id IS NULL
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "organization.managed_by references missing user",
        """
        SELECT o.id, o.managed_by
        FROM organization o
        LEFT JOIN users u ON u.id = o.managed_by
        WHERE o.managed_by IS NOT NULL
          AND u.id IS NULL
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "organization.created_at is null",
        """
        SELECT o.id
        FROM organization o
        WHERE o.created_at IS NULL
        LIMIT 10
        """,
    )


def _iter_team_membership_batches(
    bind: Any,
) -> Iterator[Sequence[Mapping[str, Any]]]:
    return _iter_query_batches(
        bind,
        """
            SELECT DISTINCT ON (tm.grantee_organization_id, tm.user_id)
                tm.grantee_organization_id AS organization_id,
                tm.user_id AS user_id,
                COALESCE(assigner.id, o.created_by) AS invited_by,
                tm.assigned_at AS invited_at,
                tm.assigned_at AS accepted_at,
                tm.assigned_at AS created_at,
                tm.assigned_at AS updated_at
            FROM team_memberships tm
            JOIN organization o ON o.id = tm.grantee_organization_id
            LEFT JOIN users assigner ON assigner.id = tm.assigned_by
            ORDER BY
                tm.grantee_organization_id,
                tm.user_id,
                tm.assigned_at ASC,
                tm.id ASC
            """
    )


def _iter_creator_batches(bind: Any) -> Iterator[Sequence[Mapping[str, Any]]]:
    return _iter_query_batches(
        bind,
        """
            SELECT
                o.id AS organization_id,
                o.created_by AS user_id,
                o.created_by AS invited_by,
                o.created_at AS invited_at,
                o.created_at AS accepted_at,
                o.created_at AS created_at,
                o.created_at AS updated_at
            FROM organization o
            """
    )


def _iter_manager_batches(bind: Any) -> Iterator[Sequence[Mapping[str, Any]]]:
    return _iter_query_batches(
        bind,
        """
            SELECT
                o.id AS organization_id,
                o.managed_by AS user_id,
                o.created_by AS invited_by,
                o.created_at AS invited_at,
                o.created_at AS accepted_at,
                o.created_at AS created_at,
                o.created_at AS updated_at
            FROM organization o
            WHERE o.managed_by IS NOT NULL
            """
    )


def _membership_values(
    row: Mapping[str, Any],
    organization_auth_state: str,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "organization_id": row["organization_id"],
        "user_id": row["user_id"],
        "membership_state": ORGANIZATION_MEMBERSHIP_ACTIVE,
        "organization_auth_state": organization_auth_state,
        "invited_by": row["invited_by"],
        "invited_at": row["invited_at"],
        "accepted_at": row["accepted_at"],
        "removed_at": None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "options": {},
        "flags": 0,
    }


def _upsert_memberships(
    bind: Any,
    row_batches: Iterator[Sequence[Mapping[str, Any]]],
    organization_auth_state: str,
) -> None:
    target = organization_memberships.c

    for batch in row_batches:
        values = [
            _membership_values(row, organization_auth_state)
            for row in batch
        ]
        if not values:
            continue

        insert_stmt = pg_insert(organization_memberships).values(values)
        excluded = insert_stmt.excluded

        update_set = {
            "membership_state": ORGANIZATION_MEMBERSHIP_ACTIVE,
            "organization_auth_state": sa.case(
                (
                    target.organization_auth_state == ORGANIZATION_AUTH_MANAGER,
                    ORGANIZATION_AUTH_MANAGER,
                ),
                else_=organization_auth_state,
            ),
            "invited_by": sa.func.coalesce(target.invited_by, excluded.invited_by),
            "invited_at": sa.func.coalesce(target.invited_at, excluded.invited_at),
            "accepted_at": sa.func.coalesce(
                target.accepted_at,
                excluded.accepted_at,
            ),
            "removed_at": None,
            "updated_at": sa.func.now(),
        }
        where_clause = sa.or_(
            target.membership_state != ORGANIZATION_MEMBERSHIP_ACTIVE,
            target.removed_at.is_not(None),
            sa.and_(target.invited_by.is_(None), excluded.invited_by.is_not(None)),
            sa.and_(target.invited_at.is_(None), excluded.invited_at.is_not(None)),
            sa.and_(target.accepted_at.is_(None), excluded.accepted_at.is_not(None)),
        )

        if organization_auth_state == ORGANIZATION_AUTH_MANAGER:
            update_set["organization_auth_state"] = ORGANIZATION_AUTH_MANAGER
            where_clause = sa.or_(
                where_clause,
                target.organization_auth_state != ORGANIZATION_AUTH_MANAGER,
            )

        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["organization_id", "user_id"],
            set_=update_set,
            where=where_clause,
        )
        bind.execute(upsert_stmt)


def _validate_backfill(bind: Any) -> None:
    _fail_if_rows(
        bind,
        "missing active organization membership for team_memberships source",
        """
        SELECT tm.id, tm.grantee_organization_id, tm.user_id
        FROM team_memberships tm
        LEFT JOIN organization_memberships om
          ON om.organization_id = tm.grantee_organization_id
         AND om.user_id = tm.user_id
         AND om.membership_state = 'active'
        WHERE om.id IS NULL
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "missing active manager membership for organization.created_by",
        """
        SELECT o.id, o.created_by
        FROM organization o
        LEFT JOIN organization_memberships om
          ON om.organization_id = o.id
         AND om.user_id = o.created_by
         AND om.membership_state = 'active'
         AND om.organization_auth_state = 'manager'
        WHERE om.id IS NULL
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "missing active manager membership for organization.managed_by",
        """
        SELECT o.id, o.managed_by
        FROM organization o
        LEFT JOIN organization_memberships om
          ON om.organization_id = o.id
         AND om.user_id = o.managed_by
         AND om.membership_state = 'active'
         AND om.organization_auth_state = 'manager'
        WHERE o.managed_by IS NOT NULL
          AND om.id IS NULL
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "duplicate organization_memberships rows",
        """
        SELECT organization_id, user_id, count(*) AS duplicate_count
        FROM organization_memberships
        GROUP BY organization_id, user_id
        HAVING count(*) > 1
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "invalid organization_memberships state",
        """
        SELECT id, membership_state, organization_auth_state
        FROM organization_memberships
        WHERE membership_state NOT IN ('invited', 'active', 'suspended', 'removed')
           OR organization_auth_state NOT IN ('member', 'manager')
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "organization_memberships has null required values",
        """
        SELECT id
        FROM organization_memberships
        WHERE id IS NULL
           OR organization_id IS NULL
           OR user_id IS NULL
           OR membership_state IS NULL
           OR organization_auth_state IS NULL
           OR created_at IS NULL
           OR updated_at IS NULL
           OR options IS NULL
           OR flags IS NULL
        LIMIT 10
        """,
    )
    _fail_if_rows(
        bind,
        "organization_memberships.flags is negative",
        """
        SELECT id, flags
        FROM organization_memberships
        WHERE flags < 0
        LIMIT 10
        """,
    )


def upgrade() -> None:
    bind = op.get_bind()
    _precheck_source_data(bind)
    _upsert_memberships(
        bind,
        _iter_team_membership_batches(bind),
        ORGANIZATION_AUTH_MEMBER,
    )
    _upsert_memberships(
        bind,
        _iter_creator_batches(bind),
        ORGANIZATION_AUTH_MANAGER,
    )
    _upsert_memberships(
        bind,
        _iter_manager_batches(bind),
        ORGANIZATION_AUTH_MANAGER,
    )
    _validate_backfill(bind)


def downgrade() -> None:
    pass
