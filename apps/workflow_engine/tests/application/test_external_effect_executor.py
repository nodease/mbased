import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.workflow_engine.application import external_effect as external_effect_module
from apps.workflow_engine.application.external_effect import (
    EffectAttemptSpec,
    ExternalEffectExecutor,
)
from apps.workflow_engine.adapters.providers.generic_http import (
    GenericHttpEffectAdapter,
    GenericHttpRequest,
)
from apps.workflow_engine.domain.external_effect import (
    EffectInvocationFailure,
    EffectOutcome,
    ExternalEffectContext,
    ExternalEffectError,
    ExternalEffectRetrySignal,
    PreparedEffectRequest,
    PreparedProviderCall,
    ProviderReplayCapability,
    ReplayDecision,
    ResultReuseCapability,
)
from apps.workflow_engine.tests.fakes.external_effects import (
    FakeEffectAdapter,
    InMemoryEffectAttemptRepository,
)


def _context() -> ExternalEffectContext:
    return ExternalEffectContext(
        organization_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        execution_id=uuid.uuid4(),
        node_invocation_id=uuid.uuid4(),
        node_id="fake-1",
        workflow_run_id=None,
        node_run_id=None,
    )


def _executor(repository: InMemoryEffectAttemptRepository) -> ExternalEffectExecutor:
    return ExternalEffectExecutor(
        repository=repository,
        keyring={"test-v1": b"process-local-test-secret"},
        active_key_version="test-v1",
        clock=lambda: datetime.now(timezone.utc),
    )


def test_duplicate_supported_delivery_reuses_result_without_second_effect() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(result_reuse=ResultReuseCapability.SUPPORTED)
    executor = _executor(repository)
    context = _context()

    first = executor.execute(context=context, adapter=adapter, payload={"value": 1})
    duplicate = executor.execute(context=context, adapter=adapter, payload={"value": 1})

    assert duplicate == first
    assert adapter.provider.effect_count == 1
    assert adapter.provider.call_count == 1


def test_duplicate_without_projection_stops_without_second_provider_call() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(result_reuse=ResultReuseCapability.UNAVAILABLE)
    executor = _executor(repository)
    context = _context()

    executor.execute(context=context, adapter=adapter, payload={"value": 1})

    with pytest.raises(ExternalEffectError) as captured:
        executor.execute(context=context, adapter=adapter, payload={"value": 1})

    assert captured.value.code == "external_effect.result_unavailable"
    assert captured.value.retryable is False
    assert adapter.provider.call_count == 1


def test_unknown_provider_response_loss_is_not_replayed() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(
        provider_replay=ProviderReplayCapability.UNKNOWN,
        result_reuse=ResultReuseCapability.UNAVAILABLE,
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                error_code="response_lost",
            )
        ],
    )
    executor = _executor(repository)
    context = _context()

    with pytest.raises(ExternalEffectError) as first:
        executor.execute(context=context, adapter=adapter, payload={"secret": "hidden"})
    with pytest.raises(ExternalEffectError) as duplicate:
        executor.execute(context=context, adapter=adapter, payload={"secret": "hidden"})

    assert first.value.code == "external_effect.outcome_unknown"
    assert duplicate.value.code == "external_effect.outcome_unknown"
    assert adapter.provider.call_count == 1
    assert "hidden" not in str(first.value)


def test_repository_terminal_decision_overrides_precommit_retry_decision() -> None:
    repository = InMemoryEffectAttemptRepository()
    original_finish = repository.finish

    def finish_with_expired_deadline(*args, **kwargs):
        terminal = original_finish(*args, **kwargs)
        stopped = replace(terminal, replay_decision=ReplayDecision.STOP)
        repository._by_slot[repository._slot(stopped.spec)] = stopped
        return stopped

    repository.finish = finish_with_expired_deadline
    adapter = FakeEffectAdapter(
        provider_replay=ProviderReplayCapability.SUPPORTED,
        result_reuse=ResultReuseCapability.UNAVAILABLE,
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                error_code="response_lost",
            )
        ],
    )
    executor = _executor(repository)

    with pytest.raises(ExternalEffectError) as captured:
        executor.execute(context=_context(), adapter=adapter, payload={"value": 1})

    assert captured.value.code == "external_effect.outcome_unknown"
    assert captured.value.retryable is False


