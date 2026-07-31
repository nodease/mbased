from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.workflow_engine.application.external_effect import (
    AcquireKind,
    AcquireResult,
    EffectAttemptRecord,
    EffectAttemptSpec,
)
from apps.workflow_engine.domain.external_effect import (
    EffectAttemptStatus,
    EffectInvocationFailure,
    EffectOutcome,
    PreparedEffectRequest,
    PreparedProviderCall,
    ProviderContractProfile,
    ProviderInvocationResult,
    ProviderReplayCapability,
    ReplayDecision,
    ResultReuseCapability,
    decide_replay,
    provider_contract_registry,
)


class InMemoryEffectAttemptRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_slot: dict[
            tuple[uuid.UUID, uuid.UUID, uuid.UUID, int], EffectAttemptRecord
        ] = {}

    @staticmethod
    def _slot(spec: EffectAttemptSpec) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, int]:
        return (
            spec.context.organization_id,
            spec.context.execution_id,
            spec.context.node_invocation_id,
            spec.effect_sequence,
        )

    @staticmethod
    def _same_semantics(left: EffectAttemptSpec, right: EffectAttemptSpec) -> bool:
        return (
            left.context.app_id == right.context.app_id
            and left.context.workflow_id == right.context.workflow_id
            and left.profile.provider == right.profile.provider
            and left.profile.operation == right.profile.operation
            and left.profile.contract_version == right.profile.contract_version
            and left.profile.provider_replay == right.profile.provider_replay
            and left.profile.result_reuse == right.profile.result_reuse
            and left.effect_input_digest == right.effect_input_digest
        )

    def acquire(
        self,
        spec: EffectAttemptSpec,
        *,
        claim_owner: str,
        claim_ttl: timedelta,
        allow_retry: bool,
        now: datetime,
    ) -> AcquireResult:
        slot = self._slot(spec)
        with self._lock:
            current = self._by_slot.get(slot)
            if current is None:
                frozen_spec = spec
                if spec.profile.provider_replay is ProviderReplayCapability.SUPPORTED:
                    frozen_spec = replace(
                        spec,
                        replay_deadline_at=now + spec.profile.retention,
                    )
                current = EffectAttemptRecord(
                    id=uuid.uuid4(),
                    spec=frozen_spec,
                    status=EffectAttemptStatus.PREPARED.value,
                    claim_owner=claim_owner,
                    claim_generation=1,
                    claim_expires_at=now + claim_ttl,
                )
                self._by_slot[slot] = current
                return AcquireResult(AcquireKind.CLAIMED, copy.deepcopy(current))
            if not self._same_semantics(current.spec, spec):
                return AcquireResult(
                    AcquireKind.IDENTITY_CONFLICT, copy.deepcopy(current)
                )
            if current.status == EffectAttemptStatus.TERMINAL.value:
                if current.replay_decision in {
                    ReplayDecision.RETRY_BEFORE_EFFECT,
                    ReplayDecision.REPLAY_SAME_KEY,
                }:
                    if not allow_retry:
                        current = replace(
                            current,
                            replay_decision=ReplayDecision.STOP,
                        )
                        self._by_slot[slot] = current
                        return AcquireResult(
                            AcquireKind.TERMINAL,
                            copy.deepcopy(current),
                        )
                    if (
                        current.replay_decision is ReplayDecision.REPLAY_SAME_KEY
                        and current.spec.replay_deadline_at is not None
                        and now >= current.spec.replay_deadline_at
                    ):
                        current = replace(current, replay_decision=ReplayDecision.STOP)
                        self._by_slot[slot] = current
                        return AcquireResult(
                            AcquireKind.TERMINAL, copy.deepcopy(current)
                        )
                    current = replace(
                        current,
                        spec=replace(
                            current.spec,
                            context=replace(
                                current.spec.context,
                                workflow_run_id=spec.context.workflow_run_id,
                                node_run_id=spec.context.node_run_id,
                            ),
                        ),
                        status=EffectAttemptStatus.PREPARED.value,
                        claim_owner=claim_owner,
                        claim_generation=current.claim_generation + 1,
                        claim_expires_at=now + claim_ttl,
                        outcome=None,
                        replay_decision=None,
                        replay_result=None,
                        provider_started_at=None,
                        provider_status_code=None,
                        error_code=None,
                        terminal_at=None,
                    )
                    self._by_slot[slot] = current
                    return AcquireResult(AcquireKind.CLAIMED, copy.deepcopy(current))
                return AcquireResult(AcquireKind.TERMINAL, copy.deepcopy(current))
            if current.claim_expires_at is not None and current.claim_expires_at <= now:
                if current.status == EffectAttemptStatus.IN_FLIGHT.value:
                    decision = decide_replay(
                        outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                        provider_replay=current.spec.profile.provider_replay,
                        result_reuse=current.spec.profile.result_reuse,
                        has_replay_result=False,
                        now=now,
                        replay_deadline_at=current.spec.replay_deadline_at,
                    )
                    if decision is ReplayDecision.REPLAY_SAME_KEY and not allow_retry:
                        decision = ReplayDecision.STOP
                    current = replace(
                        current,
                        status=EffectAttemptStatus.TERMINAL.value,
                        claim_owner=None,
                        claim_expires_at=None,
                        outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                        replay_decision=decision,
                        error_code="response_lost",
                        terminal_at=now,
                    )
                    self._by_slot[slot] = current
                    return AcquireResult(AcquireKind.TERMINAL, copy.deepcopy(current))
                current = replace(
                    current,
                    spec=replace(
                        current.spec,
                        context=replace(
                            current.spec.context,
                            workflow_run_id=spec.context.workflow_run_id,
                            node_run_id=spec.context.node_run_id,
                        ),
                    ),
                    claim_owner=claim_owner,
                    claim_generation=current.claim_generation + 1,
                    claim_expires_at=now + claim_ttl,
                )
                self._by_slot[slot] = current
                return AcquireResult(AcquireKind.CLAIMED, copy.deepcopy(current))
            return AcquireResult(AcquireKind.WAIT, copy.deepcopy(current))

    def find_by_slot(self, *, context, effect_sequence: int):
        slot = (
            context.organization_id,
            context.execution_id,
            context.node_invocation_id,
            effect_sequence,
        )
        with self._lock:
            current = self._by_slot.get(slot)
            return copy.deepcopy(current) if current is not None else None

    def mark_in_flight(
        self,
        record: EffectAttemptRecord,
        *,
        now: datetime,
    ) -> EffectAttemptRecord:
        slot = self._slot(record.spec)
        with self._lock:
            current = self._by_slot[slot]
            if (
                current.status != EffectAttemptStatus.PREPARED.value
                or current.claim_owner != record.claim_owner
                or current.claim_generation != record.claim_generation
                or current.claim_expires_at is None
                or current.claim_expires_at <= now
            ):
                raise RuntimeError("stale effect claim")
            current = replace(
                current,
                status=EffectAttemptStatus.IN_FLIGHT.value,
                provider_started_at=now,
            )
            self._by_slot[slot] = current
            return copy.deepcopy(current)

    def finish(
        self,
        record: EffectAttemptRecord,
        *,
        outcome: EffectOutcome,
        replay_decision: ReplayDecision,
        replay_result: Any,
        provider_status_code: int | None,
        error_code: str | None,
        now: datetime,
    ) -> EffectAttemptRecord:
        slot = self._slot(record.spec)
        with self._lock:
            current = self._by_slot[slot]
            if (
                current.claim_owner != record.claim_owner
                or current.claim_generation != record.claim_generation
                or current.claim_expires_at is None
                or current.claim_expires_at <= now
            ):
                raise RuntimeError("stale effect claim")
            effective_decision = replay_decision
            if (
                outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN
                and replay_decision is ReplayDecision.REPLAY_SAME_KEY
            ):
                effective_decision = decide_replay(
                    outcome=outcome,
                    provider_replay=current.spec.profile.provider_replay,
                    result_reuse=current.spec.profile.result_reuse,
                    has_replay_result=False,
                    now=now,
                    replay_deadline_at=current.spec.replay_deadline_at,
                )
            current = replace(
                current,
                status=EffectAttemptStatus.TERMINAL.value,
                claim_owner=None,
                claim_expires_at=None,
                outcome=outcome,
                replay_decision=effective_decision,
                replay_result=copy.deepcopy(replay_result),
                provider_status_code=provider_status_code,
                error_code=error_code,
                terminal_at=now,
            )
            self._by_slot[slot] = current
            return copy.deepcopy(current)

    def wait_for_resolution(
        self,
        record: EffectAttemptRecord,
        *,
        deadline: float | None,
    ) -> AcquireResult:
        current = self._by_slot[self._slot(record.spec)]
        kind = (
            AcquireKind.TERMINAL
            if current.status == EffectAttemptStatus.TERMINAL.value
            else AcquireKind.WAIT
        )
        return AcquireResult(kind, copy.deepcopy(current))

    def stop_retry(
        self,
        record: EffectAttemptRecord,
        *,
        now: datetime,
    ) -> EffectAttemptRecord:
        del now
        slot = self._slot(record.spec)
        with self._lock:
            current = self._by_slot[slot]
            if (
                current.status == EffectAttemptStatus.TERMINAL.value
                and current.claim_generation == record.claim_generation
                and current.replay_decision
                in {
                    ReplayDecision.RETRY_BEFORE_EFFECT,
                    ReplayDecision.REPLAY_SAME_KEY,
                }
            ):
                current = replace(current, replay_decision=ReplayDecision.STOP)
                self._by_slot[slot] = current
            return copy.deepcopy(current)

    def guard_read_only_slot(self, *, context, effect_sequence: int) -> None:
        slot = (
            context.organization_id,
            context.execution_id,
            context.node_invocation_id,
            effect_sequence,
        )
        if slot in self._by_slot:
            from apps.workflow_engine.domain.external_effect import ExternalEffectError

            raise ExternalEffectError(
                "external_effect.identity_conflict",
                retryable=False,
                node_id=context.node_id,
            )


