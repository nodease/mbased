from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from apps.shared.services.provider_usage_cost_read_model import (
    ProviderUsageAggregate,
    provider_usage_aggregate_subquery,
    provider_usage_model_subject_rows_subquery,
    provider_usage_subject_aggregate_subquery,
    summarize_usage_by_execution_subject,
    summarize_usage_records,
)
from sqlalchemy.dialects import postgresql

START = datetime(2026, 7, 1, tzinfo=timezone.utc)
END = datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_mixed_legacy_and_canonical_usage_excludes_compatibility_projection():
    organization_id = uuid4()
    workflow_id = uuid4()
    operation_id = uuid4()

    result = summarize_usage_records(
        workflow_id=workflow_id,
        organization_id=organization_id,
        start_at=START,
        end_at=END,
        legacy_usage_logs=[
            _legacy_usage(
                workflow_id=workflow_id,
                organization_id=None,
                prompt_tokens=2,
                completion_tokens=3,
                total_cost=Decimal("0.25"),
            ),
            _legacy_usage(
                workflow_id=workflow_id,
                organization_id=organization_id,
                prompt_tokens=11,
                completion_tokens=13,
                total_cost=Decimal("9.99"),
                provider_usage_operation_id=operation_id,
            ),
        ],
        provider_operations=[
            _provider_operation(
                workflow_id=workflow_id,
                organization_id=organization_id,
                state="succeeded",
                prompt_tokens=5,
                completion_tokens=7,
                total_cost_microusd=1_500_000,
            )
        ],
    )

    assert result == ProviderUsageAggregate(
        prompt_tokens=7,
        completion_tokens=10,
        call_count=2,
        total_cost=Decimal("1.75"),
        agent_builder_cost=Decimal("0"),
        unresolved_provider_call_count=0,
    )
    assert result.usage_data_complete is True
    assert result.workflow_execution_cost == Decimal("1.75")


def test_unresolved_attempt_is_not_zero_cost_and_uses_provider_start_period():
    organization_id = uuid4()
    workflow_id = uuid4()

    result = summarize_usage_records(
        workflow_id=workflow_id,
        organization_id=organization_id,
        start_at=START,
        end_at=END,
        legacy_usage_logs=[],
        provider_operations=[
            _provider_operation(
                workflow_id=workflow_id,
                organization_id=organization_id,
                state="provider_started",
                provider_started_at=START,
            ),
            _provider_operation(
                workflow_id=workflow_id,
                organization_id=organization_id,
                state="outcome_unknown",
                provider_started_at=END,
            ),
        ],
    )

    assert result.total_cost == Decimal("0")
    assert result.unresolved_provider_call_count == 1
    assert result.usage_data_complete is False


def test_organization_and_billable_purpose_scope_are_fail_closed():
    organization_id = uuid4()
    workflow_id = uuid4()

    result = summarize_usage_records(
        workflow_id=workflow_id,
        organization_id=organization_id,
        start_at=START,
        end_at=END,
        legacy_usage_logs=[],
        provider_operations=[
            _provider_operation(
                workflow_id=workflow_id,
                organization_id=uuid4(),
                state="succeeded",
                prompt_tokens=100,
                completion_tokens=100,
                total_cost_microusd=50_000_000,
            ),
            _provider_operation(
                workflow_id=workflow_id,
                organization_id=organization_id,
                purpose="future_non_billable",
                state="outcome_unknown",
            ),
        ],
    )

    assert result == ProviderUsageAggregate()


