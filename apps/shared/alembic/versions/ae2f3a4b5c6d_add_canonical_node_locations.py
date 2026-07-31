"""Add canonical workflow node locations to provider policy records.

Revision ID: ae2f3a4b5c6d
Revises: ad1e2f3a4b5c
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ae2f3a4b5c6d"
down_revision: str | Sequence[str] | None = "ad1e2f3a4b5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIGEST_DOMAIN = "nodease:canonical-node-location:v1"
_POLICY_TABLE = "llm_deployment_credential_policies"
_CAPABILITY_TABLE = "provider_execution_capabilities"
_BACKFILL_BATCH_SIZE = 500


def _frame(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded


def _root_location_digest(node_id: str) -> str:
    encoded = _frame(_DIGEST_DOMAIN) + (0).to_bytes(4, "big") + _frame(node_id)
    return hashlib.sha256(encoded).hexdigest()


def _add_location_columns(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column(
            "container_path",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        table_name,
        sa.Column("node_location_digest", sa.String(length=64), nullable=True),
    )


def _backfill_root_digests(table_name: str) -> None:
    table = sa.table(
        table_name,
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("node_id", sa.String(length=255)),
        sa.column("node_location_digest", sa.String(length=64)),
    )
    connection = op.get_bind()
    update_statement = (
        sa.update(table)
        .where(table.c.id == sa.bindparam("target_id"))
        .values(node_location_digest=sa.bindparam("location_digest"))
    )
    last_id = None
    while True:
        select_statement = (
            sa.select(table.c.id, table.c.node_id)
            .order_by(table.c.id)
            .limit(_BACKFILL_BATCH_SIZE)
        )
        if last_id is not None:
            select_statement = select_statement.where(table.c.id > last_id)
        rows = connection.execute(select_statement).mappings().all()
        if not rows:
            break
        connection.execute(
            update_statement,
            [
                {
                    "target_id": row["id"],
                    "location_digest": _root_location_digest(row["node_id"]),
                }
                for row in rows
            ],
        )
        last_id = rows[-1]["id"]
    op.alter_column(
        table_name,
        "node_location_digest",
        existing_type=sa.String(length=64),
        nullable=False,
    )


def upgrade() -> None:
    _add_location_columns(_POLICY_TABLE)
    _add_location_columns(_CAPABILITY_TABLE)
    _backfill_root_digests(_POLICY_TABLE)
    _backfill_root_digests(_CAPABILITY_TABLE)
    for table_name in (_POLICY_TABLE, _CAPABILITY_TABLE):
        op.alter_column(
            table_name,
            "container_path",
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            server_default=None,
        )

    op.drop_index(
        "uq_llm_deploy_credential_policy_active",
        table_name=_POLICY_TABLE,
    )
    op.drop_index(
        "ix_llm_deploy_credential_policy_lookup",
        table_name=_POLICY_TABLE,
    )
    op.create_index(
        "uq_llm_deploy_credential_policy_active",
        _POLICY_TABLE,
        [
            "organization_id",
            "deployment_id",
            "deployment_version",
            "node_location_digest",
        ],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_llm_deploy_credential_policy_lookup",
        _POLICY_TABLE,
        [
            "organization_id",
            "deployment_id",
            "deployment_version",
            "node_location_digest",
            "is_active",
        ],
    )
    op.create_check_constraint(
        "ck_llm_deploy_credential_policy_location",
        _POLICY_TABLE,
        "jsonb_typeof(container_path) = 'array' "
        "AND length(node_location_digest) = 64",
    )
    op.create_check_constraint(
        "ck_provider_execution_capability_location",
        _CAPABILITY_TABLE,
        "jsonb_typeof(container_path) = 'array' "
        "AND length(node_location_digest) = 64",
    )


def _assert_root_only(table_name: str) -> None:
    nested = op.get_bind().execute(
        sa.text(
            f"SELECT 1 FROM {table_name} "
            "WHERE container_path <> '[]'::jsonb LIMIT 1"
        )
    ).first()
    if nested is not None:
        raise RuntimeError(
            "canonical node location downgrade requires all nested rows to be removed"
        )


def downgrade() -> None:
    _assert_root_only(_CAPABILITY_TABLE)
    _assert_root_only(_POLICY_TABLE)

    op.drop_constraint(
        "ck_provider_execution_capability_location",
        _CAPABILITY_TABLE,
        type_="check",
    )
    op.drop_constraint(
        "ck_llm_deploy_credential_policy_location",
        _POLICY_TABLE,
        type_="check",
    )
    op.drop_index(
        "ix_llm_deploy_credential_policy_lookup",
        table_name=_POLICY_TABLE,
    )
    op.drop_index(
        "uq_llm_deploy_credential_policy_active",
        table_name=_POLICY_TABLE,
    )
    op.create_index(
        "uq_llm_deploy_credential_policy_active",
        _POLICY_TABLE,
        ["organization_id", "deployment_id", "deployment_version", "node_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_llm_deploy_credential_policy_lookup",
        _POLICY_TABLE,
        [
            "organization_id",
            "deployment_id",
            "deployment_version",
            "node_id",
            "is_active",
        ],
    )
    for table_name in (_CAPABILITY_TABLE, _POLICY_TABLE):
        op.drop_column(table_name, "node_location_digest")
        op.drop_column(table_name, "container_path")
