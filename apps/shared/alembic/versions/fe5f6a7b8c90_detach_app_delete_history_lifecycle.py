"""Detach retained history from App/Workflow lifecycle foreign keys.

Revision ID: fe5f6a7b8c90
Revises: b9e5f4a3c2d3
Create Date: 2026-07-14 00:00:00.000000
"""

from collections.abc import Sequence
from typing import NamedTuple

import sqlalchemy as sa
from alembic import op


revision: str = "fe5f6a7b8c90"
down_revision: str | Sequence[str] | None = "b9e5f4a3c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class _HistoryForeignKey(NamedTuple):
    table_name: str
    local_columns: tuple[str, ...]
    referred_table: str
    remote_columns: tuple[str, ...]
    legacy_ondelete: str | None
    restore_name: str


# Retained rows already contain the immutable resource IDs required for
# provenance. The upgrade keeps every value and only removes lifecycle FKs.
_HISTORY_FOREIGN_KEYS = (
    _HistoryForeignKey(
        "workflow_runs",
        ("workflow_id",),
        "workflows",
        ("id",),
        "CASCADE",
        "fk_mba87_runs_workflow",
    ),
    _HistoryForeignKey(
        "workflow_runs",
        ("app_id",),
        "apps",
        ("id",),
        "SET NULL",
        "fk_mba87_runs_app",
    ),
    _HistoryForeignKey(
        "workflow_runs",
        ("deployment_id",),
        "workflow_deployments",
        ("id",),
        "SET NULL",
        "fk_mba87_runs_deployment",
    ),
    _HistoryForeignKey(
        "llm_usage_logs",
        ("workflow_id",),
        "workflows",
        ("id",),
        None,
        "fk_mba87_usage_workflow",
    ),
    _HistoryForeignKey(
        "cost_optimizer_experiments",
        ("workflow_id",),
        "workflows",
        ("id",),
        "CASCADE",
        "fk_mba87_cost_experiment_workflow",
    ),
    _HistoryForeignKey(
        "cost_optimizer_experiments",
        ("app_id",),
        "apps",
        ("id",),
        "SET NULL",
        "fk_mba87_cost_experiment_app",
    ),
    _HistoryForeignKey(
        "cost_optimizer_recommendation_verifications",
        ("workflow_id",),
        "workflows",
        ("id",),
        "CASCADE",
        "fk_mba87_cost_verification_workflow",
    ),
    _HistoryForeignKey(
        "mail_message_processings",
        ("workflow_id",),
        "workflows",
        ("id",),
        "CASCADE",
        "fk_mba87_mail_processing_workflow",
    ),
    _HistoryForeignKey(
        "mail_message_processings",
        ("deployment_id",),
        "workflow_deployments",
        ("id",),
        "SET NULL",
        "fk_mba87_mail_processing_deployment",
    ),
    _HistoryForeignKey(
        "llm_node_model_routing_policy_updates",
        ("policy_id",),
        "llm_node_model_routing_policies",
        ("id",),
        "CASCADE",
        "fk_mba87_routing_update_policy",
    ),
    _HistoryForeignKey(
        "llm_node_model_routing_policy_run_events",
        ("policy_id",),
        "llm_node_model_routing_policies",
        ("id",),
        "CASCADE",
        "fk_mba87_routing_run_event_policy",
    ),
    _HistoryForeignKey(
        "llm_node_model_routing_cohorts",
        ("policy_id",),
        "llm_node_model_routing_policies",
        ("id",),
        "CASCADE",
        "fk_mba87_routing_cohort_policy",
    ),
    _HistoryForeignKey(
        "llm_node_model_routing_observations",
        ("policy_id",),
        "llm_node_model_routing_policies",
        ("id",),
        "CASCADE",
        "fk_mba87_routing_observation_policy",
    ),
    _HistoryForeignKey(
        "llm_node_model_routing_validation_batches",
        ("policy_id",),
        "llm_node_model_routing_policies",
        ("id",),
        "CASCADE",
        "fk_mba87_routing_validation_batch_policy",
    ),
    _HistoryForeignKey(
        "llm_node_model_routing_validation_budget_months",
        ("policy_id",),
        "llm_node_model_routing_policies",
        ("id",),
        "CASCADE",
        "fk_mba87_routing_budget_policy",
    ),
)


