"""Application orchestration for the initial public Conversation runtime.

The module owns ordering only.  Memory persistence, Workflow admission,
provider capability/usage and observer projection stay behind narrow ports.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Literal, Mapping, Protocol

from apps.shared.domain.conversation_memory_runtime import (
    ConversationMemoryRuntimeContract,
    ConversationMemoryRuntimeContractError,
    validate_conversation_memory_runtime,
)
from apps.shared.domain.conversation_memory_task import ConversationTurnTaskEnvelope
from apps.shared.utils.prompt_injection_guard import (
    PLATFORM_UNTRUSTED_CONTEXT_GUARDRAIL_PROMPT,
)
from apps.workflow_engine.application.conversation_memory_admission import (
    ConversationExecutionFenceError,
    ConversationExecutionState,
)
from apps.workflow_engine.application.provider_execution import (
    LLMCredentialNotAvailableError,
    ProviderExecutionConfigurationError,
    ProviderInvocationOutcomeUnknownError,
    ProviderStartCommitRetryableError,
)

_TEMPLATE_VARIABLE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


class ConversationExecutionRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ConversationExecutionBinding:
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
class ConversationExecutionGraph:
    graph: Mapping[str, Any]
    deployment_config: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ConversationMemoryContextBuild:
    plan_id: uuid.UUID
    lease_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ConversationMemoryContextClaim:
    attempt_id: uuid.UUID
    attempt_version: int
    history_block: str


@dataclass(frozen=True, slots=True)
class ConversationProviderPreparation:
    capability_reference: str
    capability_revision: str
    provider_attempt_id: uuid.UUID
    expires_at: datetime
    state: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ConversationProviderResult:
    text: str
    usage: Mapping[str, Any]
    state: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ConversationMemoryCheckpoint:
    entry_id: uuid.UUID
    content_digest: str
    context_attempt_id: uuid.UUID
    context_attempt_version: int
    usage_reference: str
    lifecycle_revision: int
    turn_version: int
    state: object = field(repr=False, compare=False)
    context_attempt_outcome: str = "provider_started"


@dataclass(frozen=True, slots=True)
class ConversationMemoryTerminalProjection:
    outcome: Literal["completed", "failed", "outcome_unknown"]
    safe_failure_reason: str | None
    result_entry_id: uuid.UUID | None = None
    result_digest: str | None = None
    provider_attempt_id: uuid.UUID | None = None
    usage_reference: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationMemoryTerminalRecovery:
    binding: ConversationExecutionBinding
    projection: ConversationMemoryTerminalProjection
    requires_memory_failure: bool = False
    requires_dispatch_acknowledgement: bool = False


@dataclass(frozen=True, slots=True)
class ExecuteConversationTurnCommand:
    envelope: ConversationTurnTaskEnvelope
    worker_owner: str
    delivery_attempt_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ExecuteConversationTurnResult:
    admission_id: uuid.UUID
    execution_id: uuid.UUID
    turn_id: uuid.UUID
    state: ConversationExecutionState


class ConversationMemoryRuntimePort(Protocol):
    def resolve(self, envelope: ConversationTurnTaskEnvelope) -> ConversationExecutionBinding: ...

    def resolve_terminal(
        self,
        envelope: ConversationTurnTaskEnvelope,
        **kwargs,
    ) -> ConversationMemoryTerminalRecovery | None: ...

    def finalize_reference_failure(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationMemoryTerminalProjection: ...

    def observe_admitted(self, binding: ConversationExecutionBinding, **kwargs) -> ConversationExecutionBinding: ...

    def observe_running(self, binding: ConversationExecutionBinding, **kwargs) -> ConversationExecutionBinding: ...

    def read_current_input(self, binding: ConversationExecutionBinding, **kwargs) -> str: ...

    def build_context(self, binding: ConversationExecutionBinding, **kwargs) -> ConversationMemoryContextBuild: ...

    def claim_context(self, binding: ConversationExecutionBinding, **kwargs) -> ConversationMemoryContextClaim: ...

    def validate_current(self, binding: ConversationExecutionBinding, **kwargs) -> None: ...

    def recover_checkpoint(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationMemoryCheckpoint | None: ...

    def recover_terminal(
        self,
        binding: ConversationExecutionBinding,
        **kwargs,
    ) -> ConversationMemoryTerminalProjection | None: ...

    def mark_provider_started(self, binding: ConversationExecutionBinding, **kwargs) -> int: ...

    def finish_context_attempt(self, binding: ConversationExecutionBinding, **kwargs) -> None: ...

    def checkpoint(self, binding: ConversationExecutionBinding, **kwargs) -> ConversationMemoryCheckpoint: ...

    def complete(self, binding: ConversationExecutionBinding, **kwargs) -> None: ...

    def fail(self, binding: ConversationExecutionBinding, **kwargs) -> None: ...


class ConversationExecutionAdmissionPort(Protocol):
    def admit(self, binding: ConversationExecutionBinding, **kwargs): ...

    def claim(self, binding: ConversationExecutionBinding, **kwargs): ...

    def require_fence(self, binding: ConversationExecutionBinding, **kwargs) -> None: ...

    def finish(self, binding: ConversationExecutionBinding, **kwargs) -> None: ...


class ConversationExecutionGraphPort(Protocol):
    def load(self, binding: ConversationExecutionBinding) -> ConversationExecutionGraph: ...


class ConversationProviderPort(Protocol):
    def prepare(self, **kwargs) -> ConversationProviderPreparation: ...

    def generate(
        self,
        *,
        messages: tuple[Mapping[str, str], ...],
        before_provider_start: Callable[[str], None],
        **kwargs,
    ) -> ConversationProviderResult: ...

    def record_success(self, result: ConversationProviderResult) -> None: ...

    def resume_checkpoint(
        self,
        checkpoint: ConversationMemoryCheckpoint,
        **kwargs,
    ) -> None: ...

    def reconcile_reference_terminal(
        self,
        *,
        organization_id: uuid.UUID,
        provider_attempt_id: uuid.UUID,
        usage_reference: str | None,
    ) -> str: ...


class ConversationObserverPort(Protocol):
    def record(self, **kwargs) -> None: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class ExecuteConversationTurnUseCase:
    def __init__(
        self,
        *,
        memory: ConversationMemoryRuntimePort,
        admissions: ConversationExecutionAdmissionPort,
        graphs: ConversationExecutionGraphPort,
        provider: ConversationProviderPort,
        observer: ConversationObserverPort,
        clock: ClockPort,
        worker_capability: str,
        lease_duration: timedelta,
        context_lease_duration: timedelta,
    ) -> None:
        self.memory = memory
        self.admissions = admissions
        self.graphs = graphs
        self.provider = provider
        self.observer = observer
        self.clock = clock
        self.worker_capability = worker_capability
        self.lease_duration = lease_duration
        self.context_lease_duration = context_lease_duration

    def execute(
        self,
        command: ExecuteConversationTurnCommand,
    ) -> ExecuteConversationTurnResult:
        envelope = command.envelope
        if envelope.minimum_worker_capability != self.worker_capability:
            raise ConversationExecutionRuntimeError("memory.worker_incompatible")
        deterministic_admission_id = uuid.uuid5(
            envelope.dispatch_id,
            "conversation-workflow-admission-v1",
        )
        deterministic_execution_id = uuid.uuid5(
            deterministic_admission_id,
            "conversation-execution-v1",
        )
        stable_attempt_id = uuid.uuid5(
            deterministic_execution_id,
            "conversation-execution-attempt-v1",
        )
        terminal_recovery = self.memory.resolve_terminal(
            envelope,
            admission_id=deterministic_admission_id,
            execution_id=deterministic_execution_id,
            attempt_id=stable_attempt_id,
        )
        if terminal_recovery is not None:
            binding = terminal_recovery.binding
            self._require_envelope_binding(envelope, binding)
            admitted = self.admissions.admit(binding, now=self.clock.now())
            if (
                admitted.admission_id != deterministic_admission_id
                or admitted.execution_id != deterministic_execution_id
            ):
                raise ConversationExecutionRuntimeError(
                    "memory.execution_binding_mismatch"
                )
            if admitted.state in {
                ConversationExecutionState.COMPLETED,
                ConversationExecutionState.FAILED,
                ConversationExecutionState.OUTCOME_UNKNOWN,
            }:
                return self._return_terminal_admission(binding, admitted)
            now = self.clock.now()
            claimed = self.admissions.claim(
                binding,
                owner=command.worker_owner,
                attempt_id=stable_attempt_id,
                lease_deadline=now + self.lease_duration,
                now=now,
            )
            projection = terminal_recovery.projection
            if terminal_recovery.requires_memory_failure:
                context_outcome = "failed"
                if projection.provider_attempt_id is not None:
                    usage_state = self.provider.reconcile_reference_terminal(
                        organization_id=binding.organization_id,
                        provider_attempt_id=projection.provider_attempt_id,
                        usage_reference=projection.usage_reference,
                    )
                    if usage_state == "outcome_unknown":
                        projection = ConversationMemoryTerminalProjection(
                            outcome="outcome_unknown",
                            safe_failure_reason="provider_outcome_unknown",
                            provider_attempt_id=(
                                projection.provider_attempt_id
                            ),
                            usage_reference=projection.usage_reference,
                        )
                        context_outcome = "outcome_unknown"
                    elif usage_state == "succeeded":
                        context_outcome = "succeeded"
                projection = self.memory.finalize_reference_failure(
                    binding,
                    admission_id=admitted.admission_id,
                    execution_id=admitted.execution_id,
                    attempt_id=stable_attempt_id,
                    safe_failure_reason=projection.safe_failure_reason,
                    acknowledge_dispatch=(
                        terminal_recovery.requires_dispatch_acknowledgement
                    ),
                    provider_attempt_id=projection.provider_attempt_id,
                    context_outcome=context_outcome,
                    execution_outcome=projection.outcome,
                )
            return self._reconcile_terminal_projection(
                binding=binding,
                claimed=claimed,
                projection=projection,
                worker_owner=command.worker_owner,
            )
        binding = self.memory.resolve(envelope)
        self._require_envelope_binding(envelope, binding)
        graph = self.graphs.load(binding)
        self._require_graph_binding(graph, binding)
        now = self.clock.now()
        admitted = self.admissions.admit(binding, now=now)
        if admitted.state in {
            ConversationExecutionState.COMPLETED,
            ConversationExecutionState.FAILED,
            ConversationExecutionState.OUTCOME_UNKNOWN,
        }:
            return self._return_terminal_admission(binding, admitted)
        stable_attempt_id = uuid.uuid5(
            admitted.execution_id,
            "conversation-execution-attempt-v1",
        )
        node_invocation_id = uuid.uuid5(
            admitted.execution_id,
            f"conversation-node:{binding.llm_node_id}",
        )
        claimed = None
        if admitted.state is ConversationExecutionState.LEASED:
            now = self.clock.now()
            claimed = self.admissions.claim(
                binding,
                owner=command.worker_owner,
                attempt_id=stable_attempt_id,
                lease_deadline=now + self.lease_duration,
                now=now,
            )
            terminal_projection = self.memory.recover_terminal(
                binding,
                admission_id=admitted.admission_id,
                execution_id=admitted.execution_id,
                attempt_id=stable_attempt_id,
                node_invocation_id=node_invocation_id,
                provider_attempt_id=_provider_attempt_id(
                    admitted.execution_id,
                    node_invocation_id,
                ),
            )
            if terminal_projection is not None:
                return self._reconcile_terminal_projection(
                    binding=binding,
                    claimed=claimed,
                    projection=terminal_projection,
                    worker_owner=command.worker_owner,
                )
        checkpoint = self.memory.recover_checkpoint(
            binding,
            execution_id=admitted.execution_id,
        )
        if checkpoint is None:
            binding = self.memory.observe_admitted(
                binding,
                admission_id=admitted.admission_id,
                claim_generation=envelope.claim_generation,
                broker_message_id=envelope.broker_message_id,
            )
            self._observe(
                "execution_admitted",
                binding,
                admitted.admission_id,
                admitted.execution_id,
            )
        if claimed is None:
            now = self.clock.now()
            claimed = self.admissions.claim(
                binding,
                owner=command.worker_owner,
                attempt_id=stable_attempt_id,
                lease_deadline=now + self.lease_duration,
                now=now,
            )
        if checkpoint is not None:
            return self._complete_from_checkpoint(
                binding=binding,
                claimed=claimed,
                checkpoint=checkpoint,
                worker_owner=command.worker_owner,
            )
        binding = self.memory.observe_running(
            binding,
            admission_id=claimed.admission_id,
            execution_id=claimed.execution_id,
            attempt_id=claimed.attempt_id,
        )
        self._observe(
            "execution_running",
            binding,
            claimed.admission_id,
            claimed.execution_id,
        )
        input_text = self.memory.read_current_input(
            binding,
            execution_id=claimed.execution_id,
            attempt_id=claimed.attempt_id,
        )
        llm_data = _llm_data(graph.graph, binding.llm_node_id)
        try:
            preparation = self.provider.prepare(
                binding=binding,
                admission_id=claimed.admission_id,
                execution_id=claimed.execution_id,
                node_invocation_id=node_invocation_id,
                node_data=llm_data,
                deployment_config=graph.deployment_config,
            )
        except (
            LLMCredentialNotAvailableError,
            ProviderExecutionConfigurationError,
        ) as exc:
            safe_reason = _safe_reason_code(exc, "provider_not_sent")
            self.admissions.require_fence(
                binding,
                owner=command.worker_owner,
                lease_generation=claimed.lease_generation,
                now=self.clock.now(),
            )
            self.memory.fail(
                binding,
                execution_id=claimed.execution_id,
                attempt_id=claimed.attempt_id,
                safe_reason_code=safe_reason,
            )
            self.admissions.finish(
                binding,
                owner=command.worker_owner,
                lease_generation=claimed.lease_generation,
                outcome="failed",
                result_entry_id=None,
                result_digest=None,
                safe_failure_reason=safe_reason,
                now=self.clock.now(),
            )
            self._observe(
                "execution_failed",
                binding,
                claimed.admission_id,
                claimed.execution_id,
                safe_failure_reason=safe_reason,
            )
            return ExecuteConversationTurnResult(
                admission_id=claimed.admission_id,
                execution_id=claimed.execution_id,
                turn_id=binding.turn_id,
                state=ConversationExecutionState.FAILED,
            )
        context_build = self.memory.build_context(
            binding,
            node_invocation_id=node_invocation_id,
            capability_reference=preparation.capability_reference,
            capability_revision=preparation.capability_revision,
            provider_attempt_id=preparation.provider_attempt_id,
            expires_at=preparation.expires_at,
        )
        now = self.clock.now()
        context = self.memory.claim_context(
            binding,
            plan_id=context_build.plan_id,
            lease_id=context_build.lease_id,
            node_invocation_id=node_invocation_id,
            capability_reference=preparation.capability_reference,
            capability_revision=preparation.capability_revision,
            provider_attempt_id=preparation.provider_attempt_id,
            claim_deadline_at=min(
                preparation.expires_at,
                now + self.context_lease_duration,
            ),
        )
        messages = _messages(
            llm_data,
            variable=binding.input_variable,
            input_text=input_text,
            history_block=context.history_block,
        )
        context_attempt_version = context.attempt_version
        usage_reference: str | None = None

        def before_provider_start(current_usage_reference: str) -> None:
            nonlocal context_attempt_version, usage_reference
            now_at_fence = self.clock.now()
            self.admissions.require_fence(
                binding,
                owner=command.worker_owner,
                lease_generation=claimed.lease_generation,
                now=now_at_fence,
            )
            self.memory.validate_current(
                binding,
                execution_id=claimed.execution_id,
                attempt_id=claimed.attempt_id,
            )
            context_attempt_version = self.memory.mark_provider_started(
                binding,
                context_attempt_id=context.attempt_id,
                expected_version=context_attempt_version,
                usage_reference=current_usage_reference,
            )
            usage_reference = current_usage_reference

        try:
            provider_result = self.provider.generate(
                binding=binding,
                admission_id=claimed.admission_id,
                execution_id=claimed.execution_id,
                node_invocation_id=node_invocation_id,
                node_data=llm_data,
                preparation=preparation,
                messages=messages,
                before_provider_start=before_provider_start,
            )
        except ConversationExecutionFenceError:
            raise
        except ProviderInvocationOutcomeUnknownError:
            self.admissions.require_fence(
                binding,
                owner=command.worker_owner,
                lease_generation=claimed.lease_generation,
                now=self.clock.now(),
            )
            self.memory.finish_context_attempt(
                binding,
                context_attempt_id=context.attempt_id,
                expected_version=context_attempt_version,
                outcome="outcome_unknown",
                safe_failure_reason="provider_outcome_unknown",
            )
            self.memory.fail(
                binding,
                execution_id=claimed.execution_id,
                attempt_id=claimed.attempt_id,
                safe_reason_code="provider_outcome_unknown",
            )
            self.admissions.finish(
                binding,
                owner=command.worker_owner,
                lease_generation=claimed.lease_generation,
                outcome="outcome_unknown",
                result_entry_id=None,
                result_digest=None,
                safe_failure_reason="provider_outcome_unknown",
                now=self.clock.now(),
            )
            self._observe(
                "execution_outcome_unknown",
                binding,
                claimed.admission_id,
                claimed.execution_id,
                safe_failure_reason="provider_outcome_unknown",
            )
            raise
        except ProviderStartCommitRetryableError:
            # Memory's provider-start marker may already be durable while the
            # canonical usage start commit is ambiguous.  A bounded retry must
            # inspect the usage ledger before deciding whether send is allowed.
            raise
        except Exception as exc:
            safe_reason = _safe_reason_code(exc, "provider_not_sent")
            self.admissions.require_fence(
                binding,
                owner=command.worker_owner,
                lease_generation=claimed.lease_generation,
                now=self.clock.now(),
            )
            self.memory.finish_context_attempt(
                binding,
                context_attempt_id=context.attempt_id,
                expected_version=context_attempt_version,
                outcome="failed",
                safe_failure_reason=safe_reason,
            )
            self.memory.fail(
                binding,
                execution_id=claimed.execution_id,
                attempt_id=claimed.attempt_id,
                safe_reason_code=safe_reason,
            )
            self.admissions.finish(
                binding,
                owner=command.worker_owner,
                lease_generation=claimed.lease_generation,
                outcome="failed",
                result_entry_id=None,
                result_digest=None,
                safe_failure_reason=safe_reason,
                now=self.clock.now(),
            )
            self._observe(
                "execution_failed",
                binding,
                claimed.admission_id,
                claimed.execution_id,
                safe_failure_reason=safe_reason,
            )
            raise

        self.admissions.require_fence(
            binding,
            owner=command.worker_owner,
            lease_generation=claimed.lease_generation,
            now=self.clock.now(),
        )
        if usage_reference is None:
            raise ConversationExecutionRuntimeError(
                "provider_usage.binding_mismatch"
            )
        checkpoint = self.memory.checkpoint(
            binding,
            execution_id=claimed.execution_id,
            attempt_id=claimed.attempt_id,
            assistant_text=provider_result.text,
            context_attempt_id=context.attempt_id,
            context_attempt_version=context_attempt_version,
            usage_reference=usage_reference,
        )
        self.provider.record_success(provider_result)
        self.memory.finish_context_attempt(
            binding,
            context_attempt_id=context.attempt_id,
            expected_version=context_attempt_version,
            outcome="succeeded",
            safe_failure_reason=None,
        )
        return self._complete_from_checkpoint(
            binding=binding,
            claimed=claimed,
            checkpoint=checkpoint,
            worker_owner=command.worker_owner,
            resume_usage=False,
        )

    def _return_terminal_admission(
        self,
        binding: ConversationExecutionBinding,
        admitted,
    ) -> ExecuteConversationTurnResult:
        terminal_event = {
            ConversationExecutionState.COMPLETED: "execution_completed",
            ConversationExecutionState.FAILED: "execution_failed",
            ConversationExecutionState.OUTCOME_UNKNOWN: (
                "execution_outcome_unknown"
            ),
        }[admitted.state]
        self._observe(
            terminal_event,
            binding,
            admitted.admission_id,
            admitted.execution_id,
            safe_failure_reason=admitted.safe_failure_reason,
        )
        return ExecuteConversationTurnResult(
            admission_id=admitted.admission_id,
            execution_id=admitted.execution_id,
            turn_id=binding.turn_id,
            state=admitted.state,
        )

    def _reconcile_terminal_projection(
        self,
        *,
        binding: ConversationExecutionBinding,
        claimed,
        projection: ConversationMemoryTerminalProjection,
        worker_owner: str,
    ) -> ExecuteConversationTurnResult:
        try:
            state = ConversationExecutionState(projection.outcome)
        except ValueError as exc:
            raise ConversationExecutionRuntimeError(
                "memory.terminal_projection_invalid"
            ) from exc
        self.admissions.finish(
            binding,
            owner=worker_owner,
            lease_generation=claimed.lease_generation,
            outcome=state.value,
            result_entry_id=projection.result_entry_id,
            result_digest=projection.result_digest,
            safe_failure_reason=projection.safe_failure_reason,
            now=self.clock.now(),
        )
        self._observe(
            f"execution_{state.value}",
            binding,
            claimed.admission_id,
            claimed.execution_id,
            safe_failure_reason=projection.safe_failure_reason,
        )
        return ExecuteConversationTurnResult(
            admission_id=claimed.admission_id,
            execution_id=claimed.execution_id,
            turn_id=binding.turn_id,
            state=state,
        )

    def _complete_from_checkpoint(
        self,
        *,
        binding: ConversationExecutionBinding,
        claimed,
        checkpoint: ConversationMemoryCheckpoint,
        worker_owner: str,
        resume_usage: bool = True,
    ) -> ExecuteConversationTurnResult:
        if resume_usage:
            self.provider.resume_checkpoint(
                checkpoint,
                organization_id=binding.organization_id,
            )
            if checkpoint.context_attempt_outcome == "provider_started":
                self.memory.finish_context_attempt(
                    binding,
                    context_attempt_id=checkpoint.context_attempt_id,
                    expected_version=checkpoint.context_attempt_version,
                    outcome="succeeded",
                    safe_failure_reason=None,
                )
            elif checkpoint.context_attempt_outcome not in {
                "succeeded",
                "outcome_unknown",
            }:
                raise ConversationExecutionRuntimeError(
                    "memory.checkpoint_invalid"
                )
        self.admissions.require_fence(
            binding,
            owner=worker_owner,
            lease_generation=claimed.lease_generation,
            now=self.clock.now(),
        )
        self.memory.complete(
            binding,
            execution_id=claimed.execution_id,
            attempt_id=claimed.attempt_id,
            checkpoint=checkpoint,
        )
        self.admissions.finish(
            binding,
            owner=worker_owner,
            lease_generation=claimed.lease_generation,
            outcome="completed",
            result_entry_id=checkpoint.entry_id,
            result_digest=checkpoint.content_digest,
            safe_failure_reason=None,
            now=self.clock.now(),
        )
        self._observe(
            "execution_completed",
            binding,
            claimed.admission_id,
            claimed.execution_id,
        )
        return ExecuteConversationTurnResult(
            admission_id=claimed.admission_id,
            execution_id=claimed.execution_id,
            turn_id=binding.turn_id,
            state=ConversationExecutionState.COMPLETED,
        )

    @staticmethod
    def _require_envelope_binding(
        envelope: ConversationTurnTaskEnvelope,
        binding: ConversationExecutionBinding,
    ) -> None:
        if (
            envelope.organization_id != binding.organization_id
            or envelope.dispatch_id != binding.dispatch_id
            or envelope.turn_id != binding.turn_id
            or envelope.claim_generation != binding.dispatch_claim_generation
            or envelope.broker_message_id != binding.broker_message_id
            or envelope.memory_contract_version != binding.memory_contract_version
            or envelope.storage_generation != binding.storage_generation
            or envelope.minimum_worker_capability
            != binding.minimum_worker_capability
        ):
            raise ValueError("memory.execution_binding_mismatch")

    @staticmethod
    def _require_graph_binding(
        graph: ConversationExecutionGraph,
        binding: ConversationExecutionBinding,
    ) -> ConversationMemoryRuntimeContract:
        try:
            contract = validate_conversation_memory_runtime(
                graph.graph,
                graph.deployment_config,
            )
        except ConversationMemoryRuntimeContractError as exc:
            raise ConversationExecutionRuntimeError(exc.code) from exc
        if contract is None or (
            contract.contract_version != binding.memory_contract_version
            or contract.storage_generation != binding.storage_generation
            or contract.mapping_version != binding.mapping_version
            or contract.memory_policy_version != binding.memory_policy_version
            or contract.start_node_id != binding.start_node_id
            or contract.input_variable != binding.input_variable
            or contract.llm_node_id != binding.llm_node_id
            or contract.answer_node_id != binding.answer_node_id
            or contract.output_variable != binding.output_variable
            or contract.memory.max_turns != binding.max_turns
            or contract.memory.max_context_tokens != binding.max_context_tokens
        ):
            raise ConversationExecutionRuntimeError("memory.runtime_binding_stale")
        return contract

    def _observe(
        self,
        event: str,
        binding: ConversationExecutionBinding,
        admission_id: uuid.UUID,
        execution_id: uuid.UUID,
        *,
        safe_failure_reason: str | None = None,
    ) -> None:
        self.observer.record(
            event=event,
            organization_id=binding.organization_id,
            admission_id=admission_id,
            execution_id=execution_id,
            app_id=binding.app_id,
            workflow_id=binding.workflow_id,
            deployment_id=binding.deployment_id,
            deployment_version=binding.deployment_version,
            session_id=binding.session_id,
            turn_id=binding.turn_id,
            node_id=binding.llm_node_id,
            node_type="llmNode",
            safe_failure_reason=safe_failure_reason,
        )


def _llm_data(graph: Mapping[str, Any], node_id: str) -> Mapping[str, Any]:
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        raise ConversationExecutionRuntimeError("memory.graph_unsupported")
    matches = [
        node.get("data")
        for node in nodes
        if isinstance(node, Mapping) and node.get("id") == node_id
    ]
    if len(matches) != 1 or not isinstance(matches[0], Mapping):
        raise ConversationExecutionRuntimeError("memory.graph_unsupported")
    return matches[0]


def _provider_attempt_id(
    execution_id: uuid.UUID,
    node_invocation_id: uuid.UUID,
) -> uuid.UUID:
    return uuid.uuid5(
        execution_id,
        f"provider_execution:{node_invocation_id}:main_generation",
    )


def _messages(
    node_data: Mapping[str, Any],
    *,
    variable: str,
    input_text: str,
    history_block: str,
) -> tuple[Mapping[str, str], ...]:
    if not isinstance(input_text, str) or not input_text:
        raise ConversationExecutionRuntimeError("memory.input_mapping_invalid")
    system_prompt = node_data.get("system_prompt") or ""
    user_prompt = node_data.get("user_prompt") or ""
    assistant_prompt = node_data.get("assistant_prompt") or ""
    if any(not isinstance(value, str) for value in (system_prompt, user_prompt, assistant_prompt)):
        raise ConversationExecutionRuntimeError("memory.llm_behavior_unsupported")
    if _TEMPLATE_VARIABLE.findall(system_prompt) or _TEMPLATE_VARIABLE.findall(
        assistant_prompt
    ):
        raise ConversationExecutionRuntimeError("memory.input_mapping_invalid")
    variables = _TEMPLATE_VARIABLE.findall(user_prompt)
    if set(variables) != {variable}:
        raise ConversationExecutionRuntimeError("memory.input_mapping_invalid")
    rendered_user = _TEMPLATE_VARIABLE.sub(
        lambda match: input_text if match.group(1) == variable else "",
        user_prompt,
    )
    messages: list[Mapping[str, str]] = []
    system_parts = [PLATFORM_UNTRUSTED_CONTEXT_GUARDRAIL_PROMPT]
    if system_prompt:
        system_parts.append(system_prompt)
    messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    if history_block:
        messages.append({"role": "user", "content": history_block})
    messages.append({"role": "user", "content": rendered_user})
    if assistant_prompt:
        messages.append({"role": "assistant", "content": assistant_prompt})
    return tuple(messages)


def _safe_reason_code(error: Exception, default: str) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", code):
        return code
    return default


__all__ = [
    "ConversationExecutionBinding",
    "ConversationExecutionGraph",
    "ConversationExecutionRuntimeError",
    "ConversationMemoryCheckpoint",
    "ConversationMemoryContextBuild",
    "ConversationMemoryContextClaim",
    "ConversationMemoryTerminalProjection",
    "ConversationMemoryTerminalRecovery",
    "ConversationProviderPreparation",
    "ConversationProviderResult",
    "ExecuteConversationTurnCommand",
    "ExecuteConversationTurnResult",
    "ExecuteConversationTurnUseCase",
]
