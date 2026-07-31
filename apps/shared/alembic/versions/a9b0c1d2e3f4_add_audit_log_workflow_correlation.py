"""Add typed workflow correlation to audit logs.

Revision ID: a9b0c1d2e3f4
Revises: a8b9c0d1e2f3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "a8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID_PATTERN = (
    "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _backfill_existing_correlation(
    *,
    column_name: str,
    referenced_table: str,
    require_matching_run: bool = False,
) -> None:
    matching_run_clause = (
        "AND (audit.workflow_run_id IS NULL "
        "OR referenced.workflow_run_id = audit.workflow_run_id)"
        if require_matching_run
        else ""
    )
    workflow_run_join = (
        "JOIN workflow_runs AS correlation_run "
        "ON correlation_run.id = referenced.workflow_run_id"
        if require_matching_run
        else ""
    )
    workflow_id_expression = (
        "correlation_run.workflow_id"
        if require_matching_run
        else "referenced.workflow_id"
    )
    op.execute(
        sa.text(
            f"""
            WITH candidates AS (
                SELECT
                    id,
                    CASE
                        WHEN audit_metadata ->> :metadata_key ~* :uuid_pattern
                        THEN (audit_metadata ->> :metadata_key)::uuid
                        ELSE NULL
                    END AS correlation_id,
                    CASE
                        WHEN audit_metadata ->> 'organization_id' ~* :uuid_pattern
                        THEN (audit_metadata ->> 'organization_id')::uuid
                        ELSE NULL
                    END AS organization_id
                FROM audit_logs
            )
            UPDATE audit_logs AS audit
            SET {column_name} = candidates.correlation_id
            FROM candidates
            WHERE audit.id = candidates.id
              AND candidates.correlation_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM {referenced_table} AS referenced
                  {workflow_run_join}
                  JOIN workflows AS workflow
                    ON workflow.id = {workflow_id_expression}
                  WHERE referenced.id = candidates.correlation_id
                    AND candidates.organization_id IS NOT NULL
                    AND workflow.organization_id = candidates.organization_id
                  {matching_run_clause}
              )
            """
        ).bindparams(
            metadata_key=column_name,
            uuid_pattern=_UUID_PATTERN,
        )
    )


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "audit_logs",
        sa.Column(
            "workflow_node_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    _backfill_existing_correlation(
        column_name="workflow_run_id",
        referenced_table="workflow_runs",
    )
    _backfill_existing_correlation(
        column_name="workflow_node_run_id",
        referenced_table="workflow_node_runs",
        require_matching_run=True,
    )
    op.create_foreign_key(
        "fk_audit_logs_workflow_run_id",
        "audit_logs",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_audit_logs_workflow_node_run_id",
        "audit_logs",
        "workflow_node_runs",
        ["workflow_node_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_audit_logs_workflow_run_id",
        "audit_logs",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_workflow_node_run_id",
        "audit_logs",
        ["workflow_node_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_logs_workflow_node_run_id",
        table_name="audit_logs",
    )
    op.drop_index(
        "ix_audit_logs_workflow_run_id",
        table_name="audit_logs",
    )
    op.drop_constraint(
        "fk_audit_logs_workflow_node_run_id",
        "audit_logs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_audit_logs_workflow_run_id",
        "audit_logs",
        type_="foreignkey",
    )
    op.drop_column("audit_logs", "workflow_node_run_id")
    op.drop_column("audit_logs", "workflow_run_id")
