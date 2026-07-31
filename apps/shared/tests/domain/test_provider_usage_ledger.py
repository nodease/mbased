from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from apps.shared.domain.provider_execution_capability import (
    CapabilityPurpose,
    ProviderExecutionBinding,
    RuntimeIdentityContext,
    RuntimePrincipal,
)
from apps.shared.domain.provider_usage_ledger import (
    ProviderUsageIntentSnapshot,
    ProviderUsageLedgerError,
    ProviderUsageMeasurement,
    ProviderUsageOperation,
    ProviderUsageOperationKey,
    ProviderUsageState,
)

NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


def _snapshot() -> ProviderUsageIntentSnapshot:
    organization_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    binding = ProviderExecutionBinding(
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=3,
        node_id="llm-1",
        node_invocation_id=uuid.uuid4(),
        execution_admission_id=uuid.uuid4(),
        provider_attempt_id=uuid.uuid4(),
        purpose=CapabilityPurpose.MAIN_GENERATION,
    )
    return ProviderUsageIntentSnapshot(
        binding=binding,
        capability_id=uuid.uuid4(),
        capability_revision=2,
        policy_id=uuid.uuid4(),
        policy_revision=4,
        provider_id=uuid.uuid4(),
        model_id=uuid.uuid4(),
        model_api_id="gpt-safe",
        credential_id=uuid.uuid4(),
        identities=RuntimeIdentityContext(
            execution_subject=RuntimePrincipal.user(subject_id),
            credential_principal=RuntimePrincipal.user(uuid.uuid4()),
            billing_principal=RuntimePrincipal.organization(organization_id),
            audit_actor=RuntimePrincipal.user(subject_id),
        ),
        permission_revision="a" * 64,
        relation_revision="b" * 64,
        egress_revision="c" * 64,
        pricing_revision="d" * 64,
        input_price_per_1k="0.100000",
        output_price_per_1k="0.200000",
        input_token_cap=1_000,
        output_token_cap=100,
        cost_cap_microusd=50_000,
        admitted_input_tokens=300,
        admitted_output_tokens=50,
        expires_at=NOW + timedelta(minutes=5),
    )


def _operation() -> ProviderUsageOperation:
    return ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=_snapshot(),
        now=NOW,
    )


def _query_embedding_snapshot() -> ProviderUsageIntentSnapshot:
    snapshot = _snapshot()
    return replace(
        snapshot,
        binding=replace(
            snapshot.binding,
            purpose=CapabilityPurpose.QUERY_EMBEDDING,
        ),
        output_token_cap=0,
        admitted_output_tokens=0,
    )


def _measurement(
    *,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    cost: int | None = None,
) -> ProviderUsageMeasurement:
    if cost is None:
        cost = prompt_tokens * 100 + completion_tokens * 200
    return ProviderUsageMeasurement(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_cost_microusd=cost,
        latency_ms=42,
    )


def test_operation_key_uses_the_adr_0064_provider_attempt_identity() -> None:
    snapshot = _snapshot()

    key = ProviderUsageOperationKey.from_binding(snapshot.binding)

    assert key == ProviderUsageOperationKey(
        organization_id=snapshot.binding.organization_id,
        provider_attempt_id=snapshot.binding.provider_attempt_id,
        purpose=CapabilityPurpose.MAIN_GENERATION,
    )
    assert not hasattr(key, "attempt_generation")


def test_intent_requires_exact_typed_identity_and_billing_scope() -> None:
    snapshot = _snapshot()
    wrong_org = uuid.uuid4()
    invalid = replace(
        snapshot,
        identities=replace(
            snapshot.identities,
            billing_principal=RuntimePrincipal.organization(wrong_org),
        ),
    )

    with pytest.raises(ValueError, match="billing principal"):
        ProviderUsageOperation.intent(
            operation_id=uuid.uuid4(),
            snapshot=invalid,
            now=NOW,
        )