class FakeProvider:
    def __init__(
        self,
        *,
        require_key: bool = True,
        clock=None,
        retention: timedelta = timedelta(hours=24),
    ) -> None:
        self.call_count = 0
        self.effect_count = 0
        self.require_key = require_key
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.retention = retention
        self._effects: dict[str, tuple[str, dict[str, Any], datetime]] = {}

    def invoke(self, call: PreparedProviderCall) -> ProviderInvocationResult:
        self.call_count += 1
        request = call.request
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        key = call.idempotency_key
        if self.require_key and (
            not isinstance(key, str)
            or len(key) != 43
            or re.fullmatch(r"[A-Za-z0-9_-]+", key) is None
        ):
            raise EffectInvocationFailure(
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                error_code="invalid_prepared_request",
                retry_before_effect=False,
            )
        key = key or str(uuid.uuid4())
        now = self.clock()
        current = self._effects.get(key)
        if current is not None:
            previous_digest, output, accepted_at = current
            if now < accepted_at + self.retention:
                if previous_digest != digest:
                    raise EffectInvocationFailure(
                        outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                        error_code="provider_key_request_conflict",
                        provider_status_code=409,
                        retry_before_effect=False,
                    )
                return ProviderInvocationResult(
                    copy.deepcopy(output),
                    provider_status_code=200,
                )
        self.effect_count += 1
        output = {"effect_id": str(uuid.uuid4())}
        self._effects[key] = (digest, copy.deepcopy(output), now)
        return ProviderInvocationResult(output, provider_status_code=201)