def test_expired_claim_before_provider_call_uses_external_effect_retry_signal() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()

    def reject_stale_claim(*_args, **_kwargs):
        raise RuntimeError("stale effect claim")

    repository.mark_in_flight = reject_stale_claim

    with pytest.raises(ExternalEffectRetrySignal) as captured:
        _executor(repository).execute(
            context=_context(),
            adapter=adapter,
            payload={"value": 1},
        )

    assert captured.value.code == "external_effect.claim_wait"
    assert captured.value.terminal_code == "external_effect.claim_wait"
    assert adapter.provider.call_count == 0


def test_expired_task_deadline_fails_before_effect_attempt(
    monkeypatch,
) -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()
    monkeypatch.setattr(
        external_effect_module,
        "time",
        SimpleNamespace(monotonic=lambda: 10.0),
        raising=False,
    )
    executor = ExternalEffectExecutor(
        repository=repository,
        keyring={"test-v1": b"process-local-test-secret"},
        active_key_version="test-v1",
        task_deadline=lambda: 10.0,
    )

    with pytest.raises(ExternalEffectError) as captured:
        executor.execute(
            context=_context(),
            adapter=adapter,
            payload={"value": 1},
        )

    assert captured.value.code == "external_effect.deadline_exceeded"
    assert captured.value.retryable is False
    assert repository._by_slot == {}
    assert adapter.provider.call_count == 0


def test_deadline_expiring_after_claim_terminalizes_before_provider_call(
    monkeypatch,
) -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()
    monotonic = {"value": 9.0}
    original_finalize = adapter.finalize_provider_call

    def finalize_then_expire(prepared, idempotency_key):
        call = original_finalize(prepared, idempotency_key)
        monotonic["value"] = 10.0
        return call

    adapter.finalize_provider_call = finalize_then_expire
    monkeypatch.setattr(
        external_effect_module,
        "time",
        SimpleNamespace(monotonic=lambda: monotonic["value"]),
        raising=False,
    )
    executor = ExternalEffectExecutor(
        repository=repository,
        keyring={"test-v1": b"process-local-test-secret"},
        active_key_version="test-v1",
        task_deadline=lambda: 10.0,
    )

    with pytest.raises(ExternalEffectError) as captured:
        executor.execute(
            context=_context(),
            adapter=adapter,
            payload={"value": 1},
        )

    record = next(iter(repository._by_slot.values()))
    assert captured.value.code == "external_effect.deadline_exceeded"
    assert record.outcome is EffectOutcome.FAILED_BEFORE_EFFECT
    assert record.replay_decision is ReplayDecision.STOP
    assert record.error_code == "deadline_exceeded"
    assert record.provider_started_at is None
    assert adapter.provider.call_count == 0


def test_expired_task_deadline_blocks_read_only_provider_slot(
    monkeypatch,
) -> None:
    repository = InMemoryEffectAttemptRepository()
    monkeypatch.setattr(
        external_effect_module,
        "time",
        SimpleNamespace(monotonic=lambda: 10.0),
        raising=False,
    )
    executor = ExternalEffectExecutor(
        repository=repository,
        task_deadline=lambda: 10.0,
    )

    with pytest.raises(ExternalEffectError) as captured:
        executor.guard_read_only_slot(context=_context())

    assert captured.value.code == "external_effect.deadline_exceeded"
    assert captured.value.retryable is False


