"""Bounded Conversation context planning, claim, and provider-attempt fencing."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from apps.memory.application.content import memory_content_aad
from apps.memory.application.execution import (
    ConversationExecutionMemoryRepositoryPort,
    ConversationExecutionScope,
    ResolvedConversationExecution,
    require_runtime_binding,
    resolve_command_for_binding,
)
from apps.memory.domain.conversation import (
    CONVERSATION_SOURCE_FREE_PROOF_VERSION,
    ConversationMemoryEntry,
    EntryLifecycle,
    EntryType,
    ProtectedContent,
    TurnStatus,
)
from apps.memory.domain.errors import (
    MemoryContextConflictError,
    MemoryContextUnavailableError,
)

CONTEXT_SERIALIZER_VERSION = "conversation-history-jsonl-v1"
CONTEXT_POLICY_VERSION = "conversation-window-v1"
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ContextLeaseState(StrEnum):
    ISSUED = "issued"
    CLAIMED = "claimed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class ContextAttemptState(StrEnum):
    CLAIMED = "claimed"
    PROVIDER_STARTED = "provider_started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECONCILED = "reconciled"


@dataclass(frozen=True, slots=True)
class ContextEntryReference:
    entry_id: uuid.UUID
    turn_id: uuid.UUID
    sequence: int
    entry_type: EntryType
    content_revision: int
    content_digest: str
    dependency_proof_version: str | None = None


@dataclass(frozen=True, slots=True)
class ContextCandidatePair:
    turn_id: uuid.UUID
    turn_sequence: int
    status: TurnStatus
    user: ContextEntryReference | None
    assistant: ContextEntryReference | None
    dependency_count: int

    @property
    def eligible(self) -> bool:
        return (
            self.status is TurnStatus.COMPLETED
            and self.user is not None
            and self.assistant is not None
            and self.user.entry_type is EntryType.USER_TURN
            and self.assistant.entry_type is EntryType.ASSISTANT_TURN
            and self.user.dependency_proof_version
            == CONVERSATION_SOURCE_FREE_PROOF_VERSION
            and self.assistant.dependency_proof_version
            == CONVERSATION_SOURCE_FREE_PROOF_VERSION
            and self.dependency_count == 0
        )


@dataclass(slots=True)
class MemoryContextPlan:
    id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    ordered_pairs: tuple[ContextCandidatePair, ...]
    policy_version: str
    content_digest: str
    lifecycle_revision: int
    content_revision: int
    turn_version: int
    authorization_revision_set_digest: str
    expires_at: datetime


@dataclass(slots=True)
class MemoryContextLease:
    id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    plan_id: uuid.UUID
    node_invocation_id: uuid.UUID
    provider_capability_reference: str
    provider_capability_revision: str
    provider_attempt_id: uuid.UUID | None
    state: ContextLeaseState
    claim_generation: int
    claim_deadline_at: datetime | None
    expires_at: datetime

    def claim(self, *, provider_attempt_id: uuid.UUID, deadline: datetime, now: datetime) -> bool:
        if now >= self.expires_at or deadline <= now or deadline > self.expires_at:
            raise MemoryContextUnavailableError()
        if self.state is ContextLeaseState.CLAIMED:
            if (
                self.provider_attempt_id != provider_attempt_id
                or self.claim_deadline_at is None
            ):
                raise MemoryContextConflictError()
            return True
        if self.state is not ContextLeaseState.ISSUED:
            raise MemoryContextUnavailableError()
        self.provider_attempt_id = provider_attempt_id
        self.state = ContextLeaseState.CLAIMED
        self.claim_generation += 1
        self.claim_deadline_at = deadline
        return False

    def reclaim(
        self,
        *,
        provider_attempt_id: uuid.UUID,
        expected_claim_generation: int,
        expected_claim_deadline_at: datetime,
        deadline: datetime,
        now: datetime,
    ) -> None:
        if now >= self.expires_at or deadline <= now or deadline > self.expires_at:
            raise MemoryContextUnavailableError()
        if (
            self.state is not ContextLeaseState.CLAIMED
            or self.provider_attempt_id != provider_attempt_id
            or self.claim_generation != expected_claim_generation
            or self.claim_deadline_at != expected_claim_deadline_at
            or now < expected_claim_deadline_at
        ):
            raise MemoryContextConflictError()
        self.claim_generation += 1
        self.claim_deadline_at = deadline


@dataclass(slots=True)
class MemoryContextProviderAttempt:
    id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    lease_id: uuid.UUID
    plan_id: uuid.UUID
    node_invocation_id: uuid.UUID
    provider_capability_reference: str
    provider_capability_revision: str
    status: ContextAttemptState
    version: int
    claim_generation: int
    claim_deadline_at: datetime
    provider_started_at: datetime | None = None
    usage_reference: str | None = None
    safe_failure_reason: str | None = None
    terminal_at: datetime | None = None

    def reclaim(
        self,
        *,
        expected_claim_generation: int,
        expected_claim_deadline_at: datetime,
        claim_generation: int,
        claim_deadline_at: datetime,
        now: datetime,
    ) -> None:
        if (
            self.status is not ContextAttemptState.CLAIMED
            or self.claim_generation != expected_claim_generation
            or self.claim_deadline_at != expected_claim_deadline_at
            or now < self.claim_deadline_at
            or claim_generation != self.claim_generation + 1
            or claim_deadline_at <= now
        ):
            raise MemoryContextConflictError()
        self.version += 1
        self.claim_generation = claim_generation
        self.claim_deadline_at = claim_deadline_at

    def mark_provider_started(
        self,
        *,
        expected_version: int,
        usage_reference: str,
        now: datetime,
    ) -> bool:
        if not _SAFE_REFERENCE.fullmatch(usage_reference):
            raise MemoryContextConflictError()
        if self.status is ContextAttemptState.PROVIDER_STARTED:
            if self.usage_reference == usage_reference:
                return True
            raise MemoryContextConflictError()
        if (
            self.status is not ContextAttemptState.CLAIMED
            or self.version != expected_version
            or now >= self.claim_deadline_at
        ):
            raise MemoryContextConflictError()
        self.status = ContextAttemptState.PROVIDER_STARTED
        self.version += 1
        self.provider_started_at = now
        self.usage_reference = usage_reference
        return False

    def finish(
        self,
        *,
        expected_version: int,
        outcome: ContextAttemptState,
        safe_failure_reason: str | None,
        now: datetime,
    ) -> bool:
        terminal = {
            ContextAttemptState.SUCCEEDED,
            ContextAttemptState.FAILED,
            ContextAttemptState.OUTCOME_UNKNOWN,
        }
        if outcome not in terminal:
            raise MemoryContextConflictError()
        if self.status in terminal:
            if (
                self.status is outcome
                and self.safe_failure_reason == safe_failure_reason
            ):
                return True
            raise MemoryContextConflictError()
        allowed_source = self.status is ContextAttemptState.PROVIDER_STARTED or (
            self.status is ContextAttemptState.CLAIMED
            and outcome is ContextAttemptState.FAILED
        )
        if not allowed_source or self.version != expected_version:
            raise MemoryContextConflictError()
        if outcome is ContextAttemptState.SUCCEEDED:
            if safe_failure_reason is not None:
                raise MemoryContextConflictError()
        elif not isinstance(safe_failure_reason, str) or not _SAFE_REFERENCE.fullmatch(
            safe_failure_reason
        ):
            raise MemoryContextConflictError()
        self.status = outcome
        self.version += 1
        self.safe_failure_reason = safe_failure_reason
        self.terminal_at = now
        return False


@dataclass(frozen=True, slots=True)
class BuildMemoryContextCommand:
    binding: ResolvedConversationExecution
    node_invocation_id: uuid.UUID
    provider_capability_reference: str
    provider_capability_revision: str
    provider_attempt_id: uuid.UUID
    expires_at: datetime
    now: datetime


@dataclass(frozen=True, slots=True)
class BuildMemoryContextResult:
    plan_id: uuid.UUID
    lease_id: uuid.UUID
    candidate_pair_count: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class ClaimMemoryContextCommand:
    binding: ResolvedConversationExecution
    plan_id: uuid.UUID
    lease_id: uuid.UUID
    node_invocation_id: uuid.UUID
    provider_capability_reference: str
    provider_capability_revision: str
    provider_attempt_id: uuid.UUID
    claim_deadline_at: datetime
    now: datetime


@dataclass(frozen=True, slots=True)
class ClaimedMemoryContext:
    attempt_id: uuid.UUID
    attempt_version: int
    history_block: str
    selected_pair_count: int
    token_count: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class MarkContextProviderStartedCommand:
    organization_id: uuid.UUID
    attempt_id: uuid.UUID
    expected_version: int
    usage_reference: str
    now: datetime


@dataclass(frozen=True, slots=True)
class FinishContextProviderAttemptCommand:
    organization_id: uuid.UUID
    attempt_id: uuid.UUID
    expected_version: int
    outcome: ContextAttemptState
    safe_failure_reason: str | None
    now: datetime


class MemoryContextRepositoryPort(ConversationExecutionMemoryRepositoryPort, Protocol):
    def list_prior_context_candidates(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        before_turn_sequence: int,
        limit: int,
    ) -> tuple[ContextCandidatePair, ...]: ...

    def find_context_plan(self, plan_id: uuid.UUID) -> MemoryContextPlan | None: ...

    def add_context_plan(self, plan: MemoryContextPlan) -> None: ...

    def find_context_lease(self, lease_id: uuid.UUID) -> MemoryContextLease | None: ...

    def lock_context_lease(self, lease_id: uuid.UUID) -> MemoryContextLease | None: ...

    def add_context_lease(self, lease: MemoryContextLease) -> None: ...

    def save_context_lease(self, lease: MemoryContextLease) -> None: ...

    def get_context_entry(self, reference: ContextEntryReference) -> ConversationMemoryEntry | None: ...

    def find_context_attempt(self, attempt_id: uuid.UUID) -> MemoryContextProviderAttempt | None: ...

    def lock_context_attempt(self, attempt_id: uuid.UUID) -> MemoryContextProviderAttempt | None: ...

    def add_context_attempt(self, attempt: MemoryContextProviderAttempt) -> None: ...

    def save_context_attempt(self, attempt: MemoryContextProviderAttempt) -> None: ...


class MemoryContentRevealPort(Protocol):
    def reveal(self, protected: ProtectedContent, *, associated_data: str) -> str | None: ...


class MemoryContextTokenCounterPort(Protocol):
    def count(self, value: str) -> int: ...


class _ContextUseCase:
    def __init__(self, *, repository, uow) -> None:
        self.repository = repository
        self.uow = uow

    def _execute(self, operation):
        self.uow.begin()
        try:
            result = operation()
            self.uow.commit()
            return result
        except Exception:
            self.uow.rollback()
            raise


class BuildMemoryContextUseCase(_ContextUseCase):
    def execute(self, command: BuildMemoryContextCommand) -> BuildMemoryContextResult:
        def operation() -> BuildMemoryContextResult:
            _validate_capability(command)
            if command.expires_at <= command.now:
                raise MemoryContextUnavailableError()
            scope = _locked_execution_scope(self.repository, command.binding, command.now)
            if scope.turn.status is not TurnStatus.RUNNING:
                raise MemoryContextUnavailableError()
            plan_id = uuid.uuid5(command.provider_attempt_id, "memory-context-plan-v1")
            lease_id = uuid.uuid5(command.provider_attempt_id, "memory-context-lease-v1")
            existing_plan = self.repository.find_context_plan(plan_id)
            existing_lease = self.repository.find_context_lease(lease_id)
            if existing_plan is not None or existing_lease is not None:
                if not _same_build(existing_plan, existing_lease, command):
                    raise MemoryContextConflictError()
                return BuildMemoryContextResult(
                    plan_id=plan_id,
                    lease_id=lease_id,
                    candidate_pair_count=len(existing_plan.ordered_pairs),
                    replayed=True,
                )
            candidates = self.repository.list_prior_context_candidates(
                organization_id=command.binding.organization_id,
                session_id=command.binding.session_id,
                before_turn_sequence=scope.turn.sequence,
                limit=command.binding.max_turns,
            )
            selected: list[ContextCandidatePair] = []
            for candidate in candidates:
                if not candidate.eligible:
                    break
                selected.append(candidate)
            if candidates and not selected:
                raise MemoryContextUnavailableError()
            ordered_pairs = tuple(selected)
            digest = _plan_digest(command, ordered_pairs)
            plan = MemoryContextPlan(
                id=plan_id,
                organization_id=command.binding.organization_id,
                session_id=command.binding.session_id,
                turn_id=command.binding.turn_id,
                ordered_pairs=ordered_pairs,
                policy_version=CONTEXT_POLICY_VERSION,
                content_digest=digest,
                lifecycle_revision=command.binding.lifecycle_revision,
                content_revision=scope.session.content_revision,
                turn_version=command.binding.turn_version,
                authorization_revision_set_digest=_authorization_digest(scope),
                expires_at=command.expires_at,
            )
            lease = MemoryContextLease(
                id=lease_id,
                organization_id=command.binding.organization_id,
                session_id=command.binding.session_id,
                turn_id=command.binding.turn_id,
                plan_id=plan_id,
                node_invocation_id=command.node_invocation_id,
                provider_capability_reference=command.provider_capability_reference,
                provider_capability_revision=command.provider_capability_revision,
                provider_attempt_id=None,
                state=ContextLeaseState.ISSUED,
                claim_generation=0,
                claim_deadline_at=None,
                expires_at=command.expires_at,
            )
            self.repository.add_context_plan(plan)
            self.repository.add_context_lease(lease)
            return BuildMemoryContextResult(
                plan_id=plan_id,
                lease_id=lease_id,
                candidate_pair_count=len(ordered_pairs),
                replayed=False,
            )

        return self._execute(operation)


class ClaimMemoryContextUseCase(_ContextUseCase):
    def __init__(self, *, repository, uow, content_cipher, token_counter) -> None:
        super().__init__(repository=repository, uow=uow)
        self.content_cipher = content_cipher
        self.token_counter = token_counter

    def execute(self, command: ClaimMemoryContextCommand) -> ClaimedMemoryContext:
        def operation() -> ClaimedMemoryContext:
            _validate_capability(command)
            scope = _locked_execution_scope(self.repository, command.binding, command.now)
            if scope.turn.status is not TurnStatus.RUNNING:
                raise MemoryContextUnavailableError()
            plan = self.repository.find_context_plan(command.plan_id)
            lease = self.repository.lock_context_lease(command.lease_id)
            if not _same_claim(plan, lease, command, scope):
                raise MemoryContextConflictError()
            previous_claim_generation = lease.claim_generation
            previous_claim_deadline_at = lease.claim_deadline_at
            previously_issued = lease.state is ContextLeaseState.ISSUED
            replayed = lease.claim(
                provider_attempt_id=command.provider_attempt_id,
                deadline=command.claim_deadline_at,
                now=command.now,
            )
            attempt_id = command.provider_attempt_id
            existing = self.repository.lock_context_attempt(attempt_id)
            if existing is not None:
                if (
                    existing.lease_id != lease.id
                    or existing.organization_id != command.binding.organization_id
                    or existing.session_id != command.binding.session_id
                    or existing.turn_id != command.binding.turn_id
                    or existing.plan_id != plan.id
                    or existing.node_invocation_id != command.node_invocation_id
                    or existing.provider_capability_reference
                    != command.provider_capability_reference
                    or existing.provider_capability_revision
                    != command.provider_capability_revision
                ):
                    raise MemoryContextConflictError()
            reclaiming = (
                lease.state is ContextLeaseState.CLAIMED
                and lease.provider_attempt_id == command.provider_attempt_id
                and previous_claim_deadline_at is not None
                and command.now >= previous_claim_deadline_at
            )
            if reclaiming:
                if existing is None:
                    raise MemoryContextConflictError()
                if existing.status is ContextAttemptState.CLAIMED:
                    next_claim_generation = previous_claim_generation + 1
                    existing.reclaim(
                        expected_claim_generation=previous_claim_generation,
                        expected_claim_deadline_at=previous_claim_deadline_at,
                        claim_generation=next_claim_generation,
                        claim_deadline_at=command.claim_deadline_at,
                        now=command.now,
                    )
                    lease.reclaim(
                        provider_attempt_id=command.provider_attempt_id,
                        expected_claim_generation=previous_claim_generation,
                        expected_claim_deadline_at=previous_claim_deadline_at,
                        deadline=command.claim_deadline_at,
                        now=command.now,
                    )
                    self.repository.save_context_attempt(existing)
                elif (
                    existing.status is not ContextAttemptState.PROVIDER_STARTED
                    or existing.claim_generation != previous_claim_generation
                    or existing.claim_deadline_at != previous_claim_deadline_at
                ):
                    raise MemoryContextConflictError()
                attempt = existing
                replayed = True
            else:
                if existing is not None and (
                    existing.claim_generation != lease.claim_generation
                    or existing.claim_deadline_at != lease.claim_deadline_at
                    or existing.status not in {
                        ContextAttemptState.CLAIMED,
                        ContextAttemptState.PROVIDER_STARTED,
                        ContextAttemptState.SUCCEEDED,
                    }
                ):
                    raise MemoryContextConflictError()
                if existing is not None:
                    attempt = existing
                    replayed = True
                else:
                    if not previously_issued:
                        raise MemoryContextConflictError()
                    attempt = MemoryContextProviderAttempt(
                        id=attempt_id,
                        organization_id=command.binding.organization_id,
                        session_id=command.binding.session_id,
                        turn_id=command.binding.turn_id,
                        lease_id=lease.id,
                        plan_id=plan.id,
                        node_invocation_id=command.node_invocation_id,
                        provider_capability_reference=command.provider_capability_reference,
                        provider_capability_revision=command.provider_capability_revision,
                        status=ContextAttemptState.CLAIMED,
                        version=1,
                        claim_generation=lease.claim_generation,
                        claim_deadline_at=command.claim_deadline_at,
                    )
                    self.repository.add_context_attempt(attempt)
            self.repository.save_context_lease(lease)
            selected, block, token_count = self._materialize(
                plan,
                command.binding,
                now=command.now,
            )
            return ClaimedMemoryContext(
                attempt_id=attempt.id,
                attempt_version=attempt.version,
                history_block=block,
                selected_pair_count=len(selected),
                token_count=token_count,
                replayed=replayed,
            )

        return self._execute(operation)

    def _materialize(
        self,
        plan: MemoryContextPlan,
        binding: ResolvedConversationExecution,
        *,
        now: datetime,
    ) -> tuple[tuple[tuple[str, str], ...], str, int]:
        if not plan.ordered_pairs:
            return (), "", 0
        selected_newest: list[tuple[str, str]] = []
        selected_block = ""
        selected_tokens = 0
        for pair in plan.ordered_pairs:
            values = self._pair_values(pair, binding, now=now)
            candidate = tuple(reversed((*selected_newest, values)))
            block = serialize_untrusted_history(candidate)
            tokens = self.token_counter.count(block)
            if tokens > binding.max_context_tokens:
                break
            selected_newest.append(values)
            selected_block = block
            selected_tokens = tokens
        if not selected_newest:
            raise MemoryContextUnavailableError()
        return tuple(reversed(selected_newest)), selected_block, selected_tokens

    def _pair_values(
        self,
        pair: ContextCandidatePair,
        binding: ResolvedConversationExecution,
        *,
        now: datetime,
    ) -> tuple[str, str]:
        if pair.user is None or pair.assistant is None:
            raise MemoryContextUnavailableError()
        values: list[str] = []
        for reference in (pair.user, pair.assistant):
            entry = self.repository.get_context_entry(reference)
            if (
                entry is None
                or entry.organization_id != binding.organization_id
                or entry.session_id != binding.session_id
                or entry.turn_id != reference.turn_id
                or entry.entry_type is not reference.entry_type
                or entry.lifecycle is not EntryLifecycle.APPROVED
                or entry.channel != "conversation"
                or entry.invalidated_at is not None
                or (entry.expires_at is not None and now >= entry.expires_at)
                or entry.content_revision != reference.content_revision
                or entry.content is None
                or entry.content.model is None
                or entry.content.model.content_digest != reference.content_digest
            ):
                raise MemoryContextUnavailableError()
            value = self.content_cipher.reveal(
                entry.content.model,
                associated_data=memory_content_aad(
                    organization_id=binding.organization_id,
                    session_id=binding.session_id,
                    turn_id=entry.turn_id,
                    entry_id=entry.id,
                    projection="model",
                ),
            )
            if value is None:
                raise MemoryContextUnavailableError()
            values.append(value)
        return values[0], values[1]


class MarkContextProviderStartedUseCase(_ContextUseCase):
    def execute(self, command: MarkContextProviderStartedCommand) -> int:
        def operation() -> int:
            attempt = self.repository.lock_context_attempt(command.attempt_id)
            if attempt is None or attempt.organization_id != command.organization_id:
                raise MemoryContextConflictError()
            attempt.mark_provider_started(
                expected_version=command.expected_version,
                usage_reference=command.usage_reference,
                now=command.now,
            )
            self.repository.save_context_attempt(attempt)
            return attempt.version

        return self._execute(operation)


class FinishContextProviderAttemptUseCase(_ContextUseCase):
    def execute(self, command: FinishContextProviderAttemptCommand) -> int:
        def operation() -> int:
            attempt = self.repository.lock_context_attempt(command.attempt_id)
            if attempt is None or attempt.organization_id != command.organization_id:
                raise MemoryContextConflictError()
            attempt.finish(
                expected_version=command.expected_version,
                outcome=command.outcome,
                safe_failure_reason=command.safe_failure_reason,
                now=command.now,
            )
            self.repository.save_context_attempt(attempt)
            return attempt.version

        return self._execute(operation)


def serialize_untrusted_history(pairs: tuple[tuple[str, str], ...]) -> str:
    lines = ["<UNTRUSTED_CONVERSATION_HISTORY version=\"1\">"]
    for user_value, assistant_value in pairs:
        lines.append(
            json.dumps(
                {"role": "user", "content": user_value},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        lines.append(
            json.dumps(
                {"role": "assistant", "content": assistant_value},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    lines.append("</UNTRUSTED_CONVERSATION_HISTORY>")
    return "\n".join(lines)


def _locked_execution_scope(repository, binding, now) -> ConversationExecutionScope:
    scope = repository.resolve_execution_scope(
        resolve_command_for_binding(binding),
        for_update=True,
    )
    if scope is None:
        raise MemoryContextUnavailableError()
    try:
        require_runtime_binding(scope, binding, now=now)
    except Exception as exc:
        raise MemoryContextUnavailableError() from exc
    return scope


def _validate_capability(command) -> None:
    if (
        not _SAFE_REFERENCE.fullmatch(command.provider_capability_reference)
        or not _SAFE_REFERENCE.fullmatch(command.provider_capability_revision)
    ):
        raise MemoryContextConflictError()


def _same_build(plan, lease, command) -> bool:
    return (
        isinstance(plan, MemoryContextPlan)
        and isinstance(lease, MemoryContextLease)
        and plan.organization_id == command.binding.organization_id
        and plan.session_id == command.binding.session_id
        and plan.turn_id == command.binding.turn_id
        and lease.organization_id == command.binding.organization_id
        and lease.session_id == command.binding.session_id
        and lease.turn_id == command.binding.turn_id
        and lease.plan_id == plan.id
        and lease.node_invocation_id == command.node_invocation_id
        and lease.provider_attempt_id in {None, command.provider_attempt_id}
        and lease.provider_capability_reference
        == command.provider_capability_reference
        and lease.provider_capability_revision
        == command.provider_capability_revision
    )


def _same_claim(plan, lease, command, scope) -> bool:
    return (
        _same_build(plan, lease, command)
        and plan.lifecycle_revision == command.binding.lifecycle_revision
        and plan.content_revision == scope.session.content_revision
        and plan.turn_version == command.binding.turn_version
        and plan.authorization_revision_set_digest == _authorization_digest(scope)
        and plan.expires_at > command.now
        and plan.policy_version == CONTEXT_POLICY_VERSION
        and plan.content_digest == _plan_digest(command, plan.ordered_pairs)
    )


def _plan_digest(command, pairs: tuple[ContextCandidatePair, ...]) -> str:
    payload = {
        "serializer": CONTEXT_SERIALIZER_VERSION,
        "policy": CONTEXT_POLICY_VERSION,
        "organization_id": str(command.binding.organization_id),
        "session_id": str(command.binding.session_id),
        "turn_id": str(command.binding.turn_id),
        "node_invocation_id": str(command.node_invocation_id),
        "provider_attempt_id": str(command.provider_attempt_id),
        "capability": command.provider_capability_reference,
        "capability_revision": command.provider_capability_revision,
        "pairs": [
            {
                "turn_id": str(pair.turn_id),
                "turn_sequence": pair.turn_sequence,
                "user": _reference_payload(pair.user),
                "assistant": _reference_payload(pair.assistant),
            }
            for pair in pairs
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _authorization_digest(scope: ConversationExecutionScope) -> str:
    grant = scope.grant
    payload = {
        "grant_id": str(grant.id),
        "deployment_id": str(grant.deployment_id),
        "deployment_version": grant.deployment_version,
        "audience_kind": grant.audience_kind.value,
        "verifier_key_version": grant.verifier_key_version,
        "expires_at": grant.expires_at.isoformat(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _reference_payload(reference: ContextEntryReference | None):
    if reference is None:
        return None
    return {
        "entry_id": str(reference.entry_id),
        "turn_id": str(reference.turn_id),
        "sequence": reference.sequence,
        "entry_type": reference.entry_type.value,
        "content_revision": reference.content_revision,
        "content_digest": reference.content_digest,
    }


__all__ = [
    "BuildMemoryContextCommand",
    "BuildMemoryContextResult",
    "BuildMemoryContextUseCase",
    "ClaimMemoryContextCommand",
    "ClaimMemoryContextUseCase",
    "ClaimedMemoryContext",
    "ContextAttemptState",
    "ContextCandidatePair",
    "ContextEntryReference",
    "ContextLeaseState",
    "FinishContextProviderAttemptCommand",
    "FinishContextProviderAttemptUseCase",
    "MarkContextProviderStartedCommand",
    "MarkContextProviderStartedUseCase",
    "MemoryContextLease",
    "MemoryContextPlan",
    "MemoryContextProviderAttempt",
    "serialize_untrusted_history",
]
