from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Mapping, NoReturn, Protocol

from apps.workflow_engine.domain.external_effect import (
    EffectInvocationFailure,
    EffectOutcome,
    ExternalEffectContext,
    ExternalEffectError,
    ExternalEffectRetrySignal,
    PreparedEffectRequest,
    PreparedProviderCall,
    ProviderContractProfile,
    ProviderInvocationResult,
    ProviderReplayCapability,
    ReplayDecision,
    ResultReuseCapability,
    build_provider_idempotency_key,
    canonical_replay_result,
    decide_replay,
    safe_effect_error_code,
)


DEFAULT_CLAIM_TTL = timedelta(seconds=630)


@dataclass(frozen=True)
class EffectAttemptSpec:
    context: ExternalEffectContext
    profile: ProviderContractProfile
    effect_sequence: int
    effect_input_digest: str
    replay_deadline_at: datetime | None
    key_version: str | None
    key_format_version: str | None
    idempotency_key_fingerprint: str | None


@dataclass(frozen=True)
class EffectAttemptRecord:
    id: uuid.UUID
    spec: EffectAttemptSpec
    status: str
    claim_owner: str | None
    claim_generation: int
    claim_expires_at: datetime | None
    outcome: EffectOutcome | None = None
    replay_decision: ReplayDecision | None = None
    replay_result: Any = None
    provider_started_at: datetime | None = None
    provider_status_code: int | None = None
    error_code: str | None = None
    terminal_at: datetime | None = None


class AcquireKind(str, Enum):
    CLAIMED = "claimed"
    TERMINAL = "terminal"
    WAIT = "wait"
    IDENTITY_CONFLICT = "identity_conflict"


@dataclass(frozen=True)
class AcquireResult:
    kind: AcquireKind
    record: EffectAttemptRecord


class EffectAttemptRepository(Protocol):
    def find_by_slot(
        self,
        *,
        context: ExternalEffectContext,
        effect_sequence: int,
    ) -> EffectAttemptRecord | None: ...

    def acquire(
        self,
        spec: EffectAttemptSpec,
        *,
        claim_owner: str,
        claim_ttl: timedelta,
        allow_retry: bool,
        now: datetime,
    ) -> AcquireResult: ...

    def mark_in_flight(
        self,
        record: EffectAttemptRecord,
        *,
        now: datetime,
    ) -> EffectAttemptRecord: ...

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
    ) -> EffectAttemptRecord: ...

    def wait_for_resolution(
        self,
        record: EffectAttemptRecord,
        *,
        deadline: float | None,
    ) -> AcquireResult: ...

    def stop_retry(
        self,
        record: EffectAttemptRecord,
        *,
        now: datetime,
    ) -> EffectAttemptRecord: ...


class EffectAdapter(Protocol):
    @property
    def profile(self) -> ProviderContractProfile: ...

    def prepare_effect(
        self,
        payload: Any,
        *,
        profile: ProviderContractProfile,
    ) -> PreparedEffectRequest: ...

    def finalize_provider_call(
        self,
        prepared: PreparedEffectRequest,
        idempotency_key: str | None,
    ) -> PreparedProviderCall: ...

    def invoke_effect(self, call: PreparedProviderCall) -> ProviderInvocationResult: ...

    def replay_projection(
        self,
        output: Any,
        *,
        profile: ProviderContractProfile,
    ) -> Any: ...


