"""Add query-embedding policy, capability, and usage purpose.

Revision ID: b17c8d9e0f12
Revises: b16c7d8e9f01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b17c8d9e0f12"
down_revision: str | Sequence[str] | None = "b16c7d8e9f01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QUERY_EMBEDDING_DOWNGRADE_GUARD = (
    "query_embedding policy, capability, or usage rows must be removed "
    "before downgrade"
)

_POLICY_CHECK = (
    "deployment_version >= 1 AND policy_revision >= 1 "
    "AND purpose IN ('main_generation', 'query_embedding')"
)
_LEGACY_POLICY_CHECK = "deployment_version >= 1 AND policy_revision >= 1"

_CAPABILITY_CHECK = (
    "purpose IN ('main_generation', 'memory_summary', 'query_embedding') "
    "AND state IN ('active', 'revoked') "
    "AND execution_subject_kind IN ('user', 'anonymous_public', 'system') "
    "AND billing_principal_kind = 'organization' "
    "AND audit_actor_kind IN ('user', 'public', 'system') "
    "AND length(permission_revision) = 64 "
    "AND length(relation_revision) = 64 "
    "AND length(egress_revision) = 64 "
    "AND length(pricing_revision) = 64"
)
_LEGACY_CAPABILITY_CHECK = _CAPABILITY_CHECK.replace(
    ", 'query_embedding'",
    "",
)

_USAGE_IDENTITY_CHECK = (
    "purpose IN ('main_generation', 'memory_summary', 'query_embedding') "
    "AND state IN ('intent', 'provider_started', 'succeeded', "
    "'failed_definitive', 'outcome_unknown') "
    "AND projection_status IN ('pending', 'projected', "
    "'awaiting_workflow_run', 'retryable_failure', 'terminal_failure') "
    "AND execution_subject_kind IN ('user', 'anonymous_public', 'system') "
    "AND credential_principal_kind = 'user' "
    "AND billing_principal_kind = 'organization' "
    "AND audit_actor_kind IN ('user', 'public', 'system') "
    "AND length(permission_revision) = 64 "
    "AND length(relation_revision) = 64 "
    "AND length(egress_revision) = 64 "
    "AND length(pricing_revision) = 64"
)
_LEGACY_USAGE_IDENTITY_CHECK = _USAGE_IDENTITY_CHECK.replace(
    ", 'query_embedding'",
    "",
)

_QUERY_EMBEDDING_TABLES = (
    "llm_deployment_credential_policies",
    "provider_execution_capabilities",
    "provider_usage_operations",
)


def _lock_query_embedding_tables(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE "
            + ", ".join(_QUERY_EMBEDDING_TABLES)
            + " IN ACCESS EXCLUSIVE MODE"
        )
    )


def _create_policy_indexes(*, include_query: bool) -> None:
    op.create_index(
        "uq_llm_deploy_credential_policy_active",
        "llm_deployment_credential_policies",
        [
            "organization_id",
            "deployment_id",
            "deployment_version",
            "node_location_digest",
        ],
        unique=True,
        postgresql_where=sa.text(
            "is_active AND purpose = 'main_generation'"
            if include_query
            else "is_active"
        ),
    )
    if include_query:
        op.create_index(
            "uq_llm_deploy_credential_policy_active_query_embedding",
            "llm_deployment_credential_policies",
            [
                "organization_id",
                "deployment_id",
                "deployment_version",
                "node_location_digest",
                "model_id",
            ],
            unique=True,
            postgresql_where=sa.text(
                "is_active AND purpose = 'query_embedding'"
            ),
        )
    lookup_columns = [
        "organization_id",
        "deployment_id",
        "deployment_version",
        "node_location_digest",
    ]
    if include_query:
        lookup_columns.extend(["purpose", "model_id"])
    lookup_columns.append("is_active")
    op.create_index(
        "ix_llm_deploy_credential_policy_lookup",
        "llm_deployment_credential_policies",
        lookup_columns,
    )


def _create_usage_cost_indexes(*, include_query: bool) -> None:
    purposes = (
        "('main_generation', 'memory_summary', 'query_embedding')"
        if include_query
        else "('main_generation', 'memory_summary')"
    )
    op.create_index(
        "ix_provider_usage_operation_workflow_budget_period",
        "provider_usage_operations",
        ["workflow_id", "provider_started_at"],
        postgresql_where=sa.text(
            "provider_started_at IS NOT NULL "
            f"AND purpose IN {purposes} "
            "AND state IN ('provider_started', 'succeeded', 'outcome_unknown')"
        ),
    )
    op.create_index(
        "ix_provider_usage_operation_subject_cost_period",
        "provider_usage_operations",
        ["execution_subject_id", "provider_started_at"],
        postgresql_where=sa.text(
            "execution_subject_kind = 'user' "
            "AND execution_subject_id IS NOT NULL "
            "AND provider_started_at IS NOT NULL "
            f"AND purpose IN {purposes} "
            "AND state = 'succeeded'"
        ),
    )


def upgrade() -> None:
    op.add_column(
        "llm_deployment_credential_policies",
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'main_generation'"),
        ),
    )
    op.drop_constraint(
        "ck_llm_deploy_credential_policy_revision",
        "llm_deployment_credential_policies",
        type_="check",
    )
    op.create_check_constraint(
        "ck_llm_deploy_credential_policy_revision",
        "llm_deployment_credential_policies",
        _POLICY_CHECK,
    )
    op.drop_index(
        "uq_llm_deploy_credential_policy_active",
        table_name="llm_deployment_credential_policies",
    )
    op.drop_index(
        "ix_llm_deploy_credential_policy_lookup",
        table_name="llm_deployment_credential_policies",
    )
    _create_policy_indexes(include_query=True)

    op.drop_constraint(
        "ck_provider_execution_capability_state",
        "provider_execution_capabilities",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_execution_capability_state",
        "provider_execution_capabilities",
        _CAPABILITY_CHECK,
    )
    op.create_check_constraint(
        "ck_provider_execution_capability_query_embedding_output",
        "provider_execution_capabilities",
        "purpose <> 'query_embedding' OR output_token_cap = 0",
    )

    op.drop_constraint(
        "ck_provider_usage_operation_identity_kinds",
        "provider_usage_operations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_usage_operation_identity_kinds",
        "provider_usage_operations",
        _USAGE_IDENTITY_CHECK,
    )
    op.create_check_constraint(
        "ck_provider_usage_operation_query_embedding_output",
        "provider_usage_operations",
        "purpose <> 'query_embedding' OR (output_token_cap = 0 "
        "AND admitted_output_tokens = 0 "
        "AND (completion_tokens IS NULL OR completion_tokens = 0))",
    )
    op.drop_index(
        "ix_provider_usage_operation_workflow_budget_period",
        table_name="provider_usage_operations",
    )
    op.drop_index(
        "ix_provider_usage_operation_subject_cost_period",
        table_name="provider_usage_operations",
    )
    _create_usage_cost_indexes(include_query=True)


def downgrade() -> None:
    bind = op.get_bind()
    _lock_query_embedding_tables(bind)
    has_query_rows = bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM llm_deployment_credential_policies "
            "WHERE purpose = 'query_embedding' "
            "UNION ALL SELECT 1 FROM provider_execution_capabilities "
            "WHERE purpose = 'query_embedding' "
            "UNION ALL SELECT 1 FROM provider_usage_operations "
            "WHERE purpose = 'query_embedding'"
            ")"
        )
    ).scalar()
    if has_query_rows:
        raise RuntimeError(QUERY_EMBEDDING_DOWNGRADE_GUARD)

    op.drop_index(
        "ix_provider_usage_operation_workflow_budget_period",
        table_name="provider_usage_operations",
    )
    op.drop_index(
        "ix_provider_usage_operation_subject_cost_period",
        table_name="provider_usage_operations",
    )
    _create_usage_cost_indexes(include_query=False)
    op.drop_constraint(
        "ck_provider_usage_operation_query_embedding_output",
        "provider_usage_operations",
        type_="check",
    )
    op.drop_constraint(
        "ck_provider_usage_operation_identity_kinds",
        "provider_usage_operations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_usage_operation_identity_kinds",
        "provider_usage_operations",
        _LEGACY_USAGE_IDENTITY_CHECK,
    )

    op.drop_constraint(
        "ck_provider_execution_capability_query_embedding_output",
        "provider_execution_capabilities",
        type_="check",
    )
    op.drop_constraint(
        "ck_provider_execution_capability_state",
        "provider_execution_capabilities",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_execution_capability_state",
        "provider_execution_capabilities",
        _LEGACY_CAPABILITY_CHECK,
    )

    op.drop_index(
        "uq_llm_deploy_credential_policy_active_query_embedding",
        table_name="llm_deployment_credential_policies",
    )
    op.drop_index(
        "uq_llm_deploy_credential_policy_active",
        table_name="llm_deployment_credential_policies",
    )
    op.drop_index(
        "ix_llm_deploy_credential_policy_lookup",
        table_name="llm_deployment_credential_policies",
    )
    _create_policy_indexes(include_query=False)
    op.drop_constraint(
        "ck_llm_deploy_credential_policy_revision",
        "llm_deployment_credential_policies",
        type_="check",
    )
    op.create_check_constraint(
        "ck_llm_deploy_credential_policy_revision",
        "llm_deployment_credential_policies",
        _LEGACY_POLICY_CHECK,
    )
    op.drop_column("llm_deployment_credential_policies", "purpose")
