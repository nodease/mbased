"""Production adapters for the public Conversation Memory worker path."""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.memory.adapters.persistence.repository import (
    SqlAlchemyConversationMemoryRepository,
    SqlAlchemyMemoryUnitOfWork,
)
from apps.memory.application.content import memory_content_aad
from apps.memory.application.context import (
    BuildMemoryContextCommand,
    BuildMemoryContextUseCase,
    ClaimMemoryContextCommand,
    ClaimMemoryContextUseCase,
    ContextAttemptState,
    FinishContextProviderAttemptCommand,
    FinishContextProviderAttemptUseCase,
    MarkContextProviderStartedCommand,
    MarkContextProviderStartedUseCase,
)
from apps.memory.application.execution import (
    ConversationExecutionTerminalProjection,
    FinalizeReferenceConversationExecutionCommand,
    FinalizeReferenceConversationExecutionUseCase,
    ObserveConversationExecutionAdmittedCommand,
    ObserveConversationExecutionAdmittedUseCase,
    ObserveConversationExecutionRunningCommand,
    ObserveConversationExecutionRunningUseCase,
    ReadCurrentTurnInputCommand,
    ReadCurrentTurnInputUseCase,
    RecoverConversationExecutionTerminalCommand,
    RecoverConversationExecutionTerminalUseCase,
    ResolveConversationExecutionCommand,
    ResolveConversationExecutionUseCase,
    ResolveTerminalConversationExecutionCommand,
    ResolveTerminalConversationExecutionUseCase,
    ResolvedConversationExecution,
)
from apps.memory.application.lifecycle import (
    CheckpointAssistantResultCommand,
    CheckpointAssistantResultUseCase,
    CompleteTurnCommand,
    CompleteTurnUseCase,
    RecoverAssistantCheckpointCommand,
    RecoverAssistantCheckpointUseCase,
)
from apps.memory.domain.conversation import ProtectedEntryContent
from apps.shared.db.models.app import App
from apps.shared.db.models.workflow_deployment import (
    DeploymentType,
    WorkflowDeployment,
)
from apps.shared.domain.conversation_memory_task import ConversationTurnTaskEnvelope
from apps.workflow_engine.adapters.conversation_memory_admission_repository import (
    SqlAlchemyConversationExecutionAdmissionRepository,
    SqlAlchemyConversationExecutionUnitOfWork,
)
from apps.workflow_engine.application.conversation_memory_admission import (
    AdmitConversationExecutionCommand,
    AdmitConversationExecutionUseCase,
    ClaimConversationExecutionCommand,
    ClaimConversationExecutionUseCase,
    ConversationExecutionConflictError,
    FinishConversationExecutionCommand,
    FinishConversationExecutionUseCase,
)
from apps.workflow_engine.application.conversation_memory_execution import (
    ConversationExecutionBinding,
    ConversationExecutionGraph,
    ConversationExecutionRuntimeError,
    ConversationMemoryCheckpoint,
    ConversationMemoryContextBuild,
    ConversationMemoryContextClaim,
    ConversationMemoryTerminalProjection,
    ConversationMemoryTerminalRecovery,
)

_LOGGER = logging.getLogger(__name__)