def test_duplicate_intent_reuses_only_an_exact_snapshot() -> None:
    operation = _operation()

    operation.require_same_intent(operation.snapshot)

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        operation.require_same_intent(
            replace(operation.snapshot, capability_revision=3)
        )
    assert exc_info.value.code == "provider_usage.intent_conflict"

    other_loop = replace(
        operation.snapshot,
        binding=replace(
            operation.snapshot.binding,
            container_path=(("loop", "another-loop"),),
        ),
    )
    with pytest.raises(ProviderUsageLedgerError) as location_exc:
        operation.require_same_intent(other_loop)
    assert location_exc.value.code == "provider_usage.intent_conflict"


def test_intent_prices_use_one_exact_database_scale_for_crash_replay() -> None:
    snapshot = _snapshot()

    assert snapshot.input_price_per_1k == "0.100000000"
    assert snapshot.output_price_per_1k == "0.200000000"

    operation = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=snapshot,
        now=NOW,
    )
    operation.require_same_intent(
        replace(
            snapshot,
            input_price_per_1k="0.100000000",
            output_price_per_1k="0.200000000",
        )
    )


@pytest.mark.parametrize("price", ["0.0000000001", "100000000000"])
def test_intent_price_rejects_values_the_ledger_column_cannot_store_exactly(
    price: str,
) -> None:
    with pytest.raises(ValueError, match="ledger price"):
        replace(_snapshot(), input_price_per_1k=price)


def test_provider_call_requires_a_durable_start_and_cannot_restart() -> None:
    operation = _operation()

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        operation.record_success(measurement=_measurement(), now=NOW)
    assert exc_info.value.code == "provider_usage.outcome_not_allowed"

    started = operation.mark_provider_started(now=NOW)
    assert started.state is ProviderUsageState.PROVIDER_STARTED
    assert started.state_version == operation.state_version + 1

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        started.mark_provider_started(now=NOW)
    assert exc_info.value.code == "provider_usage.start_not_allowed"


def test_ambiguous_outcome_blocks_replay_and_accepts_one_late_success() -> None:
    started = _operation().mark_provider_started(now=NOW)
    unknown = started.mark_outcome_unknown(
        reason_code="provider_call_failed",
        now=NOW,
    )

    assert unknown.state is ProviderUsageState.OUTCOME_UNKNOWN
    assert unknown.measurement is None
    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        unknown.mark_provider_started(now=NOW)
    assert exc_info.value.code == "provider_usage.start_not_allowed"

    succeeded = unknown.reconcile_success(
        measurement=_measurement(),
        now=NOW + timedelta(minutes=1),
    )
    assert succeeded.state is ProviderUsageState.SUCCEEDED
    assert succeeded.usage_revision == 1
    assert (
        succeeded.reconcile_success(
            measurement=_measurement(),
            now=NOW + timedelta(minutes=2),
        )
        == succeeded
    )


@pytest.mark.parametrize(
    "measurement",
    [
        _measurement(prompt_tokens=301),
        _measurement(completion_tokens=51),
        _measurement(cost=2_001),
    ],
)
def test_late_success_revalidates_sealed_admission_and_pricing(
    measurement: ProviderUsageMeasurement,
) -> None:
    unknown = (
        _operation()
        .mark_provider_started(now=NOW)
        .mark_outcome_unknown(reason_code="provider_timeout", now=NOW)
    )

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        unknown.reconcile_success(
            measurement=measurement,
            now=NOW + timedelta(minutes=1),
        )

    assert exc_info.value.code == "provider_usage.measurement_invalid"


def test_success_rejects_exact_usage_above_the_sealed_cost_cap() -> None:
    snapshot = replace(_snapshot(), cost_cap_microusd=1_999)
    started = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=snapshot,
        now=NOW,
    ).mark_provider_started(now=NOW)

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        started.record_success(measurement=_measurement(), now=NOW)

    assert exc_info.value.code == "provider_usage.measurement_invalid"