class ExternalEffectExecutor:
    def __init__(
        self,
        *,
        repository: EffectAttemptRepository,
        keyring: Mapping[str, bytes] | None = None,
        active_key_version: str | None = None,
        claim_ttl: timedelta = DEFAULT_CLAIM_TTL,
        clock: Callable[[], datetime] | None = None,
        task_deadline: Callable[[], float | None] | None = None,
        retry_available: Callable[[], bool] | None = None,
    ) -> None:
        self.repository = repository
        self.keyring = dict(keyring or {})
        self.active_key_version = active_key_version
        self.claim_ttl = claim_ttl
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.task_deadline = task_deadline or (lambda: None)
        self.retry_available = retry_available or (lambda: True)

    def execute(
        self,
        *,
        context: ExternalEffectContext,
        adapter: EffectAdapter,
        payload: Any,
        effect_sequence: int = 0,
    ) -> Any:
        allow_retry = self.retry_available()
        try:
            existing = self.repository.find_by_slot(
                context=context,
                effect_sequence=effect_sequence,
            )
        except Exception:
            self._raise_repository_retry(
                allow_retry=allow_retry,
                node_id=context.node_id,
                terminal_code="external_effect.claim_wait",
            )
        if existing is not None and (
            existing.spec.profile.provider != adapter.profile.provider
            or existing.spec.profile.operation != adapter.profile.operation
        ):
            raise ExternalEffectError(
                "external_effect.identity_conflict",
                retryable=False,
                node_id=context.node_id,
            )
        profile = existing.spec.profile if existing is not None else adapter.profile
        try:
            prepared = adapter.prepare_effect(payload, profile=profile)
            self._validate_prepared_request(prepared, profile=profile)
        except ExternalEffectError:
            raise
        except Exception:
            raise ExternalEffectError(
                "external_effect.prepare_failed",
                retryable=False,
                node_id=context.node_id,
            ) from None

        now = self.clock()
        key_version = None
        key_format_version = None
        key_fingerprint = None
        replay_deadline_at = None
        if profile.provider_replay is ProviderReplayCapability.SUPPORTED:
            if existing is not None:
                key_version = existing.spec.key_version
                key_format_version = existing.spec.key_format_version
                key_fingerprint = existing.spec.idempotency_key_fingerprint
                replay_deadline_at = existing.spec.replay_deadline_at
            else:
                key_version = self.active_key_version
                if not key_version or key_version not in self.keyring:
                    raise ExternalEffectError(
                        "external_effect.prepare_failed",
                        retryable=False,
                        node_id=context.node_id,
                    )
                key_format_version = profile.key_format
                candidate_key = self._provider_key(
                    context,
                    profile,
                    effect_sequence=effect_sequence,
                    key_version=key_version,
                )
                if (
                    profile.key_max_length
                    and len(candidate_key) > profile.key_max_length
                ):
                    raise ExternalEffectError(
                        "external_effect.prepare_failed",
                        retryable=False,
                        node_id=context.node_id,
                    )
                key_fingerprint = hashlib.sha256(
                    candidate_key.encode("ascii")
                ).hexdigest()
                # The repository freezes this from its database clock.
                replay_deadline_at = None

        spec = EffectAttemptSpec(
            context=context,
            profile=profile,
            effect_sequence=effect_sequence,
            effect_input_digest=prepared.effect_input_digest,
            replay_deadline_at=replay_deadline_at,
            key_version=key_version,
            key_format_version=key_format_version,
            idempotency_key_fingerprint=key_fingerprint,
        )
        try:
            acquired = self.repository.acquire(
                spec,
                claim_owner=str(uuid.uuid4()),
                claim_ttl=self.claim_ttl,
                allow_retry=allow_retry,
                now=now,
            )
        except ExternalEffectError:
            raise
        except Exception:
            self._raise_repository_retry(
                allow_retry=allow_retry,
                node_id=context.node_id,
                terminal_code="external_effect.claim_wait",
            )
        if acquired.kind is AcquireKind.IDENTITY_CONFLICT:
            raise ExternalEffectError(
                "external_effect.identity_conflict",
                retryable=False,
                node_id=context.node_id,
            )
        if acquired.kind is AcquireKind.WAIT:
            try:
                acquired = self.repository.wait_for_resolution(
                    acquired.record,
                    deadline=self.task_deadline(),
                )
            except Exception:
                self._raise_repository_retry(
                    allow_retry=allow_retry,
                    node_id=context.node_id,
                    terminal_code="external_effect.claim_wait",
                )
        if acquired.kind is AcquireKind.IDENTITY_CONFLICT:
            raise ExternalEffectError(
                "external_effect.identity_conflict",
                retryable=False,
                node_id=context.node_id,
            )
        if acquired.kind is AcquireKind.TERMINAL:
            terminal = self._stop_retry_if_exhausted(
                acquired.record,
                allow_retry=allow_retry,
            )
            self._attach_terminal_trace(adapter, terminal)
            return self._terminal_result(terminal)
        if acquired.kind is not AcquireKind.CLAIMED:
            if not allow_retry:
                raise ExternalEffectError(
                    "external_effect.claim_wait",
                    retryable=False,
                    node_id=context.node_id,
                )
            raise ExternalEffectRetrySignal(
                "external_effect.claim_wait",
                node_id=context.node_id,
            )

        record = acquired.record
        if prepared.error_code is not None:
            safe_error_code = safe_effect_error_code(
                prepared.error_code,
                fallback="invalid_prepared_request",
            )
            terminal = self._finish_attempt(
                record,
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                replay_decision=ReplayDecision.STOP,
                replay_result=None,
                provider_status_code=None,
                error_code=safe_error_code,
                now=self.clock(),
                allow_retry=allow_retry,
                terminal_code="external_effect.prepare_failed",
            )
            self._attach_terminal_trace(adapter, terminal)
            return self._terminal_result(terminal)
        try:
            provider_key = self._validated_winner_key(record)
            call = adapter.finalize_provider_call(prepared, provider_key)
            self._validate_provider_call(
                call,
                provider_key=provider_key,
                profile=profile,
            )
        except Exception:
            terminal = self._finish_attempt(
                record,
                outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
                replay_decision=ReplayDecision.STOP,
                replay_result=None,
                provider_status_code=None,
                error_code="provider_call_finalize_failed",
                now=self.clock(),
                allow_retry=allow_retry,
                terminal_code="external_effect.prepare_failed",
            )
            self._attach_terminal_trace(adapter, terminal)
            raise ExternalEffectError(
                "external_effect.prepare_failed",
                retryable=False,
                node_id=context.node_id,
            ) from None

        try:
            record = self.repository.mark_in_flight(record, now=self.clock())
        except Exception:
            self._raise_repository_retry(
                allow_retry=allow_retry,
                node_id=context.node_id,
                terminal_code="external_effect.claim_wait",
            )
        try:
            invocation = adapter.invoke_effect(call)
            if not isinstance(invocation, ProviderInvocationResult):
                raise TypeError("invalid provider invocation result")
            provider_status_code = invocation.provider_status_code
            if provider_status_code is not None and (
                isinstance(provider_status_code, bool)
                or not isinstance(provider_status_code, int)
                or not 100 <= provider_status_code <= 599
            ):
                raise TypeError("invalid provider status code")
        except EffectInvocationFailure as failure:
            safe_error_code = safe_effect_error_code(
                failure.error_code,
                fallback="provider_call_failed",
            )
            outcome = failure.outcome
            if isinstance(outcome, EffectOutcome):
                decision = decide_replay(
                    outcome=outcome,
                    provider_replay=record.spec.profile.provider_replay,
                    result_reuse=record.spec.profile.result_reuse,
                    has_replay_result=False,
                    retry_before_effect=failure.retry_before_effect is True,
                    now=self.clock(),
                    replay_deadline_at=record.spec.replay_deadline_at,
                )
            else:
                outcome = EffectOutcome.EFFECT_OUTCOME_UNKNOWN
                decision = ReplayDecision.STOP
                safe_error_code = "provider_call_failed"
            if (
                decision
                in {
                    ReplayDecision.RETRY_BEFORE_EFFECT,
                    ReplayDecision.REPLAY_SAME_KEY,
                }
                and not allow_retry
            ):
                decision = ReplayDecision.STOP
            provider_status_code = failure.provider_status_code
            if (
                isinstance(provider_status_code, bool)
                or not isinstance(provider_status_code, int)
                or not 100 <= provider_status_code <= 599
            ):
                provider_status_code = None
            terminal = self._finish_attempt(
                record,
                outcome=outcome,
                replay_decision=decision,
                replay_result=None,
                provider_status_code=provider_status_code,
                error_code=safe_error_code,
                now=self.clock(),
                allow_retry=allow_retry,
                terminal_code=(
                    "external_effect.outcome_unknown"
                    if outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN
                    else f"external_effect.{safe_error_code}"
                ),
            )
            self._attach_terminal_trace(adapter, terminal)
            return self._terminal_result(terminal)
        except Exception:
            terminal = self._finish_attempt(
                record,
                outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
                replay_decision=ReplayDecision.STOP,
                replay_result=None,
                provider_status_code=None,
                error_code="provider_call_failed",
                now=self.clock(),
                allow_retry=allow_retry,
                terminal_code="external_effect.outcome_unknown",
            )
            self._attach_terminal_trace(adapter, terminal)
            return self._terminal_result(terminal)

        replay_result = None
        if profile.result_reuse is ResultReuseCapability.SUPPORTED:
            try:
                projection = adapter.replay_projection(
                    invocation.output,
                    profile=profile,
                )
                replay_result = canonical_replay_result(projection).value
            except Exception:
                replay_result = None
        decision = decide_replay(
            outcome=EffectOutcome.SUCCEEDED,
            provider_replay=profile.provider_replay,
            result_reuse=profile.result_reuse,
            has_replay_result=replay_result is not None,
        )
        terminal = self._finish_attempt(
            record,
            outcome=EffectOutcome.SUCCEEDED,
            replay_decision=decision,
            replay_result=replay_result,
            provider_status_code=invocation.provider_status_code,
            error_code=None,
            now=self.clock(),
            allow_retry=allow_retry,
            terminal_code="external_effect.outcome_unknown",
        )
        self._attach_terminal_trace(adapter, terminal)
        return invocation.output

    @staticmethod
    def _attach_terminal_trace(
        adapter: EffectAdapter,
        record: EffectAttemptRecord,
    ) -> None:
        metadata = getattr(adapter, "trace_metadata", None)
        safe_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        summary: dict[str, Any] = {
            "provider": record.spec.profile.provider,
            "operation": record.spec.profile.operation,
        }
        if record.outcome is not None:
            summary["outcome"] = record.outcome.value
        if record.replay_decision is not None:
            summary["replay_decision"] = record.replay_decision.value
        if record.error_code is not None:
            summary["error_code"] = record.error_code
        safe_metadata["external_effect"] = summary
        setattr(adapter, "trace_metadata", safe_metadata)

    def _finish_attempt(
        self,
        record: EffectAttemptRecord,
        *,
        outcome: EffectOutcome,
        replay_decision: ReplayDecision,
        replay_result: Any,
        provider_status_code: int | None,
        error_code: str | None,
        now: datetime,
        allow_retry: bool,
        terminal_code: str,
    ) -> EffectAttemptRecord:
        try:
            return self.repository.finish(
                record,
                outcome=outcome,
                replay_decision=replay_decision,
                replay_result=replay_result,
                provider_status_code=provider_status_code,
                error_code=error_code,
                now=now,
            )
        except Exception:
            self._raise_repository_retry(
                allow_retry=allow_retry,
                node_id=record.spec.context.node_id,
                terminal_code=terminal_code,
            )

    @staticmethod
    def _raise_repository_retry(
        *,
        allow_retry: bool,
        node_id: str,
        terminal_code: str,
    ) -> NoReturn:
        if allow_retry:
            raise ExternalEffectRetrySignal(
                "external_effect.claim_wait",
                node_id=node_id,
                terminal_code=terminal_code,
            ) from None
        raise ExternalEffectError(
            terminal_code,
            retryable=False,
            node_id=node_id,
        ) from None

    @staticmethod
    def _validate_prepared_request(
        prepared: PreparedEffectRequest,
        *,
        profile: ProviderContractProfile,
    ) -> None:
        if (
            not isinstance(prepared, PreparedEffectRequest)
            or prepared.profile != profile
            or not isinstance(prepared.effect_input_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", prepared.effect_input_digest) is None
        ):
            raise ExternalEffectError(
                "external_effect.prepare_failed",
                retryable=False,
            )

    @staticmethod
    def _validate_provider_call(
        call: PreparedProviderCall,
        *,
        provider_key: str | None,
        profile: ProviderContractProfile,
    ) -> None:
        if (
            not isinstance(call, PreparedProviderCall)
            or call.idempotency_key != provider_key
            or call.profile != profile
        ):
            raise ValueError("invalid finalized provider call")

    def _stop_retry_if_exhausted(
        self,
        record: EffectAttemptRecord,
        *,
        allow_retry: bool,
    ) -> EffectAttemptRecord:
        if allow_retry or record.replay_decision not in {
            ReplayDecision.RETRY_BEFORE_EFFECT,
            ReplayDecision.REPLAY_SAME_KEY,
        }:
            return record
        try:
            return self.repository.stop_retry(record, now=self.clock())
        except Exception:
            raise ExternalEffectError(
                self._terminal_failure_code(record),
                retryable=False,
                node_id=record.spec.context.node_id,
            ) from None

    def guard_read_only_slot(
        self,
        *,
        context: ExternalEffectContext,
        effect_sequence: int = 0,
    ) -> None:
        guard = getattr(self.repository, "guard_read_only_slot", None)
        if callable(guard):
            try:
                guard(context=context, effect_sequence=effect_sequence)
            except ExternalEffectError:
                raise
            except Exception:
                self._raise_repository_retry(
                    allow_retry=self.retry_available(),
                    node_id=context.node_id,
                    terminal_code="external_effect.claim_wait",
                )

    def _validated_winner_key(self, record: EffectAttemptRecord) -> str | None:
        profile = record.spec.profile
        if profile.provider_replay is not ProviderReplayCapability.SUPPORTED:
            return None
        key_version = record.spec.key_version
        if (
            not key_version
            or key_version not in self.keyring
            or record.spec.key_format_version != profile.key_format
        ):
            raise ExternalEffectError(
                "external_effect.prepare_failed",
                retryable=False,
                node_id=record.spec.context.node_id,
            )
        key = self._provider_key(
            record.spec.context,
            profile,
            effect_sequence=record.spec.effect_sequence,
            key_version=key_version,
        )
        fingerprint = hashlib.sha256(key.encode("ascii")).hexdigest()
        if fingerprint != record.spec.idempotency_key_fingerprint:
            raise ExternalEffectError(
                "external_effect.prepare_failed",
                retryable=False,
                node_id=record.spec.context.node_id,
            )
        if profile.key_max_length and len(key) > profile.key_max_length:
            raise ExternalEffectError(
                "external_effect.prepare_failed",
                retryable=False,
                node_id=record.spec.context.node_id,
            )
        return key

    def _provider_key(
        self,
        context: ExternalEffectContext,
        profile: ProviderContractProfile,
        *,
        effect_sequence: int,
        key_version: str,
    ) -> str:
        return build_provider_idempotency_key(
            self.keyring[key_version],
            organization_id=context.organization_id,
            app_id=context.app_id,
            workflow_id=context.workflow_id,
            execution_id=context.execution_id,
            node_invocation_id=context.node_invocation_id,
            operation=profile.operation,
            effect_sequence=effect_sequence,
        )

    @staticmethod
    def _terminal_result(record: EffectAttemptRecord) -> Any:
        if record.replay_decision is ReplayDecision.REUSE_RESULT:
            return record.replay_result
        if record.replay_decision is ReplayDecision.RESULT_UNAVAILABLE:
            raise ExternalEffectError(
                "external_effect.result_unavailable",
                retryable=False,
                node_id=record.spec.context.node_id,
            )
        if record.replay_decision in {
            ReplayDecision.RETRY_BEFORE_EFFECT,
            ReplayDecision.REPLAY_SAME_KEY,
        }:
            raise ExternalEffectRetrySignal(
                "external_effect.retry_allowed",
                node_id=record.spec.context.node_id,
                terminal_code=ExternalEffectExecutor._terminal_failure_code(record),
            )
        if record.outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN:
            raise ExternalEffectError(
                "external_effect.outcome_unknown",
                retryable=False,
                node_id=record.spec.context.node_id,
            )
        if record.outcome is EffectOutcome.FAILED_BEFORE_EFFECT:
            raise ExternalEffectError(
                ExternalEffectExecutor._failed_before_effect_code(record),
                retryable=False,
                node_id=record.spec.context.node_id,
            )
        raise ExternalEffectError(
            "external_effect.stopped",
            retryable=False,
            node_id=record.spec.context.node_id,
        )

    @staticmethod
    def _terminal_failure_code(record: EffectAttemptRecord) -> str:
        if record.outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN:
            return "external_effect.outcome_unknown"
        if record.outcome is EffectOutcome.FAILED_BEFORE_EFFECT:
            return ExternalEffectExecutor._failed_before_effect_code(record)
        return "external_effect.stopped"

    @staticmethod
    def _failed_before_effect_code(record: EffectAttemptRecord) -> str:
        safe_code = safe_effect_error_code(
            record.error_code or "provider_call_failed",
            fallback="provider_call_failed",
        )
        return (
            "external_effect.prepare_failed"
            if safe_code == "provider_call_finalize_failed"
            else f"external_effect.{safe_code}"
        )
