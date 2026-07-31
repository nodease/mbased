from __future__ import annotations

from apps.shared.db.models.llm import LLMUsageLog
from apps.shared.db.models.provider_usage import (
    ProviderUsageCorrectionRecord,
    ProviderUsageOperationRecord,
)
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint


def test_provider_usage_operation_uses_the_canonical_provider_attempt_key() -> None:
    table = ProviderUsageOperationRecord.__table__
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert unique_constraints["uq_provider_usage_operation_attempt"] == (
        "organization_id",
        "provider_attempt_id",
        "purpose",
    )
    assert "attempt_generation" not in table.c


def test_provider_usage_operation_keeps_safe_snapshots_without_control_row_fks() -> None:
    table = ProviderUsageOperationRecord.__table__
    foreign_key_targets = {
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in table.foreign_key_constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    column_names = set(table.c.keys())

    assert foreign_key_targets == {("organization.id",)}
    assert {
        "capability_id",
        "capability_revision",
        "capability_expires_at",
        "policy_id",
        "policy_revision",
        "provider_id",
        "model_id",
        "model_api_id",
        "credential_id",
        "container_path",
        "execution_subject_kind",
        "credential_principal_kind",
        "billing_principal_kind",
        "audit_actor_kind",
        "permission_revision",
        "relation_revision",
        "egress_revision",
        "pricing_revision",
        "input_price_per_1k",
        "output_price_per_1k",
        "admitted_input_tokens",
        "admitted_output_tokens",
    } <= column_names
    assert {
        "raw_request",
        "raw_response",
        "prompt",
        "completion",
        "headers",
        "encrypted_config",
        "api_key",
        "capability_scope",
    }.isdisjoint(column_names)


def test_provider_usage_operation_has_state_projection_and_redaction_constraints() -> None:
    check_names = {
        constraint.name
        for constraint in ProviderUsageOperationRecord.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "ck_provider_usage_operation_bounds",
        "ck_provider_usage_operation_identity_kinds",
        "ck_provider_usage_operation_query_embedding_output",
        "ck_provider_usage_operation_location",
        "ck_provider_usage_operation_identity_alignment",
        "ck_provider_usage_operation_state_payload",
        "ck_provider_usage_operation_timestamps",
        "ck_provider_usage_operation_projection",
    } <= check_names


def test_query_embedding_usage_requires_zero_output_in_schema() -> None:
    constraint = next(
        constraint
        for constraint in ProviderUsageOperationRecord.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_provider_usage_operation_query_embedding_output"
    )

    sql = str(constraint.sqltext)
    assert "output_token_cap = 0" in sql
    assert "admitted_output_tokens = 0" in sql
    assert "completion_tokens IS NULL OR completion_tokens = 0" in sql


def test_succeeded_measurement_is_bounded_by_the_sealed_admission_in_schema() -> None:
    state_constraint = next(
        constraint
        for constraint in ProviderUsageOperationRecord.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_provider_usage_operation_state_payload"
    )
    sql = str(state_constraint.sqltext)

    assert "prompt_tokens <= admitted_input_tokens" in sql
    assert "completion_tokens <= admitted_output_tokens" in sql
    assert "total_cost_microusd <= cost_cap_microusd" in sql


def test_awaiting_workflow_run_projection_requires_a_snapshotted_run_id() -> None:
    projection_constraint = next(
        constraint
        for constraint in ProviderUsageOperationRecord.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_provider_usage_operation_projection"
    )

    assert (
        "projection_status <> 'awaiting_workflow_run' "
        "OR workflow_run_id IS NOT NULL"
        in str(projection_constraint.sqltext)
    )


def test_provider_usage_reconciliation_has_global_ordered_partial_indexes() -> None:
    indexes = {
        index.name: index for index in ProviderUsageOperationRecord.__table__.indexes
    }
    expected = {
        "ix_provider_usage_operation_projection_recovery": (
            ("provider_started_at", "id"),
            "state = 'succeeded'",
            "projection_status IN ('pending', 'retryable_failure', "
            "'awaiting_workflow_run')",
        ),
        "ix_provider_usage_operation_started_recovery": (
            ("provider_started_at", "id"),
            "state = 'provider_started'",
            None,
        ),
    }

    for name, (columns, required_where, optional_where) in expected.items():
        index = indexes[name]
        assert tuple(column.name for column in index.columns) == columns
        where = str(index.dialect_options["postgresql"]["where"])
        assert required_where in where
        if optional_where is not None:
            assert optional_where in where


def test_provider_usage_budget_lookup_has_workflow_leading_partial_index() -> None:
    indexes = {
        index.name: index for index in ProviderUsageOperationRecord.__table__.indexes
    }

    index = indexes["ix_provider_usage_operation_workflow_budget_period"]

    assert tuple(column.name for column in index.columns) == (
        "workflow_id",
        "provider_started_at",
    )
    where = str(index.dialect_options["postgresql"]["where"])
    assert "provider_started_at IS NOT NULL" in where
    assert (
        "purpose IN ('main_generation', 'memory_summary', 'query_embedding')"
        in where
    )
    assert "state IN ('provider_started', 'succeeded', 'outcome_unknown')" in where


def test_provider_usage_model_cost_lookup_has_subject_leading_partial_index() -> None:
    indexes = {
        index.name: index for index in ProviderUsageOperationRecord.__table__.indexes
    }

    index = indexes["ix_provider_usage_operation_subject_cost_period"]

    assert tuple(column.name for column in index.columns) == (
        "execution_subject_id",
        "provider_started_at",
    )
    where = str(index.dialect_options["postgresql"]["where"])
    assert "execution_subject_kind = 'user'" in where
    assert "execution_subject_id IS NOT NULL" in where
    assert "provider_started_at IS NOT NULL" in where
    assert (
        "purpose IN ('main_generation', 'memory_summary', 'query_embedding')"
        in where
    )
    assert "state = 'succeeded'" in where


def test_corrections_are_append_only_children_with_revision_uniqueness() -> None:
    table = ProviderUsageCorrectionRecord.__table__
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    foreign_keys = {
        (
            element.target_fullname,
            element.ondelete,
        )
        for constraint in table.foreign_key_constraints
        for element in constraint.elements
    }

    assert unique_constraints["uq_provider_usage_correction_key"] == (
        "operation_id",
        "correction_key",
    )
    assert unique_constraints["uq_provider_usage_correction_revision"] == (
        "operation_id",
        "resulting_usage_revision",
    )
    assert foreign_keys == {("provider_usage_operations.id", "CASCADE")}


def test_legacy_projection_has_nullable_unique_operation_identity() -> None:
    table = LLMUsageLog.__table__
    operation_column = table.c.provider_usage_operation_id
    indexes = {index.name: index for index in table.indexes}

    assert operation_column.nullable is True
    assert table.c.provider_usage_revision.nullable is True
    assert indexes["uq_llm_usage_logs_provider_usage_operation"].unique is True


def test_legacy_projection_workflow_reference_does_not_block_deletion() -> None:
    workflow_foreign_keys = list(
        LLMUsageLog.__table__.c.workflow_id.foreign_keys
    )

    assert len(workflow_foreign_keys) == 1
    assert workflow_foreign_keys[0].target_fullname == "workflows.id"
    assert workflow_foreign_keys[0].ondelete == "SET NULL"