def test_query_embedding_success_is_included_in_billable_usage():
    organization_id = uuid4()
    workflow_id = uuid4()

    result = summarize_usage_records(
        workflow_id=workflow_id,
        organization_id=organization_id,
        start_at=START,
        end_at=END,
        legacy_usage_logs=[],
        provider_operations=[
            _provider_operation(
                workflow_id=workflow_id,
                organization_id=organization_id,
                purpose="query_embedding",
                state="succeeded",
                prompt_tokens=7,
                completion_tokens=0,
                total_cost_microusd=250_000,
            )
        ],
    )

    assert result == ProviderUsageAggregate(
        prompt_tokens=7,
        completion_tokens=0,
        call_count=1,
        total_cost=Decimal("0.25"),
        agent_builder_cost=Decimal("0"),
        unresolved_provider_call_count=0,
    )


def test_sql_aggregate_uses_canonical_source_and_legacy_only_projection_filter():
    aggregate = provider_usage_aggregate_subquery(
        organization_id=uuid4(),
        start_at=START,
        end_at=END,
    )

    sql = str(
        aggregate.select().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "llm_usage_logs.provider_usage_operation_id IS NULL" in sql
    assert "provider_usage_operations.provider_started_at" in sql
    assert "provider_usage_operations.state = 'succeeded'" in sql
    assert "provider_started" in sql
    assert "outcome_unknown" in sql


def test_member_cost_uses_execution_subject_not_credential_principal():
    organization_id = uuid4()
    workflow_id = uuid4()
    execution_subject_id = uuid4()
    credential_principal_id = uuid4()
    operation = _provider_operation(
        workflow_id=workflow_id,
        organization_id=organization_id,
        state="succeeded",
        prompt_tokens=5,
        completion_tokens=7,
        total_cost_microusd=1_500_000,
    )
    operation.execution_subject_kind = "user"
    operation.execution_subject_id = execution_subject_id
    operation.credential_principal_id = credential_principal_id

    result = summarize_usage_by_execution_subject(
        organization_id=organization_id,
        eligible_workflow_ids={workflow_id},
        user_ids={execution_subject_id, credential_principal_id},
        start_at=START,
        end_at=END,
        legacy_usage_logs=[],
        provider_operations=[operation],
    )

    assert result[execution_subject_id].total_cost == Decimal("1.5")
    assert result[credential_principal_id] == ProviderUsageAggregate()


def test_member_sql_aggregate_uses_explicit_execution_subject():
    aggregate = provider_usage_subject_aggregate_subquery(
        organization_id=uuid4(),
        start_at=START,
        end_at=END,
    )

    sql = str(
        aggregate.select().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "llm_usage_logs.provider_usage_operation_id IS NULL" in sql
    assert "provider_usage_operations.execution_subject_kind = 'user'" in sql
    assert "provider_usage_operations.execution_subject_id" in sql


def test_user_model_rows_use_execution_subject_and_exclude_projection():
    rows = provider_usage_model_subject_rows_subquery(
        user_id=uuid4(),
        start_at=START,
        end_at=END,
    )

    sql = str(
        rows.select().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "llm_usage_logs.provider_usage_operation_id IS NULL" in sql
    assert "provider_usage_operations.execution_subject_kind = 'user'" in sql
    assert "provider_usage_operations.execution_subject_id" in sql
    assert "provider_usage_operations.state = 'succeeded'" in sql


def _legacy_usage(
    *,
    workflow_id,
    organization_id,
    prompt_tokens,
    completion_tokens,
    total_cost,
    provider_usage_operation_id=None,
):
    return SimpleNamespace(
        workflow_id=workflow_id,
        organization_id=organization_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_cost=total_cost,
        created_at=START,
        runtime_surface=None,
        status="success",
        provider_usage_operation_id=provider_usage_operation_id,
    )


def _provider_operation(
    *,
    workflow_id,
    organization_id,
    state,
    purpose="main_generation",
    provider_started_at=START,
    prompt_tokens=None,
    completion_tokens=None,
    total_cost_microusd=None,
):
    return SimpleNamespace(
        workflow_id=workflow_id,
        organization_id=organization_id,
        purpose=purpose,
        state=state,
        provider_started_at=provider_started_at,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_cost_microusd=total_cost_microusd,
    )