def _matching_foreign_key(
    table_name: str,
    local_columns: tuple[str, ...],
    referred_table: str,
) -> dict | None:
    inspector = sa.inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys(table_name):
        if (
            tuple(foreign_key["constrained_columns"]) == local_columns
            and foreign_key["referred_table"] == referred_table
        ):
            return foreign_key
    return None


def _drop_foreign_key_if_present(
    table_name: str,
    local_columns: tuple[str, ...],
    referred_table: str,
) -> None:
    foreign_key = _matching_foreign_key(table_name, local_columns, referred_table)
    if foreign_key is not None:
        op.drop_constraint(foreign_key["name"], table_name, type_="foreignkey")


def _replace_active_foreign_key(
    *,
    name: str,
    table_name: str,
    local_columns: tuple[str, ...],
    referred_table: str,
    remote_columns: tuple[str, ...],
    ondelete: str | None,
) -> None:
    _drop_foreign_key_if_present(table_name, local_columns, referred_table)
    op.create_foreign_key(
        name,
        table_name,
        referred_table,
        list(local_columns),
        list(remote_columns),
        ondelete=ondelete,
    )


def _history_has_orphaned_reference(spec: _HistoryForeignKey) -> bool:
    predicates = " AND ".join(
        f'source."{local}" = target."{remote}"'
        for local, remote in zip(
            spec.local_columns,
            spec.remote_columns,
            strict=True,
        )
    )
    non_null = " AND ".join(
        f'source."{column}" IS NOT NULL' for column in spec.local_columns
    )
    target_missing = f'target."{spec.remote_columns[0]}" IS NULL'
    result = op.get_bind().execute(
        sa.text(
            f'SELECT 1 FROM "{spec.table_name}" AS source '
            f'LEFT JOIN "{spec.referred_table}" AS target ON {predicates} '
            f'WHERE {non_null} AND {target_missing} LIMIT 1'
        )
    )
    return result.first() is not None


def _restore_history_foreign_key(spec: _HistoryForeignKey) -> None:
    op.create_foreign_key(
        spec.restore_name,
        spec.table_name,
        spec.referred_table,
        list(spec.local_columns),
        list(spec.remote_columns),
        ondelete=spec.legacy_ondelete,
    )


def _assert_history_downgrade_is_safe() -> None:
    orphaned_references: list[str] = []
    for spec in _HISTORY_FOREIGN_KEYS:
        if _history_has_orphaned_reference(spec):
            orphaned_references.append(
                f"{spec.table_name}.{','.join(spec.local_columns)}"
                f"->{spec.referred_table}"
            )
    if orphaned_references:
        joined = ", ".join(orphaned_references)
        raise RuntimeError(
            "MBA-87 downgrade is blocked because retained history references "
            f"deleted lifecycle rows: {joined}"
        )


def upgrade() -> None:
    for spec in _HISTORY_FOREIGN_KEYS:
        _drop_foreign_key_if_present(
            spec.table_name,
            spec.local_columns,
            spec.referred_table,
        )

    # Permission rows are active configuration. The application deletion use
    # case removes them explicitly for audit; CASCADE is the DB safety net for
    # the legacy delete path until that use case is wired.
    _replace_active_foreign_key(
        name="fk_mba87_team_workflow_permission_workflow",
        table_name="team_workflow_permissions",
        local_columns=("workflow_id",),
        referred_table="workflows",
        remote_columns=("id",),
        ondelete="CASCADE",
    )
    _replace_active_foreign_key(
        name="fk_user_workflow_permissions_workflow_org",
        table_name="user_workflow_permissions",
        local_columns=("workflow_id", "grantee_organization_id"),
        referred_table="workflows",
        remote_columns=("id", "organization_id"),
        ondelete="CASCADE",
    )


def downgrade() -> None:
    _assert_history_downgrade_is_safe()

    _replace_active_foreign_key(
        name="fk_mba87_team_workflow_permission_workflow_legacy",
        table_name="team_workflow_permissions",
        local_columns=("workflow_id",),
        referred_table="workflows",
        remote_columns=("id",),
        ondelete=None,
    )
    _replace_active_foreign_key(
        name="fk_user_workflow_permissions_workflow_org",
        table_name="user_workflow_permissions",
        local_columns=("workflow_id", "grantee_organization_id"),
        referred_table="workflows",
        remote_columns=("id", "organization_id"),
        ondelete=None,
    )

    for spec in _HISTORY_FOREIGN_KEYS:
        _restore_history_foreign_key(spec)