class FakeEffectAdapter:
    def __init__(
        self,
        *,
        provider_replay: ProviderReplayCapability = ProviderReplayCapability.SUPPORTED,
        result_reuse: ResultReuseCapability = ResultReuseCapability.SUPPORTED,
        failures: list[EffectInvocationFailure] | None = None,
        clock=None,
    ) -> None:
        base = provider_contract_registry(include_test_profiles=True).get(
            "fake", "fake.create_effect", "fake.create_effect.v1"
        )
        key_transport = base.key_transport
        key_field = base.key_field
        key_format = base.key_format
        key_max_length = base.key_max_length
        retention = base.retention
        if provider_replay is not ProviderReplayCapability.SUPPORTED:
            key_transport = "unknown"
            key_field = None
            key_format = None
            key_max_length = None
            retention = None
        self._profile = replace(
            base,
            provider_replay=provider_replay,
            result_reuse=result_reuse,
            key_transport=key_transport,
            key_field=key_field,
            key_format=key_format,
            key_max_length=key_max_length,
            retention=retention,
            replay_projection_semantics=(
                base.replay_projection_semantics
                if result_reuse is ResultReuseCapability.SUPPORTED
                else None
            ),
        )
        self.provider = FakeProvider(
            require_key=provider_replay is ProviderReplayCapability.SUPPORTED,
            clock=clock,
        )
        self.failures = list(failures or [])
        self.finalized_keys: list[str | None] = []

    @property
    def profile(self) -> ProviderContractProfile:
        return self._profile

    def prepare_effect(
        self,
        payload: Any,
        *,
        profile: ProviderContractProfile | None = None,
    ) -> PreparedEffectRequest:
        profile = profile or self.profile
        if (
            profile.provider != self.profile.provider
            or profile.operation != self.profile.operation
        ):
            raise ValueError("unsupported fake provider profile")
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return PreparedEffectRequest(
            request=copy.deepcopy(payload),
            effect_input_digest=hashlib.sha256(canonical).hexdigest(),
            profile=profile,
        )

    def finalize_provider_call(
        self,
        prepared: PreparedEffectRequest,
        idempotency_key: str | None,
    ) -> PreparedProviderCall:
        profile = prepared.profile or self.profile
        if profile.provider_replay is ProviderReplayCapability.SUPPORTED:
            if (
                not isinstance(idempotency_key, str)
                or len(idempotency_key) != 43
                or re.fullmatch(r"[A-Za-z0-9_-]+", idempotency_key) is None
            ):
                raise ValueError("invalid fake provider idempotency key")
        elif idempotency_key is not None:
            raise ValueError("fake provider system key is forbidden")
        self.finalized_keys.append(idempotency_key)
        return PreparedProviderCall(
            request=copy.deepcopy(prepared.request),
            idempotency_key=idempotency_key,
            profile=profile,
        )

    def invoke_effect(self, call: PreparedProviderCall) -> ProviderInvocationResult:
        if self.failures:
            failure = self.failures.pop(0)
            self.provider.call_count += 1
            if failure.outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN:
                self.provider.effect_count += 1
            raise failure
        return self.provider.invoke(call)

    def replay_projection(
        self,
        output: Any,
        *,
        profile: ProviderContractProfile,
    ) -> Any:
        if profile.result_reuse is ResultReuseCapability.UNAVAILABLE:
            return None
        if not isinstance(output, dict) or set(output) != {"effect_id"}:
            raise ValueError("invalid fake replay output")
        return {"effect_id": str(uuid.UUID(str(output["effect_id"])))}