def conversation_execution_attempt_id(execution_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(execution_id, "conversation-execution-attempt-v1")


def conversation_node_invocation_id(
    execution_id: uuid.UUID,
    llm_node_id: str,
) -> uuid.UUID:
    return uuid.uuid5(execution_id, f"conversation-node:{llm_node_id}")


def conversation_provider_attempt_id(
    execution_id: uuid.UUID,
    llm_node_id: str,
) -> uuid.UUID:
    node_invocation_id = conversation_node_invocation_id(
        execution_id,
        llm_node_id,
    )
    return uuid.uuid5(
        execution_id,
        f"provider_execution:{node_invocation_id}:main_generation",
    )


def conversation_assistant_entry_id(execution_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(
        execution_id,
        "conversation-assistant-checkpoint-v1",
    )


class SqlAlchemyConversationMemoryRuntimeAdapter:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        content_cipher: Any,
        token_counter: Any,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._content_cipher = content_cipher
        self._token_counter = token_counter
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def resolve(
        self,
        envelope: ConversationTurnTaskEnvelope,
    ) -> ConversationExecutionBinding:
        result = self._execute(
            lambda repository, uow: ResolveConversationExecutionUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                ResolveConversationExecutionCommand(
                    organization_id=envelope.organization_id,
                    dispatch_id=envelope.dispatch_id,
                    dispatch_claim_generation=envelope.claim_generation,
                    broker_message_id=envelope.broker_message_id,
                    turn_id=envelope.turn_id,
                    memory_contract_version=envelope.memory_contract_version,
                    storage_generation=envelope.storage_generation,
                    minimum_worker_capability=(
                        envelope.minimum_worker_capability
                    ),
                )
            )
        )
        return _workflow_binding(result)

    def resolve_terminal(
        self,
        envelope: ConversationTurnTaskEnvelope,
        **kwargs,
    ) -> ConversationMemoryTerminalRecovery | None:
        result = self._execute(
            lambda repository, uow: ResolveTerminalConversationExecutionUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                ResolveTerminalConversationExecutionCommand(
                    organization_id=envelope.organization_id,
                    dispatch_id=envelope.dispatch_id,
                    dispatch_claim_generation=envelope.claim_generation,
                    broker_message_id=envelope.broker_message_id,
                    turn_id=envelope.turn_id,
                    memory_contract_version=envelope.memory_contract_version,
                    storage_generation=envelope.storage_generation,
                    minimum_worker_capability=(
                        envelope.minimum_worker_capability
                    ),
                    workflow_admission_id=kwargs["admission_id"],
                    execution_id=kwargs["execution_id"],
                    attempt_id=kwargs["attempt_id"],
                )
            )
        )
        if result is None:
            return None
        return ConversationMemoryTerminalRecovery(
            binding=_workflow_binding(result.binding),
            projection=ConversationMemoryTerminalProjection(
                outcome=result.projection.outcome,
                safe_failure_reason=result.projection.safe_failure_reason,
                result_entry_id=result.projection.result_entry_id,
                result_digest=result.projection.result_digest,
                provider_attempt_id=result.projection.provider_attempt_id,
                usage_reference=result.projection.usage_reference,
            ),
            requires_memory_failure=result.requires_memory_failure,
            requires_dispatch_acknowledgement=(
                result.requires_dispatch_acknowledgement
            ),
        )

    def finalize_reference_failure(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationMemoryTerminalProjection:
        safe_failure_reason = kwargs["safe_failure_reason"]
        if not isinstance(safe_failure_reason, str):
            raise ConversationExecutionRuntimeError(
                "memory.terminal_projection_invalid"
            )
        result = self._execute(
            lambda repository, uow: FinalizeReferenceConversationExecutionUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                FinalizeReferenceConversationExecutionCommand(
                    binding=_memory_binding(binding),
                    workflow_admission_id=kwargs["admission_id"],
                    execution_id=kwargs["execution_id"],
                    attempt_id=kwargs["attempt_id"],
                    safe_failure_reason=safe_failure_reason,
                    acknowledge_dispatch=kwargs.get(
                        "acknowledge_dispatch",
                        False,
                    ),
                    provider_attempt_id=kwargs.get("provider_attempt_id"),
                    context_outcome=kwargs.get(
                        "context_outcome",
                        "failed",
                    ),
                    execution_outcome=kwargs.get(
                        "execution_outcome",
                        "failed",
                    ),
                )
            )
        )
        return ConversationMemoryTerminalProjection(
            outcome=result.outcome,
            safe_failure_reason=result.safe_failure_reason,
            result_entry_id=result.result_entry_id,
            result_digest=result.result_digest,
            provider_attempt_id=result.provider_attempt_id,
            usage_reference=result.usage_reference,
        )

    def observe_admitted(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationExecutionBinding:
        result = self._execute(
            lambda repository, uow: ObserveConversationExecutionAdmittedUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                ObserveConversationExecutionAdmittedCommand(
                    binding=_memory_binding(binding),
                    workflow_admission_id=kwargs["admission_id"],
                    claim_generation=kwargs["claim_generation"],
                    broker_message_id=kwargs["broker_message_id"],
                )
            )
        )
        return replace(
            binding,
            lifecycle_revision=result.lifecycle_revision,
            turn_version=result.turn_version,
        )

    def observe_running(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationExecutionBinding:
        result = self._execute(
            lambda repository, uow: ObserveConversationExecutionRunningUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                ObserveConversationExecutionRunningCommand(
                    binding=_memory_binding(binding),
                    workflow_admission_id=kwargs["admission_id"],
                    execution_id=kwargs["execution_id"],
                    attempt_id=kwargs["attempt_id"],
                )
            )
        )
        return replace(
            binding,
            lifecycle_revision=result.lifecycle_revision,
            turn_version=result.turn_version,
        )

    def read_current_input(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> str:
        result = self._execute(
            lambda repository, uow: ReadCurrentTurnInputUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                ReadCurrentTurnInputCommand(
                    binding=_memory_binding(binding),
                    execution_id=kwargs["execution_id"],
                    attempt_id=kwargs["attempt_id"],
                )
            )
        )
        value = self._content_cipher.reveal(
            result.model_content,
            associated_data=memory_content_aad(
                organization_id=binding.organization_id,
                session_id=binding.session_id,
                turn_id=binding.turn_id,
                entry_id=result.entry_id,
                projection="model",
            ),
        )
        if not isinstance(value, str) or not value:
            raise ConversationExecutionRuntimeError(
                "memory.current_input_unavailable"
            )
        return value

    def build_context(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationMemoryContextBuild:
        result = self._execute(
            lambda repository, uow: BuildMemoryContextUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                BuildMemoryContextCommand(
                    binding=_memory_binding(binding),
                    node_invocation_id=kwargs["node_invocation_id"],
                    provider_capability_reference=kwargs[
                        "capability_reference"
                    ],
                    provider_capability_revision=kwargs[
                        "capability_revision"
                    ],
                    provider_attempt_id=kwargs["provider_attempt_id"],
                    expires_at=kwargs["expires_at"],
                    now=self._now(),
                )
            )
        )
        return ConversationMemoryContextBuild(
            plan_id=result.plan_id,
            lease_id=result.lease_id,
        )

    def claim_context(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationMemoryContextClaim:
        result = self._execute(
            lambda repository, uow: ClaimMemoryContextUseCase(
                repository=repository,
                uow=uow,
                content_cipher=self._content_cipher,
                token_counter=self._token_counter,
            ).execute(
                ClaimMemoryContextCommand(
                    binding=_memory_binding(binding),
                    plan_id=kwargs["plan_id"],
                    lease_id=kwargs["lease_id"],
                    node_invocation_id=kwargs["node_invocation_id"],
                    provider_capability_reference=kwargs[
                        "capability_reference"
                    ],
                    provider_capability_revision=kwargs[
                        "capability_revision"
                    ],
                    provider_attempt_id=kwargs["provider_attempt_id"],
                    claim_deadline_at=kwargs["claim_deadline_at"],
                    now=self._now(),
                )
            )
        )
        return ConversationMemoryContextClaim(
            attempt_id=result.attempt_id,
            attempt_version=result.attempt_version,
            history_block=result.history_block,
        )

    def validate_current(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> None:
        self._execute(
            lambda repository, uow: ReadCurrentTurnInputUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                ReadCurrentTurnInputCommand(
                    binding=_memory_binding(binding),
                    execution_id=kwargs["execution_id"],
                    attempt_id=kwargs["attempt_id"],
                )
            )
        )

    def mark_provider_started(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> int:
        return self._execute(
            lambda repository, uow: MarkContextProviderStartedUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                MarkContextProviderStartedCommand(
                    organization_id=binding.organization_id,
                    attempt_id=kwargs["context_attempt_id"],
                    expected_version=kwargs["expected_version"],
                    usage_reference=kwargs["usage_reference"],
                    now=self._now(),
                )
            )
        )

    def finish_context_attempt(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> None:
        outcome = ContextAttemptState(kwargs["outcome"])
        self._execute(
            lambda repository, uow: FinishContextProviderAttemptUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                FinishContextProviderAttemptCommand(
                    organization_id=binding.organization_id,
                    attempt_id=kwargs["context_attempt_id"],
                    expected_version=kwargs["expected_version"],
                    outcome=outcome,
                    safe_failure_reason=kwargs["safe_failure_reason"],
                    now=self._now(),
                )
            )
        )

    def checkpoint(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationMemoryCheckpoint:
        execution_id = kwargs["execution_id"]
        assistant_entry_id = conversation_assistant_entry_id(execution_id)
        assistant_content = self._protect_assistant(
            binding,
            entry_id=assistant_entry_id,
            value=kwargs["assistant_text"],
        )
        result = self._execute(
            lambda repository, uow: CheckpointAssistantResultUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                CheckpointAssistantResultCommand(
                    organization_id=binding.organization_id,
                    session_id=binding.session_id,
                    turn_id=binding.turn_id,
                    expected_lifecycle_revision=binding.lifecycle_revision,
                    expected_turn_version=binding.turn_version,
                    execution_id=execution_id,
                    attempt_id=kwargs["attempt_id"],
                    assistant_entry_id=assistant_entry_id,
                    assistant_content=assistant_content,
                    now=self._now(),
                )
            )
        )
        return ConversationMemoryCheckpoint(
            entry_id=result.assistant_entry_id,
            content_digest=result.content_digest,
            context_attempt_id=kwargs["context_attempt_id"],
            context_attempt_version=kwargs["context_attempt_version"],
            usage_reference=kwargs["usage_reference"],
            lifecycle_revision=binding.lifecycle_revision,
            turn_version=binding.turn_version,
            state=assistant_content,
        )

    def recover_checkpoint(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationMemoryCheckpoint | None:
        execution_id = kwargs["execution_id"]
        provider_attempt_id = conversation_provider_attempt_id(
            execution_id,
            binding.llm_node_id,
        )
        result = self._execute(
            lambda repository, uow: RecoverAssistantCheckpointUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                RecoverAssistantCheckpointCommand(
                    organization_id=binding.organization_id,
                    session_id=binding.session_id,
                    turn_id=binding.turn_id,
                    execution_id=execution_id,
                    attempt_id=conversation_execution_attempt_id(execution_id),
                    provider_attempt_id=provider_attempt_id,
                    assistant_entry_id=conversation_assistant_entry_id(
                        execution_id
                    ),
                    now=self._now(),
                )
            )
        )
        if result is None:
            return None
        return ConversationMemoryCheckpoint(
            entry_id=result.assistant_entry_id,
            content_digest=result.content_digest,
            context_attempt_id=result.context_attempt_id,
            context_attempt_version=result.context_attempt_version,
            usage_reference=result.usage_reference,
            lifecycle_revision=result.expected_lifecycle_revision,
            turn_version=result.expected_turn_version,
            state=result.assistant_content,
            context_attempt_outcome=result.context_attempt_outcome,
        )

    def recover_terminal(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationMemoryTerminalProjection | None:
        result = self._execute(
            lambda repository, uow: RecoverConversationExecutionTerminalUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                RecoverConversationExecutionTerminalCommand(
                    binding=_memory_binding(binding),
                    workflow_admission_id=kwargs["admission_id"],
                    execution_id=kwargs["execution_id"],
                    attempt_id=kwargs["attempt_id"],
                    node_invocation_id=kwargs["node_invocation_id"],
                    provider_attempt_id=kwargs["provider_attempt_id"],
                )
            )
        )
        if result is None:
            return None
        if not isinstance(result, ConversationExecutionTerminalProjection):
            raise ConversationExecutionRuntimeError(
                "memory.terminal_projection_invalid"
            )
        return ConversationMemoryTerminalProjection(
            outcome=result.outcome,
            safe_failure_reason=result.safe_failure_reason,
            result_entry_id=result.result_entry_id,
            result_digest=result.result_digest,
            provider_attempt_id=result.provider_attempt_id,
            usage_reference=result.usage_reference,
        )

    def complete(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> None:
        checkpoint = kwargs["checkpoint"]
        if not isinstance(checkpoint.state, ProtectedEntryContent):
            raise ConversationExecutionRuntimeError(
                "memory.checkpoint_invalid"
            )
        self._execute(
            lambda repository, uow: CompleteTurnUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                CompleteTurnCommand(
                    organization_id=binding.organization_id,
                    session_id=binding.session_id,
                    turn_id=binding.turn_id,
                    expected_lifecycle_revision=(
                        checkpoint.lifecycle_revision
                    ),
                    expected_turn_version=checkpoint.turn_version,
                    outcome="completed",
                    assistant_entry_id=checkpoint.entry_id,
                    assistant_content=checkpoint.state,
                    safe_failure_reason=None,
                    now=self._now(),
                )
            )
        )

    def fail(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> None:
        self._execute(
            lambda repository, uow: CompleteTurnUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                CompleteTurnCommand(
                    organization_id=binding.organization_id,
                    session_id=binding.session_id,
                    turn_id=binding.turn_id,
                    expected_lifecycle_revision=binding.lifecycle_revision,
                    expected_turn_version=binding.turn_version,
                    outcome="failed",
                    assistant_entry_id=None,
                    assistant_content=None,
                    safe_failure_reason=kwargs["safe_reason_code"],
                    now=self._now(),
                )
            )
        )

    def _protect_assistant(
        self,
        binding: ConversationExecutionBinding,
        *,
        entry_id: uuid.UUID,
        value: str,
    ) -> ProtectedEntryContent:
        common = {
            "organization_id": binding.organization_id,
            "session_id": binding.session_id,
            "turn_id": binding.turn_id,
            "entry_id": entry_id,
        }
        return ProtectedEntryContent(
            display=self._content_cipher.protect(
                value,
                associated_data=memory_content_aad(
                    **common,
                    projection="display",
                ),
            ),
            model=self._content_cipher.protect(
                value,
                associated_data=memory_content_aad(
                    **common,
                    projection="model",
                ),
            ),
        )

    def _execute(self, operation):
        session = self._session_factory()
        try:
            repository = SqlAlchemyConversationMemoryRepository(session)
            return operation(
                repository,
                SqlAlchemyMemoryUnitOfWork(session),
            )
        finally:
            session.close()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("conversation runtime clock must be timezone-aware")
        return value


class SqlAlchemyConversationExecutionAdmissionAdapter:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
    ) -> None:
        self._session_factory = session_factory

    def admit(self, binding: ConversationExecutionBinding, *, now: datetime):
        admission_id = uuid.uuid5(
            binding.dispatch_id,
            "conversation-workflow-admission-v1",
        )
        return self._execute(
            lambda repository, uow: AdmitConversationExecutionUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                AdmitConversationExecutionCommand(
                    admission_id=admission_id,
                    organization_id=binding.organization_id,
                    dispatch_id=binding.dispatch_id,
                    session_id=binding.session_id,
                    turn_id=binding.turn_id,
                    workflow_id=binding.workflow_id,
                    app_id=binding.app_id,
                    deployment_id=binding.deployment_id,
                    deployment_version=binding.deployment_version,
                    request_fingerprint=binding.request_fingerprint,
                    memory_contract_version=binding.memory_contract_version,
                    mapping_version=binding.mapping_version,
                    memory_policy_version=binding.memory_policy_version,
                    storage_generation=binding.storage_generation,
                    minimum_worker_capability=(
                        binding.minimum_worker_capability
                    ),
                    now=now,
                )
            )
        )

    def claim(self, binding: ConversationExecutionBinding, **kwargs):
        return self._execute(
            lambda repository, uow: ClaimConversationExecutionUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                ClaimConversationExecutionCommand(
                    organization_id=binding.organization_id,
                    dispatch_id=binding.dispatch_id,
                    owner=kwargs["owner"],
                    attempt_id=kwargs["attempt_id"],
                    lease_deadline=kwargs["lease_deadline"],
                    now=kwargs["now"],
                )
            )
        )

    def require_fence(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> None:
        session = self._session_factory()
        transaction = session.begin()
        try:
            repository = SqlAlchemyConversationExecutionAdmissionRepository(
                session
            )
            admission = repository.lock_by_dispatch(
                organization_id=binding.organization_id,
                dispatch_id=binding.dispatch_id,
            )
            if admission is None:
                raise ConversationExecutionConflictError()
            admission.require_fence(
                owner=kwargs["owner"],
                lease_generation=kwargs["lease_generation"],
                now=kwargs["now"],
            )
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise
        finally:
            session.close()

    def finish(self, binding: ConversationExecutionBinding, **kwargs) -> None:
        self._execute(
            lambda repository, uow: FinishConversationExecutionUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                FinishConversationExecutionCommand(
                    organization_id=binding.organization_id,
                    dispatch_id=binding.dispatch_id,
                    owner=kwargs["owner"],
                    lease_generation=kwargs["lease_generation"],
                    outcome=kwargs["outcome"],
                    result_entry_id=kwargs["result_entry_id"],
                    result_digest=kwargs["result_digest"],
                    safe_failure_reason=kwargs["safe_failure_reason"],
                    now=kwargs["now"],
                )
            )
        )

    def _execute(self, operation):
        session = self._session_factory()
        try:
            repository = SqlAlchemyConversationExecutionAdmissionRepository(
                session
            )
            return operation(
                repository,
                SqlAlchemyConversationExecutionUnitOfWork(session),
            )
        finally:
            session.close()


