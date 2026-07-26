"""Memory-side safe projection for one Workflow-owned Conversation execution."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol

from apps.memory.application.public_lifecycle import PublicDeploymentBinding
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationMemoryEntry,
    ConversationSession,
    ConversationTurn,
    DispatchStatus,
    EntryLifecycle,
    EntryType,
    MemoryTurnDispatchJob,
    ProtectedContent,
    TurnStatus,
)
from apps.memory.domain.errors import (
    AccessGrantNotUsableError,
    DispatchStateConflictError,
    EntryNotFoundError,
    StaleTurnVersionError,
)
from apps.memory.domain.public_access import AccessGrantState, ConversationAccessGrant


@dataclass(frozen=True, slots=True)
class ResolveConversationExecutionCommand:
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    dispatch_claim_generation: int
    broker_message_id: str
    turn_id: uuid.UUID
    memory_contract_version: str
    storage_generation: int
    minimum_worker_capability: str


@dataclass(frozen=True, slots=True)
class ResolveTerminalConversationExecutionCommand:
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    dispatch_claim_generation: int
    broker_message_id: str
    turn_id: uuid.UUID
    memory_contract_version: str
    storage_generation: int
    minimum_worker_capability: str
    workflow_admission_id: uuid.UUID
    execution_id: uuid.UUID
    attempt_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ConversationExecutionScope:
    deployment: PublicDeploymentBinding
    grant: ConversationAccessGrant
    session: ConversationSession
    turn: ConversationTurn
    dispatch: MemoryTurnDispatchJob


@dataclass(frozen=True, slots=True)
class ResolvedConversationExecution:
    organization_id: uuid.UUID
    app_id: uuid.UUID
    workflow_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int
    session_id: uuid.UUID
    turn_id: uuid.UUID
    dispatch_id: uuid.UUID
    dispatch_claim_generation: int
    broker_message_id: str
    request_fingerprint: str
    lifecycle_revision: int
    turn_version: int
    memory_contract_version: str
    mapping_version: str
    memory_policy_version: str
    storage_generation: int
    minimum_worker_capability: str
    start_node_id: str
    input_variable: str
    llm_node_id: str
    answer_node_id: str
    output_variable: str
    max_turns: int
    max_context_tokens: int


@dataclass(frozen=True, slots=True)
class ObserveConversationExecutionAdmittedCommand:
    binding: ResolvedConversationExecution
    workflow_admission_id: uuid.UUID
    claim_generation: int
    broker_message_id: str


@dataclass(frozen=True, slots=True)
class ObserveConversationExecutionRunningCommand:
    binding: ResolvedConversationExecution
    workflow_admission_id: uuid.UUID
    execution_id: uuid.UUID
    attempt_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ConversationExecutionObservation:
    lifecycle_revision: int
    turn_version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class RecoverConversationExecutionTerminalCommand:
    binding: ResolvedConversationExecution
    workflow_admission_id: uuid.UUID
    execution_id: uuid.UUID
    attempt_id: uuid.UUID
    node_invocation_id: uuid.UUID
    provider_attempt_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ConversationExecutionTerminalProjection:
    outcome: Literal["completed", "failed", "outcome_unknown"]
    safe_failure_reason: str | None
    result_entry_id: uuid.UUID | None = None
    result_digest: str | None = None
    provider_attempt_id: uuid.UUID | None = None
    usage_reference: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedTerminalConversationExecution:
    binding: ResolvedConversationExecution
    projection: ConversationExecutionTerminalProjection
    requires_memory_failure: bool = False
    requires_dispatch_acknowledgement: bool = False


@dataclass(frozen=True, slots=True)
class FinalizeReferenceConversationExecutionCommand:
    binding: ResolvedConversationExecution
    workflow_admission_id: uuid.UUID
    execution_id: uuid.UUID
    attempt_id: uuid.UUID
    safe_failure_reason: str
    acknowledge_dispatch: bool = False
    provider_attempt_id: uuid.UUID | None = None
    context_outcome: Literal["succeeded", "failed", "outcome_unknown"] = "failed"
    execution_outcome: Literal["failed", "outcome_unknown"] = "failed"


@dataclass(frozen=True, slots=True)
class ReadCurrentTurnInputCommand:
    binding: ResolvedConversationExecution
    execution_id: uuid.UUID
    attempt_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class CurrentTurnInput:
    entry_id: uuid.UUID
    model_content: ProtectedContent


class ConversationExecutionMemoryRepositoryPort(Protocol):
    def current_time(self) -> datetime: ...

    def resolve_execution_scope(
        self,
        command: ResolveConversationExecutionCommand,
        *,
        for_update: bool,
    ) -> ConversationExecutionScope | None: ...

    def resolve_terminal_execution_scope(
        self,
        command: ResolveTerminalConversationExecutionCommand,
        *,
        for_update: bool,
    ) -> ConversationExecutionScope | None: ...

    def save_session(self, session: ConversationSession) -> None: ...

    def save_turn(self, turn: ConversationTurn) -> None: ...

    def save_entry(self, entry: ConversationMemoryEntry) -> None: ...

    def save_dispatch_job(self, dispatch: MemoryTurnDispatchJob) -> None: ...

    def get_entry(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        entry_id: uuid.UUID,
    ) -> ConversationMemoryEntry | None: ...

    def lock_context_attempt(self, attempt_id: uuid.UUID): ...

    def save_context_attempt(self, attempt) -> None: ...


class _ExecutionUseCase:
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


class ResolveConversationExecutionUseCase(_ExecutionUseCase):
    def execute(
        self,
        command: ResolveConversationExecutionCommand,
    ) -> ResolvedConversationExecution:
        def operation() -> ResolvedConversationExecution:
            scope = self.repository.resolve_execution_scope(
                command,
                for_update=False,
            )
            if scope is None:
                raise AccessGrantNotUsableError()
            now = self.repository.current_time()
            _require_scope(scope, command=command, now=now)
            return _resolved(scope, broker_message_id=command.broker_message_id)

        return self._execute(operation)


class ResolveTerminalConversationExecutionUseCase(_ExecutionUseCase):
    def execute(
        self,
        command: ResolveTerminalConversationExecutionCommand,
    ) -> ResolvedTerminalConversationExecution | None:
        def operation() -> ResolvedTerminalConversationExecution | None:
            scope = self.repository.resolve_terminal_execution_scope(
                command,
                for_update=False,
            )
            if scope is None:
                return None
            acknowledged = (
                scope.dispatch.status is DispatchStatus.ACKNOWLEDGED
                and scope.dispatch.workflow_admission_reference
                == str(command.workflow_admission_id)
            )
            if not acknowledged and scope.dispatch.status not in {
                DispatchStatus.CLAIMED,
                DispatchStatus.PUBLISHED,
            }:
                return None
            if not acknowledged and _reference_scope_runtime_usable(
                scope,
                now=self.repository.current_time(),
            ):
                # The broker can deliver after send but before mark_published
                # commits.  A current CLAIMED row can therefore have no stored
                # broker id yet; let the normal active resolver acknowledge it.
                return None
            _require_reference_terminal_scope(
                scope,
                command,
                require_acknowledged=acknowledged,
            )
            binding = _resolved(
                scope,
                broker_message_id=command.broker_message_id,
            )
            if not acknowledged:
                if (
                    scope.turn.status is not TurnStatus.PENDING_DISPATCH
                ):
                    return None
                return ResolvedTerminalConversationExecution(
                    binding=binding,
                    projection=ConversationExecutionTerminalProjection(
                        outcome="failed",
                        safe_failure_reason="memory.runtime_authorization_stale",
                    ),
                    requires_memory_failure=True,
                    requires_dispatch_acknowledgement=True,
                )
            if not scope.turn.terminal:
                if (
                    scope.turn.status
                    not in {TurnStatus.QUEUED, TurnStatus.RUNNING}
                    or _reference_scope_runtime_usable(
                        scope,
                        now=self.repository.current_time(),
                    )
                ):
                    return None
                _require_reference_execution_identity(
                    scope,
                    execution_id=command.execution_id,
                    attempt_id=command.attempt_id,
                )
                provider_attempt_id = _provider_attempt_id(binding)
                context_attempt = self.repository.lock_context_attempt(
                    provider_attempt_id
                )
                usage_reference = None
                if context_attempt is not None:
                    if (
                        context_attempt.id != provider_attempt_id
                        or context_attempt.organization_id
                        != binding.organization_id
                        or context_attempt.session_id != binding.session_id
                        or context_attempt.turn_id != binding.turn_id
                        or context_attempt.node_invocation_id
                        != _node_invocation_id(binding)
                    ):
                        raise StaleTurnVersionError()
                    usage_reference = context_attempt.usage_reference
                return ResolvedTerminalConversationExecution(
                    binding=binding,
                    projection=ConversationExecutionTerminalProjection(
                        outcome="failed",
                        safe_failure_reason="memory.runtime_authorization_stale",
                        provider_attempt_id=provider_attempt_id,
                        usage_reference=usage_reference,
                    ),
                    requires_memory_failure=True,
                )
            if binding.turn_version < 2:
                raise StaleTurnVersionError()
            binding = replace(binding, turn_version=binding.turn_version - 1)
            projection = (
                _completed_terminal_projection(
                    scope,
                    repository=self.repository,
                    execution_id=command.execution_id,
                    attempt_id=command.attempt_id,
                )
                if scope.turn.status is TurnStatus.COMPLETED
                else _terminal_projection(
                    scope,
                    execution_id=command.execution_id,
                    attempt_id=command.attempt_id,
                )
            )
            return ResolvedTerminalConversationExecution(
                binding=binding,
                projection=projection,
            )

        return self._execute(operation)


class FinalizeReferenceConversationExecutionUseCase(_ExecutionUseCase):
    def execute(
        self,
        command: FinalizeReferenceConversationExecutionCommand,
    ) -> ConversationExecutionTerminalProjection:
        def operation() -> ConversationExecutionTerminalProjection:
            binding_command = resolve_command_for_binding(command.binding)
            resolve_command = ResolveTerminalConversationExecutionCommand(
                organization_id=binding_command.organization_id,
                dispatch_id=binding_command.dispatch_id,
                dispatch_claim_generation=(
                    binding_command.dispatch_claim_generation
                ),
                broker_message_id=binding_command.broker_message_id,
                turn_id=binding_command.turn_id,
                memory_contract_version=binding_command.memory_contract_version,
                storage_generation=binding_command.storage_generation,
                minimum_worker_capability=(
                    binding_command.minimum_worker_capability
                ),
                workflow_admission_id=command.workflow_admission_id,
                execution_id=command.execution_id,
                attempt_id=command.attempt_id,
            )
            scope = self.repository.resolve_terminal_execution_scope(
                resolve_command,
                for_update=True,
            )
            if scope is None:
                raise AccessGrantNotUsableError()
            _require_reference_terminal_scope(
                scope,
                resolve_command,
                require_acknowledged=not command.acknowledge_dispatch,
            )
            if (
                scope.turn.status
                not in {
                    TurnStatus.PENDING_DISPATCH,
                    TurnStatus.QUEUED,
                    TurnStatus.RUNNING,
                }
                or scope.turn.version != command.binding.turn_version
                or _reference_scope_runtime_usable(
                    scope,
                    now=self.repository.current_time(),
                )
            ):
                raise StaleTurnVersionError()
            if command.acknowledge_dispatch:
                if scope.turn.status is not TurnStatus.PENDING_DISPATCH:
                    raise StaleTurnVersionError()
            else:
                _require_reference_execution_identity(
                    scope,
                    execution_id=command.execution_id,
                    attempt_id=command.attempt_id,
                )
            user_entry = self.repository.get_entry(
                organization_id=scope.turn.organization_id,
                session_id=scope.session.id,
                entry_id=scope.turn.user_entry_id,
            )
            if (
                user_entry is None
                or user_entry.turn_id != scope.turn.id
                or user_entry.entry_type is not EntryType.USER_TURN
                or user_entry.lifecycle is not EntryLifecycle.PROVISIONAL
            ):
                raise EntryNotFoundError()
            now = self.repository.current_time()
            if command.acknowledge_dispatch:
                scope.turn.mark_queued(
                    expected_version=scope.turn.version,
                    now=now,
                )
                scope.dispatch.acknowledge(
                    claim_generation=scope.dispatch.claim_generation,
                    broker_message_id=command.binding.broker_message_id,
                    workflow_admission_reference=str(
                        command.workflow_admission_id
                    ),
                    now=now,
                )
                self.repository.save_dispatch_job(scope.dispatch)
            if command.provider_attempt_id is not None:
                context_attempt = self.repository.lock_context_attempt(
                    command.provider_attempt_id
                )
                if context_attempt is not None:
                    target = type(context_attempt.status)(
                        command.context_outcome
                    )
                    terminal_values = {
                        "succeeded",
                        "failed",
                        "outcome_unknown",
                    }
                    if context_attempt.status.value not in terminal_values:
                        context_attempt.finish(
                            expected_version=context_attempt.version,
                            outcome=target,
                            safe_failure_reason=(
                                None
                                if command.context_outcome == "succeeded"
                                else command.safe_failure_reason
                            ),
                            now=now,
                        )
                        self.repository.save_context_attempt(context_attempt)
            if scope.turn.status is TurnStatus.QUEUED:
                # Persist the deterministic Workflow identity in the same
                # terminal commit so a crash before admission.finish remains
                # exactly recoverable as a FAILED projection.
                scope.turn.execution_id = command.execution_id
                scope.turn.latest_attempt_id = command.attempt_id
            scope.turn.fail(
                expected_version=scope.turn.version,
                safe_reason_code=command.safe_failure_reason,
                now=now,
            )
            scope.session.release_terminal_turn(
                turn_id=scope.turn.id,
                expected_lifecycle_revision=scope.session.lifecycle_revision,
                now=now,
            )
            user_entry.reject(now=now)
            checkpoint_entry_id = uuid.uuid5(
                command.execution_id,
                "conversation-assistant-checkpoint-v1",
            )
            checkpoint_entry = self.repository.get_entry(
                organization_id=scope.turn.organization_id,
                session_id=scope.session.id,
                entry_id=checkpoint_entry_id,
            )
            if checkpoint_entry is not None:
                if (
                    checkpoint_entry.turn_id != scope.turn.id
                    or checkpoint_entry.entry_type
                    is not EntryType.ASSISTANT_TURN
                    or checkpoint_entry.lifecycle
                    is not EntryLifecycle.PROVISIONAL
                ):
                    raise StaleTurnVersionError()
                checkpoint_entry.reject(now=now)
                self.repository.save_entry(checkpoint_entry)
            self.repository.save_turn(scope.turn)
            self.repository.save_session(scope.session)
            self.repository.save_entry(user_entry)
            return ConversationExecutionTerminalProjection(
                outcome=command.execution_outcome,
                safe_failure_reason=command.safe_failure_reason,
            )

        return self._execute(operation)


class ObserveConversationExecutionAdmittedUseCase(_ExecutionUseCase):
    def execute(
        self,
        command: ObserveConversationExecutionAdmittedCommand,
    ) -> ConversationExecutionObservation:
        def operation() -> ConversationExecutionObservation:
            resolve_command = resolve_command_for_binding(
                command.binding,
                claim_generation=command.claim_generation,
                broker_message_id=command.broker_message_id,
            )
            scope = self.repository.resolve_execution_scope(
                resolve_command,
                for_update=True,
            )
            if scope is None:
                raise AccessGrantNotUsableError()
            now = self.repository.current_time()
            _require_scope(scope, command=resolve_command, now=now)
            _require_same_resolved(scope, command.binding)
            admission_reference = str(command.workflow_admission_id)
            already_acknowledged = (
                scope.dispatch.status is DispatchStatus.ACKNOWLEDGED
                and scope.dispatch.workflow_admission_reference
                == admission_reference
            )
            if scope.turn.status is TurnStatus.PENDING_DISPATCH:
                scope.turn.mark_queued(
                    expected_version=scope.turn.version,
                    now=now,
                )
                self.repository.save_turn(scope.turn)
            elif scope.turn.status not in {
                TurnStatus.QUEUED,
                TurnStatus.RUNNING,
            }:
                raise StaleTurnVersionError()
            dispatch_replayed = scope.dispatch.acknowledge(
                claim_generation=command.claim_generation,
                broker_message_id=command.broker_message_id,
                workflow_admission_reference=admission_reference,
                now=now,
            )
            self.repository.save_dispatch_job(scope.dispatch)
            return ConversationExecutionObservation(
                lifecycle_revision=scope.session.lifecycle_revision,
                turn_version=scope.turn.version,
                replayed=already_acknowledged and dispatch_replayed,
            )

        return self._execute(operation)


class ObserveConversationExecutionRunningUseCase(_ExecutionUseCase):
    def execute(
        self,
        command: ObserveConversationExecutionRunningCommand,
    ) -> ConversationExecutionObservation:
        def operation() -> ConversationExecutionObservation:
            resolve_command = resolve_command_for_binding(
                command.binding,
                claim_generation=command.binding.dispatch_claim_generation,
                broker_message_id=command.binding.broker_message_id,
            )
            scope = self.repository.resolve_execution_scope(
                resolve_command,
                for_update=True,
            )
            if scope is None:
                raise AccessGrantNotUsableError()
            now = self.repository.current_time()
            require_runtime_binding(scope, command.binding, now=now)
            if (
                scope.dispatch.status is not DispatchStatus.ACKNOWLEDGED
                or scope.dispatch.workflow_admission_reference
                != str(command.workflow_admission_id)
            ):
                raise DispatchStateConflictError()
            if scope.turn.status is TurnStatus.RUNNING:
                if (
                    scope.turn.execution_id == command.execution_id
                    and scope.turn.latest_attempt_id == command.attempt_id
                ):
                    return ConversationExecutionObservation(
                        lifecycle_revision=scope.session.lifecycle_revision,
                        turn_version=scope.turn.version,
                        replayed=True,
                    )
                scope.turn.handoff_running_attempt(
                    expected_version=scope.turn.version,
                    execution_id=command.execution_id,
                    attempt_id=command.attempt_id,
                    now=now,
                )
                self.repository.save_turn(scope.turn)
                return ConversationExecutionObservation(
                    lifecycle_revision=scope.session.lifecycle_revision,
                    turn_version=scope.turn.version,
                    replayed=False,
                )
            if scope.turn.status is not TurnStatus.QUEUED:
                raise StaleTurnVersionError()
            scope.turn.mark_running(
                expected_version=scope.turn.version,
                execution_id=command.execution_id,
                attempt_id=command.attempt_id,
                now=now,
            )
            self.repository.save_turn(scope.turn)
            return ConversationExecutionObservation(
                lifecycle_revision=scope.session.lifecycle_revision,
                turn_version=scope.turn.version,
                replayed=False,
            )

        return self._execute(operation)


class RecoverConversationExecutionTerminalUseCase(_ExecutionUseCase):
    def execute(
        self,
        command: RecoverConversationExecutionTerminalCommand,
    ) -> ConversationExecutionTerminalProjection | None:
        def operation() -> ConversationExecutionTerminalProjection | None:
            resolve_command = resolve_command_for_binding(command.binding)
            scope = self.repository.resolve_execution_scope(
                resolve_command,
                for_update=True,
            )
            if scope is None:
                raise AccessGrantNotUsableError()
            now = self.repository.current_time()
            _require_scope(scope, command=resolve_command, now=now)
            _require_same_terminal_recovery_identity(scope, command.binding)
            if (
                scope.dispatch.status is not DispatchStatus.ACKNOWLEDGED
                or scope.dispatch.workflow_admission_reference
                != str(command.workflow_admission_id)
            ):
                raise DispatchStateConflictError()
            if scope.turn.status is TurnStatus.FAILED:
                if scope.turn.version not in {
                    command.binding.turn_version,
                    command.binding.turn_version + 1,
                }:
                    raise StaleTurnVersionError()
                return _terminal_projection(
                    scope,
                    execution_id=command.execution_id,
                    attempt_id=command.attempt_id,
                )
            if scope.turn.status is not TurnStatus.RUNNING:
                return None
            if (
                scope.turn.execution_id != command.execution_id
                or scope.turn.latest_attempt_id != command.attempt_id
                or scope.turn.version != command.binding.turn_version
                or scope.session.active_turn_id != scope.turn.id
            ):
                raise StaleTurnVersionError()
            context_attempt = self.repository.lock_context_attempt(
                command.provider_attempt_id
            )
            if (
                context_attempt is None
                or context_attempt.id != command.provider_attempt_id
                or context_attempt.organization_id != command.binding.organization_id
                or context_attempt.session_id != command.binding.session_id
                or context_attempt.turn_id != command.binding.turn_id
                or context_attempt.node_invocation_id
                != command.node_invocation_id
                or context_attempt.status.value
                not in {"failed", "outcome_unknown"}
                or not isinstance(context_attempt.safe_failure_reason, str)
                or (
                    context_attempt.status.value == "outcome_unknown"
                    and (
                        context_attempt.safe_failure_reason
                        != "provider_outcome_unknown"
                        or context_attempt.usage_reference is None
                    )
                )
            ):
                return None
            user_entry = self.repository.get_entry(
                organization_id=scope.turn.organization_id,
                session_id=scope.session.id,
                entry_id=scope.turn.user_entry_id,
            )
            if (
                user_entry is None
                or user_entry.turn_id != scope.turn.id
                or user_entry.entry_type is not EntryType.USER_TURN
                or user_entry.lifecycle is not EntryLifecycle.PROVISIONAL
            ):
                raise EntryNotFoundError()
            safe_failure_reason = context_attempt.safe_failure_reason
            scope.turn.fail(
                expected_version=command.binding.turn_version,
                safe_reason_code=safe_failure_reason,
                now=now,
            )
            scope.session.release_turn(
                turn_id=scope.turn.id,
                expected_lifecycle_revision=command.binding.lifecycle_revision,
                content_changed=False,
                now=now,
            )
            user_entry.reject(now=now)
            self.repository.save_turn(scope.turn)
            self.repository.save_session(scope.session)
            self.repository.save_entry(user_entry)
            return ConversationExecutionTerminalProjection(
                outcome=context_attempt.status.value,
                safe_failure_reason=safe_failure_reason,
            )

        return self._execute(operation)


class ReadCurrentTurnInputUseCase(_ExecutionUseCase):
    def execute(self, command: ReadCurrentTurnInputCommand) -> CurrentTurnInput:
        def operation() -> CurrentTurnInput:
            resolve_command = resolve_command_for_binding(
                command.binding,
                claim_generation=command.binding.dispatch_claim_generation,
                broker_message_id=command.binding.broker_message_id,
            )
            scope = self.repository.resolve_execution_scope(
                resolve_command,
                for_update=False,
            )
            if scope is None:
                raise AccessGrantNotUsableError()
            require_runtime_binding(
                scope,
                command.binding,
                now=self.repository.current_time(),
            )
            if (
                scope.turn.status is not TurnStatus.RUNNING
                or scope.turn.execution_id != command.execution_id
                or scope.turn.latest_attempt_id != command.attempt_id
            ):
                raise StaleTurnVersionError()
            entry = self.repository.get_entry(
                organization_id=scope.session.organization_id,
                session_id=scope.session.id,
                entry_id=scope.turn.user_entry_id,
            )
            if (
                entry is None
                or entry.turn_id != scope.turn.id
                or entry.entry_type is not EntryType.USER_TURN
                or entry.lifecycle is not EntryLifecycle.PROVISIONAL
                or entry.content is None
                or entry.content.model is None
            ):
                raise EntryNotFoundError()
            return CurrentTurnInput(
                entry_id=entry.id,
                model_content=entry.content.model,
            )

        return self._execute(operation)


def _require_scope(
    scope: ConversationExecutionScope,
    *,
    command: ResolveConversationExecutionCommand,
    now: datetime,
) -> None:
    require_runtime_binding(scope, None, now=now)
    if (
        scope.turn.organization_id != command.organization_id
        or scope.turn.id != command.turn_id
        or scope.turn.dispatch_id != command.dispatch_id
        or scope.dispatch.id != command.dispatch_id
        or scope.dispatch.turn_id != command.turn_id
        or scope.dispatch.claim_generation != command.dispatch_claim_generation
        or scope.dispatch.memory_contract_version
        != command.memory_contract_version
        or scope.dispatch.storage_generation != command.storage_generation
        or scope.dispatch.minimum_worker_capability
        != command.minimum_worker_capability
        or scope.dispatch.status
        not in {
            DispatchStatus.CLAIMED,
            DispatchStatus.PUBLISHED,
            DispatchStatus.ACKNOWLEDGED,
        }
        or (
            scope.dispatch.broker_message_id is not None
            and scope.dispatch.broker_message_id != command.broker_message_id
        )
    ):
        raise DispatchStateConflictError()


def _require_reference_terminal_scope(
    scope: ConversationExecutionScope,
    command: ResolveTerminalConversationExecutionCommand,
    *,
    require_acknowledged: bool = True,
) -> None:
    deployment = scope.deployment
    grant = scope.grant
    session = scope.session
    turn = scope.turn
    dispatch = scope.dispatch
    if (
        deployment.organization_id != command.organization_id
        or session.organization_id != command.organization_id
        or turn.organization_id != command.organization_id
        or dispatch.organization_id != command.organization_id
        or turn.id != command.turn_id
        or turn.dispatch_id != command.dispatch_id
        or dispatch.id != command.dispatch_id
        or dispatch.turn_id != command.turn_id
        or dispatch.session_id != session.id
        or dispatch.claim_generation != command.dispatch_claim_generation
        or (
            dispatch.broker_message_id != command.broker_message_id
            and (
                require_acknowledged
                or dispatch.broker_message_id is not None
            )
        )
        or dispatch.memory_contract_version != command.memory_contract_version
        or dispatch.storage_generation != command.storage_generation
        or dispatch.minimum_worker_capability
        != command.minimum_worker_capability
        or (
            require_acknowledged
            and (
                dispatch.status is not DispatchStatus.ACKNOWLEDGED
                or dispatch.workflow_admission_reference
                != str(command.workflow_admission_id)
            )
        )
        or turn.access_grant_id != grant.id
        or turn.session_id != session.id
        or session.organization_id != deployment.organization_id
        or session.app_id != deployment.app_id
        or session.workflow_id != deployment.workflow_id
        or session.deployment_id != deployment.deployment_id
        or session.deployment_version != deployment.deployment_version
        or session.mapping_version != deployment.mapping_version
        or session.memory_policy_version != deployment.memory_policy_version
        or session.memory_contract_version != deployment.memory_contract_version
        or session.storage_generation != deployment.storage_generation
        or session.audience_kind is not AudienceKind.PUBLIC_CHATBOT
        or grant.organization_id != session.organization_id
        or grant.session_id != session.id
        or grant.deployment_id != session.deployment_id
        or grant.deployment_version != session.deployment_version
        or grant.audience_kind is not AudienceKind.PUBLIC_CHATBOT
    ):
        raise DispatchStateConflictError()


def _reference_scope_runtime_usable(
    scope: ConversationExecutionScope,
    *,
    now: datetime,
) -> bool:
    if not scope.deployment.runtime_contract_ready:
        return False
    try:
        scope.grant.require_active(
            deployment_id=scope.deployment.deployment_id,
            deployment_version=scope.deployment.deployment_version,
            audience_kind=AudienceKind.PUBLIC_CHATBOT,
            now=now,
        )
        scope.session.require_active(
            expected_lifecycle_revision=scope.session.lifecycle_revision,
            now=now,
        )
    except Exception:
        return False
    return True


def _require_reference_execution_identity(
    scope: ConversationExecutionScope,
    *,
    execution_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> None:
    if scope.turn.status is TurnStatus.QUEUED:
        if (
            scope.turn.execution_id is not None
            or scope.turn.latest_attempt_id is not None
        ):
            raise StaleTurnVersionError()
        return
    if (
        scope.turn.status is not TurnStatus.RUNNING
        or scope.turn.execution_id != execution_id
        or scope.turn.latest_attempt_id != attempt_id
    ):
        raise StaleTurnVersionError()


def require_runtime_binding(
    scope: ConversationExecutionScope,
    binding: ResolvedConversationExecution | None,
    *,
    now: datetime,
) -> None:
    deployment = scope.deployment
    grant = scope.grant
    session = scope.session
    turn = scope.turn
    grant.require_active(
        deployment_id=deployment.deployment_id,
        deployment_version=deployment.deployment_version,
        audience_kind=AudienceKind.PUBLIC_CHATBOT,
        now=now,
    )
    if grant.state is not AccessGrantState.ACTIVE:
        raise AccessGrantNotUsableError()
    session.require_active(
        expected_lifecycle_revision=turn.started_lifecycle_revision,
        now=now,
    )
    if (
        not deployment.runtime_contract_ready
        or turn.access_grant_id != grant.id
        or turn.session_id != session.id
        or scope.dispatch.session_id != session.id
        or session.organization_id != deployment.organization_id
        or session.app_id != deployment.app_id
        or session.workflow_id != deployment.workflow_id
        or session.deployment_id != deployment.deployment_id
        or session.deployment_version != deployment.deployment_version
        or session.mapping_version != deployment.mapping_version
        or session.memory_policy_version != deployment.memory_policy_version
        or session.memory_contract_version != deployment.memory_contract_version
        or session.storage_generation != deployment.storage_generation
        or session.audience_kind is not AudienceKind.PUBLIC_CHATBOT
    ):
        raise AccessGrantNotUsableError()
    if binding is not None:
        current = _resolved(scope, broker_message_id=binding.broker_message_id)
        if (
            _immutable_binding_identity(current)
            != _immutable_binding_identity(binding)
            or current.lifecycle_revision != binding.lifecycle_revision
            or current.turn_version != binding.turn_version
        ):
            raise AccessGrantNotUsableError()


def _resolved(
    scope: ConversationExecutionScope,
    *,
    broker_message_id: str | None = None,
) -> ResolvedConversationExecution:
    deployment = scope.deployment
    required = (
        deployment.runtime_start_node_id,
        deployment.runtime_input_variable,
        deployment.runtime_llm_node_id,
        deployment.runtime_answer_node_id,
        deployment.runtime_output_variable,
        deployment.runtime_max_turns,
        deployment.runtime_max_context_tokens,
    )
    if any(value is None for value in required):
        raise AccessGrantNotUsableError()
    return ResolvedConversationExecution(
        organization_id=deployment.organization_id,
        app_id=deployment.app_id,
        workflow_id=deployment.workflow_id,
        deployment_id=deployment.deployment_id,
        deployment_version=deployment.deployment_version,
        session_id=scope.session.id,
        turn_id=scope.turn.id,
        dispatch_id=scope.dispatch.id,
        dispatch_claim_generation=scope.dispatch.claim_generation,
        broker_message_id=str(
            scope.dispatch.broker_message_id or broker_message_id or ""
        ),
        request_fingerprint=scope.turn.request_identity.request_fingerprint,
        lifecycle_revision=scope.session.lifecycle_revision,
        turn_version=scope.turn.version,
        memory_contract_version=deployment.memory_contract_version,
        mapping_version=deployment.mapping_version,
        memory_policy_version=deployment.memory_policy_version,
        storage_generation=deployment.storage_generation,
        minimum_worker_capability=scope.dispatch.minimum_worker_capability,
        start_node_id=str(deployment.runtime_start_node_id),
        input_variable=str(deployment.runtime_input_variable),
        llm_node_id=str(deployment.runtime_llm_node_id),
        answer_node_id=str(deployment.runtime_answer_node_id),
        output_variable=str(deployment.runtime_output_variable),
        max_turns=int(deployment.runtime_max_turns),
        max_context_tokens=int(deployment.runtime_max_context_tokens),
    )


def _require_same_resolved(
    scope: ConversationExecutionScope,
    expected: ResolvedConversationExecution,
) -> None:
    current = _resolved(scope, broker_message_id=expected.broker_message_id)
    if current != expected:
        # Turn version can advance only through the operation currently being
        # observed; at admission it must still match the resolved snapshot.
        raise AccessGrantNotUsableError()


def _require_same_terminal_recovery_identity(
    scope: ConversationExecutionScope,
    expected: ResolvedConversationExecution,
) -> None:
    current = _resolved(scope, broker_message_id=expected.broker_message_id)
    if (
        _immutable_binding_identity(current)
        != _immutable_binding_identity(expected)
        or current.lifecycle_revision != expected.lifecycle_revision
    ):
        raise AccessGrantNotUsableError()


def _terminal_projection(
    scope: ConversationExecutionScope,
    *,
    execution_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> ConversationExecutionTerminalProjection:
    if (
        scope.turn.status is not TurnStatus.FAILED
        or scope.turn.execution_id != execution_id
        or scope.turn.latest_attempt_id != attempt_id
        or scope.turn.assistant_entry_id is not None
        or scope.turn.safe_failure_reason is None
        or scope.session.active_turn_id == scope.turn.id
    ):
        raise StaleTurnVersionError()
    safe_failure_reason = scope.turn.safe_failure_reason
    return ConversationExecutionTerminalProjection(
        outcome=(
            "outcome_unknown"
            if safe_failure_reason == "provider_outcome_unknown"
            else "failed"
        ),
        safe_failure_reason=safe_failure_reason,
    )


def _completed_terminal_projection(
    scope: ConversationExecutionScope,
    *,
    repository,
    execution_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> ConversationExecutionTerminalProjection:
    assistant_entry_id = scope.turn.assistant_entry_id
    if (
        scope.turn.status is not TurnStatus.COMPLETED
        or scope.turn.execution_id != execution_id
        or scope.turn.latest_attempt_id != attempt_id
        or assistant_entry_id is None
        or scope.turn.safe_failure_reason is not None
        or scope.session.active_turn_id == scope.turn.id
    ):
        raise StaleTurnVersionError()
    assistant_entry = repository.get_entry(
        organization_id=scope.turn.organization_id,
        session_id=scope.session.id,
        entry_id=assistant_entry_id,
    )
    if (
        assistant_entry is None
        or assistant_entry.turn_id != scope.turn.id
        or assistant_entry.entry_type is not EntryType.ASSISTANT_TURN
        or assistant_entry.lifecycle is not EntryLifecycle.APPROVED
        or assistant_entry.content is None
    ):
        raise EntryNotFoundError()
    return ConversationExecutionTerminalProjection(
        outcome="completed",
        safe_failure_reason=None,
        result_entry_id=assistant_entry.id,
        result_digest=_protected_entry_identity_digest(assistant_entry),
    )


def _protected_entry_identity_digest(entry: ConversationMemoryEntry) -> str:
    content = entry.content
    if content is None:
        raise EntryNotFoundError()
    identities = []
    for projection in (content.display, content.model):
        identities.append(
            "-"
            if projection is None
            else ":".join(
                (
                    projection.format_version,
                    projection.content_digest,
                    str(projection.plaintext_byte_length),
                )
            )
        )
    return hashlib.sha256("|".join(identities).encode("utf-8")).hexdigest()


def _node_invocation_id(
    binding: ResolvedConversationExecution,
) -> uuid.UUID:
    execution_id = uuid.uuid5(
        uuid.uuid5(
            binding.dispatch_id,
            "conversation-workflow-admission-v1",
        ),
        "conversation-execution-v1",
    )
    return uuid.uuid5(
        execution_id,
        f"conversation-node:{binding.llm_node_id}",
    )


def _provider_attempt_id(
    binding: ResolvedConversationExecution,
) -> uuid.UUID:
    execution_id = uuid.uuid5(
        uuid.uuid5(
            binding.dispatch_id,
            "conversation-workflow-admission-v1",
        ),
        "conversation-execution-v1",
    )
    return uuid.uuid5(
        execution_id,
        f"provider_execution:{_node_invocation_id(binding)}:main_generation",
    )


def _immutable_binding_identity(
    binding: ResolvedConversationExecution,
) -> tuple[object, ...]:
    return (
        binding.organization_id,
        binding.app_id,
        binding.workflow_id,
        binding.deployment_id,
        binding.deployment_version,
        binding.session_id,
        binding.turn_id,
        binding.dispatch_id,
        binding.dispatch_claim_generation,
        binding.broker_message_id,
        binding.request_fingerprint,
        binding.memory_contract_version,
        binding.mapping_version,
        binding.memory_policy_version,
        binding.storage_generation,
        binding.minimum_worker_capability,
        binding.start_node_id,
        binding.input_variable,
        binding.llm_node_id,
        binding.answer_node_id,
        binding.output_variable,
        binding.max_turns,
        binding.max_context_tokens,
    )


def resolve_command_for_binding(
    binding: ResolvedConversationExecution,
    *,
    claim_generation: int | None = None,
    broker_message_id: str | None = None,
) -> ResolveConversationExecutionCommand:
    return ResolveConversationExecutionCommand(
        organization_id=binding.organization_id,
        dispatch_id=binding.dispatch_id,
        turn_id=binding.turn_id,
        dispatch_claim_generation=(
            binding.dispatch_claim_generation
            if claim_generation is None
            else claim_generation
        ),
        broker_message_id=(
            binding.broker_message_id
            if broker_message_id is None
            else broker_message_id
        ),
        memory_contract_version=binding.memory_contract_version,
        storage_generation=binding.storage_generation,
        minimum_worker_capability=binding.minimum_worker_capability,
    )


__all__ = [
    "ConversationExecutionMemoryRepositoryPort",
    "ConversationExecutionObservation",
    "ConversationExecutionScope",
    "ConversationExecutionTerminalProjection",
    "CurrentTurnInput",
    "FinalizeReferenceConversationExecutionCommand",
    "FinalizeReferenceConversationExecutionUseCase",
    "ObserveConversationExecutionAdmittedCommand",
    "ObserveConversationExecutionAdmittedUseCase",
    "ObserveConversationExecutionRunningCommand",
    "ObserveConversationExecutionRunningUseCase",
    "ReadCurrentTurnInputCommand",
    "ReadCurrentTurnInputUseCase",
    "RecoverConversationExecutionTerminalCommand",
    "RecoverConversationExecutionTerminalUseCase",
    "ResolveConversationExecutionCommand",
    "ResolveConversationExecutionUseCase",
    "ResolvedConversationExecution",
    "ResolvedTerminalConversationExecution",
    "ResolveTerminalConversationExecutionCommand",
    "ResolveTerminalConversationExecutionUseCase",
    "require_runtime_binding",
    "resolve_command_for_binding",
]