def test_repository_lookup_failure_uses_claim_wait_without_provider_call() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()

    def fail_lookup(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    repository.find_by_slot = fail_lookup

    with pytest.raises(ExternalEffectRetrySignal) as captured:
        _executor(repository).execute(
            context=_context(),
            adapter=adapter,
            payload={"value": 1},
        )

    assert captured.value.code == "external_effect.claim_wait"
    assert captured.value.terminal_code == "external_effect.claim_wait"
    assert adapter.provider.call_count == 0


def test_exhausted_retry_budget_closes_live_claim_wait() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()
    context = _context()
    prepared = adapter.prepare_effect({"value": 1})
    spec = EffectAttemptSpec(
        context=context,
        profile=adapter.profile,
        effect_sequence=0,
        effect_input_digest=prepared.effect_input_digest,
        replay_deadline_at=None,
        key_version="test-v1",
        key_format_version=adapter.profile.key_format,
        idempotency_key_fingerprint="0" * 64,
    )
    repository.acquire(
        spec,
        claim_owner="other-worker",
        claim_ttl=timedelta(seconds=30),
        allow_retry=True,
        now=datetime.now(timezone.utc),
    )
    executor = ExternalEffectExecutor(
        repository=repository,
        keyring={"test-v1": b"process-local-test-secret"},
        active_key_version="test-v1",
        retry_available=lambda: False,
    )

    with pytest.raises(ExternalEffectError) as captured:
        executor.execute(context=context, adapter=adapter, payload={"value": 1})

    assert captured.value.code == "external_effect.claim_wait"
    assert captured.value.retryable is False
    assert adapter.provider.call_count == 0


def test_terminal_commit_failure_after_provider_call_never_returns_output() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()

    def fail_terminal_commit(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    repository.finish = fail_terminal_commit

    with pytest.raises(ExternalEffectRetrySignal) as captured:
        _executor(repository).execute(
            context=_context(),
            adapter=adapter,
            payload={"value": 1},
        )

    assert captured.value.code == "external_effect.claim_wait"
    assert captured.value.terminal_code == "external_effect.outcome_unknown"
    assert adapter.provider.call_count == 1


def test_unknown_outcome_retry_preserves_terminal_public_code() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(
        provider_replay=ProviderReplayCapability.SUPPORTED,
        result_reuse=ResultReuseCapability.UNAVAILABLE,
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                error_code="response_lost",
            )
        ],
    )

    with pytest.raises(ExternalEffectRetrySignal) as retry_signal:
        _executor(repository).execute(
            context=_context(),
            adapter=adapter,
            payload={"value": 1},
        )

    assert retry_signal.value.code == "external_effect.retry_allowed"
    assert retry_signal.value.terminal_code == "external_effect.outcome_unknown"


def test_same_identity_with_different_effect_input_is_rejected() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(result_reuse=ResultReuseCapability.SUPPORTED)
    executor = _executor(repository)
    context = _context()

    executor.execute(context=context, adapter=adapter, payload={"value": 1})

    with pytest.raises(ExternalEffectError) as captured:
        executor.execute(context=context, adapter=adapter, payload={"value": 2})

    assert captured.value.code == "external_effect.identity_conflict"
    assert adapter.provider.call_count == 1


def test_same_slot_with_different_operation_is_identity_conflict() -> None:
    repository = InMemoryEffectAttemptRepository()
    first_adapter = FakeEffectAdapter(result_reuse=ResultReuseCapability.SUPPORTED)
    second_adapter = FakeEffectAdapter(result_reuse=ResultReuseCapability.SUPPORTED)
    second_adapter._profile = replace(  # noqa: SLF001 - explicit contract drift fixture
        second_adapter.profile,
        operation="fake.different_effect",
    )
    executor = _executor(repository)
    context = _context()

    executor.execute(context=context, adapter=first_adapter, payload={"value": 1})

    with pytest.raises(ExternalEffectError) as captured:
        executor.execute(context=context, adapter=second_adapter, payload={"value": 1})

    assert captured.value.code == "external_effect.identity_conflict"
    assert second_adapter.provider.call_count == 0


def test_unknown_capability_never_receives_system_key() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(
        provider_replay=ProviderReplayCapability.UNKNOWN,
        result_reuse=ResultReuseCapability.UNAVAILABLE,
    )
    executor = _executor(repository)

    executor.execute(context=_context(), adapter=adapter, payload={"value": 1})

    assert adapter.finalized_keys == [None]


def test_retry_uses_frozen_key_version_after_active_key_rotation() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="connection_failed",
            )
        ]
    )
    context = _context()
    first = ExternalEffectExecutor(
        repository=repository,
        keyring={"v1": b"first-key"},
        active_key_version="v1",
        clock=lambda: datetime.now(timezone.utc),
    )

    with pytest.raises(ExternalEffectRetrySignal) as retry_signal:
        first.execute(context=context, adapter=adapter, payload={"value": 1})

    assert retry_signal.value.code == "external_effect.retry_allowed"
    assert retry_signal.value.terminal_code == "external_effect.connection_failed"

    rotated = ExternalEffectExecutor(
        repository=repository,
        keyring={"v1": b"first-key", "v2": b"second-key"},
        active_key_version="v2",
        clock=lambda: datetime.now(timezone.utc),
    )
    rotated.execute(context=context, adapter=adapter, payload={"value": 1})

    assert adapter.finalized_keys[0] == adapter.finalized_keys[1]