class SqlAlchemyConversationExecutionGraphAdapter:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
    ) -> None:
        self._session_factory = session_factory

    def load(
        self,
        binding: ConversationExecutionBinding,
    ) -> ConversationExecutionGraph:
        session = self._session_factory()
        try:
            row = session.execute(
                select(WorkflowDeployment, App)
                .join(App, App.id == WorkflowDeployment.app_id)
                .where(
                    WorkflowDeployment.id == binding.deployment_id,
                    WorkflowDeployment.app_id == binding.app_id,
                    WorkflowDeployment.version == binding.deployment_version,
                    WorkflowDeployment.type == DeploymentType.CHATBOT,
                    App.id == binding.app_id,
                    App.organization_id == binding.organization_id,
                    App.workflow_id == binding.workflow_id,
                )
            ).one_or_none()
            if row is None:
                raise ConversationExecutionRuntimeError(
                    "memory.runtime_binding_stale"
                )
            deployment, _app = row
            if not isinstance(deployment.graph_snapshot, dict):
                raise ConversationExecutionRuntimeError(
                    "memory.graph_unsupported"
                )
            config = deployment.config or {}
            if not isinstance(config, dict):
                raise ConversationExecutionRuntimeError(
                    "memory.graph_unsupported"
                )
            return ConversationExecutionGraph(
                graph=dict(deployment.graph_snapshot),
                deployment_config=dict(config),
            )
        finally:
            session.close()


class LoggingConversationObserver:
    def record(self, **kwargs) -> None:
        _LOGGER.info(
            "Conversation execution event=%s organization_id=%s "
            "admission_id=%s turn_id=%s",
            kwargs["event"],
            kwargs["organization_id"],
            kwargs["admission_id"],
            kwargs["turn_id"],
        )


def _memory_binding(
    binding: ConversationExecutionBinding,
) -> ResolvedConversationExecution:
    return ResolvedConversationExecution(**asdict(binding))


def _workflow_binding(
    binding: ResolvedConversationExecution,
) -> ConversationExecutionBinding:
    return ConversationExecutionBinding(**asdict(binding))


__all__ = [
    "LoggingConversationObserver",
    "SqlAlchemyConversationExecutionAdmissionAdapter",
    "SqlAlchemyConversationExecutionGraphAdapter",
    "SqlAlchemyConversationMemoryRuntimeAdapter",
    "conversation_assistant_entry_id",
    "conversation_execution_attempt_id",
    "conversation_node_invocation_id",
    "conversation_provider_attempt_id",
]