def test_query_embedding_success_requires_zero_output_usage() -> None:
    operation = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=_query_embedding_snapshot(),
        now=NOW,
    ).mark_provider_started(now=NOW)

    succeeded = operation.record_success(
        measurement=_measurement(completion_tokens=0, cost=1_000),
        now=NOW,
    )

    assert succeeded.measurement is not None
    assert succeeded.measurement.completion_tokens == 0
    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        operation.record_success(
            measurement=_measurement(completion_tokens=1, cost=1_200),
            now=NOW,
        )
    assert exc_info.value.code == "provider_usage.measurement_invalid"


def test_definitive_failure_requires_a_safe_zero_effect_reason() -> None:
    started = _operation().mark_provider_started(now=NOW)

    failed = started.record_definitive_failure(
        reason_code="provider_rejected",
        now=NOW,
    )

    assert failed.state is ProviderUsageState.FAILED_DEFINITIVE
    assert failed.measurement is None
    with pytest.raises(ValueError, match="reason code"):
        started.record_definitive_failure(
            reason_code="raw provider response: credential=secret",
            now=NOW,
        )


def test_unknown_can_reconcile_to_one_authoritative_zero_effect_failure() -> None:
    unknown = (
        _operation()
        .mark_provider_started(now=NOW)
        .mark_outcome_unknown(
            reason_code="provider_timeout",
            now=NOW + timedelta(seconds=1),
        )
    )

    failed = unknown.reconcile_definitive_failure(
        reason_code="provider_not_sent",
        now=NOW + timedelta(minutes=1),
    )

    assert failed.state is ProviderUsageState.FAILED_DEFINITIVE
    assert failed.state_version == unknown.state_version + 1
    assert failed.measurement is None
    assert (
        failed.reconcile_definitive_failure(
            reason_code="provider_not_sent",
            now=NOW + timedelta(minutes=2),
        )
        == failed
    )


def test_cost_correction_is_revisioned_without_reopening_the_attempt() -> None:
    succeeded = (
        _operation()
        .mark_provider_started(now=NOW)
        .record_success(measurement=_measurement(), now=NOW)
    )

    corrected = succeeded.apply_correction(
        measurement=_measurement(prompt_tokens=20),
        expected_usage_revision=1,
    )

    assert corrected.state is ProviderUsageState.SUCCEEDED
    assert corrected.usage_revision == 2
    assert corrected.measurement == _measurement(prompt_tokens=20)
    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        corrected.apply_correction(
            measurement=_measurement(prompt_tokens=30),
            expected_usage_revision=1,
        )
    assert exc_info.value.code == "provider_usage.correction_conflict"


def test_cost_correction_revalidates_the_immutable_pricing_snapshot() -> None:
    succeeded = (
        _operation()
        .mark_provider_started(now=NOW)
        .record_success(measurement=_measurement(), now=NOW)
    )

    with pytest.raises(ProviderUsageLedgerError) as exc_info:
        succeeded.apply_correction(
            measurement=_measurement(cost=2_001),
            expected_usage_revision=1,
        )

    assert exc_info.value.code == "provider_usage.measurement_invalid"


def test_same_value_correction_still_consumes_a_usage_revision() -> None:
    succeeded = (
        _operation()
        .mark_provider_started(now=NOW)
        .record_success(measurement=_measurement(), now=NOW)
    )

    corrected = succeeded.apply_correction(
        measurement=_measurement(),
        expected_usage_revision=1,
    )

    assert corrected is not succeeded
    assert corrected.state is ProviderUsageState.SUCCEEDED
    assert corrected.state_version == succeeded.state_version + 1
    assert corrected.usage_revision == 2
    assert corrected.measurement == succeeded.measurement


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt_tokens", -1),
        ("completion_tokens", -1),
        ("total_cost_microusd", -1),
        ("latency_ms", -1),
        ("prompt_tokens", True),
    ],
)
def test_measurement_rejects_negative_or_boolean_values(field: str, value: object) -> None:
    values: dict[str, object] = {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_cost_microusd": 1,
        "latency_ms": 1,
    }
    values[field] = value

    with pytest.raises(ValueError, match="usage values"):
        ProviderUsageMeasurement(**values)  # type: ignore[arg-type]