def test_retry_claim_uses_current_run_correlation_ids() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="connection_failed",
            )
        ]
    )
    executor = _executor(repository)
    first_context = replace(
        _context(),
        workflow_run_id=uuid.uuid4(),
        node_run_id=uuid.uuid4(),
    )

    with pytest.raises(ExternalEffectRetrySignal):
        executor.execute(
            context=first_context,
            adapter=adapter,
            payload={"value": 1},
        )

    retry_context = replace(
        first_context,
        workflow_run_id=uuid.uuid4(),
        node_run_id=uuid.uuid4(),
    )
    executor.execute(
        context=retry_context,
        adapter=adapter,
        payload={"value": 1},
    )

    attempt = next(iter(repository._by_slot.values()))
    assert attempt.spec.context.workflow_run_id == retry_context.workflow_run_id
    assert attempt.spec.context.node_run_id == retry_context.node_run_id


def test_retry_uses_frozen_result_projection_after_active_profile_change() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="connection_failed",
            )
        ]
    )
    executor = _executor(repository)
    context = _context()

    with pytest.raises(ExternalEffectRetrySignal):
        executor.execute(context=context, adapter=adapter, payload={"value": 1})

    adapter._profile = replace(  # noqa: SLF001 - active-contract drift fixture
        adapter.profile,
        contract_version="fake.create_effect.v2",
        result_reuse=ResultReuseCapability.UNAVAILABLE,
        replay_projection_semantics=None,
    )
    retried = executor.execute(
        context=context,
        adapter=adapter,
        payload={"value": 1},
    )
    duplicate = executor.execute(
        context=context,
        adapter=adapter,
        payload={"value": 1},
    )

    assert duplicate == retried
    assert adapter.provider.effect_count == 1


def test_failed_before_effect_stop_preserves_allowlisted_error_for_duplicates() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="provider_rejected_request",
                retry_before_effect=False,
            )
        ]
    )
    executor = _executor(repository)
    context = _context()

    with pytest.raises(ExternalEffectError) as first:
        executor.execute(context=context, adapter=adapter, payload={"value": 1})
    with pytest.raises(ExternalEffectError) as duplicate:
        executor.execute(context=context, adapter=adapter, payload={"value": 1})

    assert first.value.code == "external_effect.provider_rejected_request"
    assert duplicate.value.code == first.value.code
    assert first.value.retryable is False
    assert adapter.provider.call_count == 1


def test_unrecognized_provider_error_is_replaced_with_safe_fallback() -> None:
    repository = InMemoryEffectAttemptRepository()
    marker = "credential-secret-from-provider"
    adapter = FakeEffectAdapter(
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code=marker,
                retry_before_effect=False,
            )
        ]
    )

    with pytest.raises(ExternalEffectError) as captured:
        _executor(repository).execute(
            context=_context(),
            adapter=adapter,
            payload={"value": 1},
        )

    assert captured.value.code == "external_effect.provider_call_failed"
    assert marker not in str(captured.value)
    assert next(iter(repository._by_slot.values())).error_code == "provider_call_failed"


def test_malformed_typed_failure_is_closed_without_generic_retry() -> None:
    repository = InMemoryEffectAttemptRepository()
    failure = EffectInvocationFailure(
        outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
        error_code="connection_failed",
        provider_status_code=503,
    )
    failure.outcome = "invalid-outcome"
    failure.error_code = {"provider-secret": "hidden"}
    failure.provider_status_code = "503"
    adapter = FakeEffectAdapter(failures=[failure])
    context = _context()

    with pytest.raises(ExternalEffectError) as captured:
        _executor(repository).execute(
            context=context,
            adapter=adapter,
            payload={"value": 1},
        )

    attempt = next(iter(repository._by_slot.values()))
    assert captured.value.code == "external_effect.outcome_unknown"
    assert captured.value.retryable is False
    assert attempt.outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN
    assert attempt.replay_decision is ReplayDecision.STOP
    assert attempt.error_code == "provider_call_failed"
    assert attempt.provider_status_code is None
    assert adapter.provider.call_count == 1


def test_untyped_adapter_failure_is_unknown_outcome_without_replay() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()
    marker = "provider-secret-in-adapter-bug"

    def fail_unexpectedly(_call):
        raise RuntimeError(marker)

    adapter.invoke_effect = fail_unexpectedly

    with pytest.raises(ExternalEffectError) as captured:
        _executor(repository).execute(
            context=_context(),
            adapter=adapter,
            payload={"value": 1},
        )

    record = next(iter(repository._by_slot.values()))
    assert captured.value.code == "external_effect.outcome_unknown"
    assert captured.value.retryable is False
    assert record.outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN
    assert record.replay_decision is not None
    assert record.replay_decision.value == "stop"
    assert record.error_code == "provider_call_failed"
    assert marker not in str(captured.value)


def test_invalid_prepared_digest_fails_before_attempt_creation() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()
    adapter.prepare_effect = lambda payload, profile: PreparedEffectRequest(
        request=payload,
        effect_input_digest="not-a-sha256",
        profile=profile,
    )

    with pytest.raises(ExternalEffectError) as captured:
        _executor(repository).execute(
            context=_context(),
            adapter=adapter,
            payload={"value": 1},
        )

    assert captured.value.code == "external_effect.prepare_failed"
    assert repository._by_slot == {}
    assert adapter.provider.call_count == 0


def test_finalized_call_cannot_switch_away_from_frozen_profile() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()
    wrong_profile = replace(
        adapter.profile,
        contract_version="fake.create_effect.v2",
    )
    original_finalize = adapter.finalize_provider_call

    def finalize_with_wrong_profile(prepared, idempotency_key):
        call = original_finalize(prepared, idempotency_key)
        return PreparedProviderCall(
            request=call.request,
            idempotency_key=call.idempotency_key,
            profile=wrong_profile,
        )

    adapter.finalize_provider_call = finalize_with_wrong_profile

    with pytest.raises(ExternalEffectError) as captured:
        _executor(repository).execute(
            context=_context(),
            adapter=adapter,
            payload={"value": 1},
        )

    attempt = next(iter(repository._by_slot.values()))
    assert captured.value.code == "external_effect.prepare_failed"
    assert attempt.outcome is EffectOutcome.FAILED_BEFORE_EFFECT
    assert attempt.provider_started_at is None
    assert adapter.provider.call_count == 0


def test_malformed_provider_result_is_unknown_without_generic_retry() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()
    adapter.invoke_effect = lambda _call: {"effect_id": "not-typed"}
    context = _context()

    with pytest.raises(ExternalEffectError) as captured:
        _executor(repository).execute(
            context=context,
            adapter=adapter,
            payload={"value": 1},
        )

    attempt = next(iter(repository._by_slot.values()))
    assert captured.value.code == "external_effect.outcome_unknown"
    assert attempt.outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN
    assert attempt.replay_decision is ReplayDecision.STOP


def test_projection_bug_closes_success_as_result_unavailable() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()
    context = _context()

    def fail_projection(_output, *, profile):
        raise RuntimeError("provider-secret-in-projection")

    adapter.replay_projection = fail_projection
    first = _executor(repository).execute(
        context=context,
        adapter=adapter,
        payload={"value": 1},
    )

    with pytest.raises(ExternalEffectError) as duplicate:
        _executor(repository).execute(
            context=context,
            adapter=adapter,
            payload={"value": 1},
        )

    attempt = next(iter(repository._by_slot.values()))
    assert set(first) == {"effect_id"}
    assert duplicate.value.code == "external_effect.result_unavailable"
    assert attempt.outcome is EffectOutcome.SUCCEEDED
    assert attempt.replay_decision is ReplayDecision.RESULT_UNAVAILABLE
    assert adapter.provider.call_count == 1


def test_exhausted_retry_budget_stops_before_effect_failure() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="connection_failed",
            )
        ]
    )
    executor = ExternalEffectExecutor(
        repository=repository,
        keyring={"test-v1": b"process-local-test-secret"},
        active_key_version="test-v1",
        retry_available=lambda: False,
    )

    with pytest.raises(ExternalEffectError) as captured:
        executor.execute(context=_context(), adapter=adapter, payload={"value": 1})

    record = next(iter(repository._by_slot.values()))
    assert captured.value.code == "external_effect.connection_failed"
    assert record.replay_decision.value == "stop"
    assert record.claim_generation == 1
    assert adapter.provider.call_count == 1


def test_exhausted_retry_budget_stops_supported_unknown_outcome() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                error_code="response_lost",
            )
        ]
    )
    executor = ExternalEffectExecutor(
        repository=repository,
        keyring={"test-v1": b"process-local-test-secret"},
        active_key_version="test-v1",
        retry_available=lambda: False,
    )

    with pytest.raises(ExternalEffectError) as captured:
        executor.execute(context=_context(), adapter=adapter, payload={"value": 1})

    record = next(iter(repository._by_slot.values()))
    assert captured.value.code == "external_effect.outcome_unknown"
    assert captured.value.retryable is False
    assert record.replay_decision is not None
    assert record.replay_decision.value == "stop"
    assert adapter.provider.call_count == 1


def test_expired_in_flight_cleanup_applies_exhausted_budget_atomically() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter()
    context = _context()
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prepared = adapter.prepare_effect({"value": 1})
    spec = EffectAttemptSpec(
        context=context,
        profile=adapter.profile,
        effect_sequence=0,
        effect_input_digest=prepared.effect_input_digest,
        replay_deadline_at=None,
        key_version="test-v1",
        key_format_version=adapter.profile.key_format,
        idempotency_key_fingerprint="0" * 64,
    )
    acquired = repository.acquire(
        spec,
        claim_owner="first-owner",
        claim_ttl=timedelta(seconds=1),
        allow_retry=True,
        now=started_at,
    )
    repository.mark_in_flight(acquired.record, now=started_at)
    executor = ExternalEffectExecutor(
        repository=repository,
        keyring={"test-v1": b"process-local-test-secret"},
        active_key_version="test-v1",
        clock=lambda: started_at + timedelta(seconds=2),
        retry_available=lambda: False,
    )

    with pytest.raises(ExternalEffectError) as captured:
        executor.execute(context=context, adapter=adapter, payload={"value": 1})

    record = next(iter(repository._by_slot.values()))
    assert captured.value.code == "external_effect.outcome_unknown"
    assert record.replay_decision is not None
    assert record.replay_decision.value == "stop"
    assert record.claim_generation == 1
    assert adapter.provider.call_count == 0


def test_exhausted_retry_budget_does_not_reopen_terminal_attempt() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = FakeEffectAdapter(
        failures=[
            EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="connection_failed",
            )
        ]
    )
    context = _context()

    with pytest.raises(ExternalEffectRetrySignal):
        _executor(repository).execute(
            context=context,
            adapter=adapter,
            payload={"value": 1},
        )

    exhausted = ExternalEffectExecutor(
        repository=repository,
        keyring={"test-v1": b"process-local-test-secret"},
        active_key_version="test-v1",
        retry_available=lambda: False,
    )
    with pytest.raises(ExternalEffectError) as captured:
        exhausted.execute(context=context, adapter=adapter, payload={"value": 1})

    record = next(iter(repository._by_slot.values()))
    assert captured.value.code == "external_effect.connection_failed"
    assert record.replay_decision.value == "stop"
    assert record.claim_generation == 1
    assert adapter.provider.call_count == 1


def test_deterministic_invalid_http_input_is_terminal_without_provider_call() -> None:
    repository = InMemoryEffectAttemptRepository()
    adapter = GenericHttpEffectAdapter()
    executor = _executor(repository)
    context = _context()
    request = GenericHttpRequest(
        method="POST",
        url="not-a-url",
        headers={},
        body="{invalid-json",
        timeout_seconds=1.0,
    )

    with pytest.raises(ExternalEffectError) as first:
        executor.execute(context=context, adapter=adapter, payload=request)
    with pytest.raises(ExternalEffectError) as duplicate:
        executor.execute(context=context, adapter=adapter, payload=request)

    record = next(iter(repository._by_slot.values()))
    assert first.value.code == "external_effect.invalid_prepared_request"
    assert duplicate.value.code == first.value.code
    assert record.outcome is EffectOutcome.FAILED_BEFORE_EFFECT
    assert record.provider_started_at is None
    assert record.error_code == "invalid_prepared_request"
