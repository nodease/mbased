from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from apps.shared.domain.conversation_memory_task import ConversationTurnTaskEnvelope
from apps.memory.domain.errors import AccessGrantNotUsableError
from apps.workflow_engine.application.conversation_memory_execution import (
    ConversationExecutionBinding,
    ConversationExecutionGraph,
    ConversationMemoryCheckpoint,
    ConversationMemoryContextBuild,
    ConversationMemoryContextClaim,
    ConversationMemoryTerminalProjection,
    ConversationMemoryTerminalRecovery,
    ConversationProviderPreparation,
    ConversationProviderResult,
    ExecuteConversationTurnCommand,
    ExecuteConversationTurnUseCase,
)
from apps.workflow_engine.application.conversation_memory_admission import (
    ConversationExecutionFenceError,
    ConversationExecutionState,
)
from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionConfigurationError,
    ProviderInvocationOutcomeUnknownError,
    ProviderStartCommitRetryableError,
)


NOW = datetime(2026, 7, 22, 16, tzinfo=timezone.utc)


def _graph() -> tuple[dict, dict]:
    graph = {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "data": {
                    "variables": [
                        {
                            "id": "question",
                            "name": "question",
                            "type": "paragraph",
                            "required": True,
                            "max_length": 16_384,
                        }
                    ]
                },
            },
            {
                "id": "llm",
                "type": "llmNode",
                "data": {
                    "model_id": "fixed-model",
                    "task_type": "generate",
                    "system_prompt": "Answer safely.",
                    "user_prompt": "Question: {{ question }}",
                    "referenced_variables": [
                        {
                            "name": "question",
                            "value_selector": ["start", "question"],
                        }
                    ],
                    "parameters": {"temperature": 0.2, "n": 1},
                    "memory": {
                        "enabled": True,
                        "channel": "conversation",
                        "readSource": "conversation_turns",
                        "writeMode": "none",
                        "maxTurns": 5,
                        "maxContextTokens": 1_200,
                        "strategy": "window",
                        "failurePolicy": "fail_node",
                    },
                },
            },
            {
                "id": "answer",
                "type": "answerNode",
                "data": {
                    "outputs": [
                        {
                            "variable": "answer",
                            "value_selector": ["llm", "text"],
                        }
                    ]
                },
            },
        ],
        "edges": [
            {"id": "start-llm", "source": "start", "target": "llm"},
            {"id": "llm-answer", "source": "llm", "target": "answer"},
        ],
    }
    config = {
        "conversation_memory": {
            "contract_version": "conversation-memory-v1",
            "storage_generation": 1,
            "mapping_version": "conversation-mapping-v1",
            "memory_policy_version": "memory-policy-v1",
            "input": {"node_id": "start", "variable": "question"},
            "output": {"node_id": "answer", "variable": "answer"},
        }
    }
    return graph, config


def _binding() -> ConversationExecutionBinding:
    return ConversationExecutionBinding(
        organization_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=2,
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        dispatch_id=uuid.uuid4(),
        dispatch_claim_generation=1,
        broker_message_id="message-1",
        request_fingerprint="a" * 64,
        lifecycle_revision=1,
        turn_version=1,
        memory_contract_version="conversation-memory-v1",
        mapping_version="conversation-mapping-v1",
        memory_policy_version="memory-policy-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
        start_node_id="start",
        input_variable="question",
        llm_node_id="llm",
        answer_node_id="answer",
        output_variable="answer",
        max_turns=5,
        max_context_tokens=1_200,
    )


class _Clock:
    def now(self):
        return NOW


class _GraphStore:
    def __init__(self, binding, graph=None):
        self.binding = binding
        values = _graph()
        self.value = ConversationExecutionGraph(
            graph=graph or values[0],
            deployment_config=values[1],
        )

    def load(self, binding):
        assert binding.deployment_id == self.binding.deployment_id
        return self.value


class _Admission:
    def __init__(self, binding):
        self.binding = binding
        self.admission_id = uuid.uuid5(
            binding.dispatch_id,
            "conversation-workflow-admission-v1",
        )
        self.execution_id = uuid.uuid5(
            self.admission_id,
            "conversation-execution-v1",
        )
        self.attempt_id = uuid.uuid4()
        self.state = ConversationExecutionState.ADMITTED
        self.safe_failure_reason = None
        self.events = []
        self.fence_failure_call = None
        self.finish_error_once = None
        self.lease_owner = None
        self.reject_other_owner = False
        self.last_finish_kwargs = None

    def admit(self, binding, *, now):
        self.events.append("admit")
        return type(
            "Admitted",
            (),
            {
                "admission_id": self.admission_id,
                "execution_id": self.execution_id,
                "state": self.state,
                "replayed": False,
                "safe_failure_reason": self.safe_failure_reason,
            },
        )()

    def claim(self, binding, *, owner, attempt_id, lease_deadline, now):
        self.events.append("claim")
        if (
            self.reject_other_owner
            and self.state is ConversationExecutionState.LEASED
            and self.lease_owner not in {None, owner}
        ):
            raise ConversationExecutionFenceError()
        self.lease_owner = owner
        self.attempt_id = attempt_id
        self.state = ConversationExecutionState.LEASED
        return type(
            "Claimed",
            (),
            {
                "admission_id": self.admission_id,
                "execution_id": self.execution_id,
                "attempt_id": attempt_id,
                "lease_generation": 1,
            },
        )()

    def require_fence(self, binding, *, owner, lease_generation, now):
        self.events.append("fence")
        if (
            self.fence_failure_call is not None
            and self.events.count("fence") == self.fence_failure_call
        ):
            raise ConversationExecutionFenceError()

    def finish(self, binding, **kwargs):
        self.events.append(f"finish:{kwargs['outcome']}")
        self.last_finish_kwargs = kwargs
        if self.finish_error_once is not None:
            error = self.finish_error_once
            self.finish_error_once = None
            raise error
        self.state = ConversationExecutionState(kwargs["outcome"])


class _Memory:
    def __init__(self, binding):
        self.binding = binding
        self.events = []
        self.context_attempt_id = uuid.uuid4()
        self.checkpoint_value = None
        self.terminal_projection = None
        self.terminal_binding = None
        self.context_terminal_projection = None
        self.fail_error_once = None
        self.validate_error = None
        self.reference_requires_failure = False
        self.reference_requires_acknowledgement = False
        self.finalize_reference_kwargs = None

    def resolve(self, envelope):
        self.events.append("resolve")
        return self.binding

    def resolve_terminal(self, envelope, **_kwargs):
        self.events.append("resolve_terminal")
        if self.terminal_projection is None:
            return None
        return ConversationMemoryTerminalRecovery(
            binding=self.terminal_binding or self.binding,
            projection=self.terminal_projection,
            requires_memory_failure=self.reference_requires_failure,
            requires_dispatch_acknowledgement=(
                self.reference_requires_acknowledgement
            ),
        )

    def finalize_reference_failure(self, binding, **kwargs):
        self.events.append("finalize_reference_failure")
        self.finalize_reference_kwargs = kwargs
        projection = ConversationMemoryTerminalProjection(
            outcome=kwargs["execution_outcome"],
            safe_failure_reason=kwargs["safe_failure_reason"],
        )
        self.terminal_projection = projection
        self.terminal_binding = binding
        self.binding = replace(binding, turn_version=binding.turn_version + 1)
        return projection

    def observe_admitted(self, binding, **_kwargs):
        self.events.append("admitted")
        self.binding = replace(binding, turn_version=binding.turn_version + 1)
        return self.binding

    def observe_running(self, binding, **_kwargs):
        self.events.append("running")
        self.binding = replace(binding, turn_version=binding.turn_version + 1)
        return self.binding

    def read_current_input(self, binding, **_kwargs):
        self.events.append("read_input")
        return "current question"

    def build_context(self, binding, **_kwargs):
        self.events.append("build_context")
        return ConversationMemoryContextBuild(uuid.uuid4(), uuid.uuid4())

    def claim_context(self, binding, **_kwargs):
        self.events.append("claim_context")
        return ConversationMemoryContextClaim(
            attempt_id=self.context_attempt_id,
            attempt_version=1,
            history_block="<UNTRUSTED_CONVERSATION_HISTORY version=\"1\">\n"
            '{"role":"user","content":"previous"}\n'
            '{"role":"assistant","content":"prior answer"}\n'
            "</UNTRUSTED_CONVERSATION_HISTORY>",
        )

    def validate_current(self, binding, **_kwargs):
        self.events.append("validate_current")
        if self.validate_error is not None:
            raise self.validate_error

    def recover_checkpoint(self, binding, **_kwargs):
        self.events.append("recover_checkpoint")
        return self.checkpoint_value

    def recover_terminal(self, binding, **_kwargs):
        self.events.append("recover_terminal")
        if self.context_terminal_projection is not None:
            self.terminal_projection = self.context_terminal_projection
            self.terminal_binding = binding
            self.binding = replace(
                binding,
                turn_version=binding.turn_version + 1,
            )
            return self.context_terminal_projection
        return self.terminal_projection

    def mark_provider_started(self, binding, **kwargs):
        self.events.append(f"memory_start:{kwargs['usage_reference']}")
        return 2

    def finish_context_attempt(self, binding, **kwargs):
        self.events.append(f"context_finish:{kwargs['outcome']}")
        if kwargs["outcome"] in {"failed", "outcome_unknown"}:
            self.context_terminal_projection = (
                ConversationMemoryTerminalProjection(
                    outcome=kwargs["outcome"],
                    safe_failure_reason=kwargs["safe_failure_reason"],
                )
            )

    def checkpoint(self, binding, **kwargs):
        self.events.append("checkpoint")
        assert kwargs["assistant_text"] == "provider answer"
        self.checkpoint_value = ConversationMemoryCheckpoint(
            entry_id=uuid.uuid4(),
            content_digest="c" * 64,
            context_attempt_id=kwargs["context_attempt_id"],
            context_attempt_version=kwargs["context_attempt_version"],
            usage_reference=kwargs["usage_reference"],
            lifecycle_revision=binding.lifecycle_revision,
            turn_version=binding.turn_version,
            state=object(),
        )
        return self.checkpoint_value

    def complete(self, binding, **_kwargs):
        self.events.append("complete")

    def fail(self, binding, **kwargs):
        self.events.append(f"fail:{kwargs['safe_reason_code']}")
        if self.fail_error_once is not None:
            error = self.fail_error_once
            self.fail_error_once = None
            raise error
        safe_reason = kwargs["safe_reason_code"]
        self.terminal_projection = ConversationMemoryTerminalProjection(
            outcome=(
                "outcome_unknown"
                if safe_reason == "provider_outcome_unknown"
                else "failed"
            ),
            safe_failure_reason=safe_reason,
        )
        self.terminal_binding = binding
        self.binding = replace(binding, turn_version=binding.turn_version + 1)


class _Provider:
    def __init__(
        self,
        *,
        prepare_error=None,
        error=None,
        success_error=None,
        success_error_committed=True,
    ):
        self.prepare_error = prepare_error
        self.error = error
        self.success_error = success_error
        self.success_error_committed = success_error_committed
        self.events = []
        self.messages = None
        self.terminal_success = False
        self.reference_usage_state = "not_started"
        self.preparation = ConversationProviderPreparation(
            capability_reference="capability-1",
            capability_revision="3",
            provider_attempt_id=uuid.uuid4(),
            expires_at=NOW + timedelta(minutes=2),
            state=object(),
        )

    def prepare(self, **_kwargs):
        self.events.append("prepare")
        if self.prepare_error is not None:
            raise self.prepare_error
        return self.preparation

    def generate(self, *, messages, before_provider_start, **_kwargs):
        self.events.append("intent")
        self.messages = messages
        before_provider_start("usage-operation-1")
        self.events.append("usage_started")
        if self.error is not None:
            raise self.error
        self.events.append("provider_io")
        return ConversationProviderResult(
            text="provider answer",
            usage={"prompt_tokens": 4, "completion_tokens": 2},
            state=object(),
        )

    def record_success(self, result):
        self.events.append("usage_success")
        if self.success_error is not None:
            error = self.success_error
            self.success_error = None
            self.terminal_success = self.success_error_committed
            raise error
        self.terminal_success = True

    def resume_checkpoint(self, checkpoint, **_kwargs):
        if not self.terminal_success:
            self.events.append("usage_outcome_unknown")
        self.events.append("resume_checkpoint")

    def reconcile_reference_terminal(self, **_kwargs):
        self.events.append("reconcile_reference_terminal")
        return self.reference_usage_state


class _Observer:
    def __init__(self):
        self.events = []
        self.records = []

    def record(self, **kwargs):
        self.events.append(kwargs["event"])
        self.records.append(kwargs)


class _FailingObserver:
    def record(self, **_kwargs):
        raise RuntimeError("journal unavailable")


class _FailingOnceObserver(_Observer):
    def __init__(self, event):
        super().__init__()
        self.event = event

    def record(self, **kwargs):
        if kwargs["event"] == self.event:
            self.event = None
            raise RuntimeError("journal unavailable")
        super().record(**kwargs)


def _envelope(binding):
    return ConversationTurnTaskEnvelope(
        organization_id=binding.organization_id,
        dispatch_id=binding.dispatch_id,
        turn_id=binding.turn_id,
        claim_generation=1,
        broker_message_id="message-1",
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
    )


def _use_case(binding, *, provider=None, observer=None):
    memory = _Memory(binding)
    admission = _Admission(binding)
    provider = provider or _Provider()
    return (
        ExecuteConversationTurnUseCase(
            memory=memory,
            admissions=admission,
            graphs=_GraphStore(binding),
            provider=provider,
            observer=observer or _Observer(),
            clock=_Clock(),
            worker_capability="memory-runtime-v1",
            lease_duration=timedelta(seconds=30),
            context_lease_duration=timedelta(seconds=20),
        ),
        memory,
        admission,
        provider,
    )


def test_vertical_execution_orders_fences_and_keeps_history_untrusted() -> None:
    binding = _binding()
    use_case, memory, admission, provider = _use_case(binding)

    result = use_case.execute(
        ExecuteConversationTurnCommand(
            envelope=_envelope(binding),
            worker_owner="worker-a",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert result.state is ConversationExecutionState.COMPLETED
    assert provider.messages[0]["role"] == "system"
    assert provider.messages[0]["content"].endswith("Answer safely.")
    assert "untrusted" in provider.messages[0]["content"].lower()
    assert provider.messages[-1] == {
        "role": "user",
        "content": "Question: current question",
    }
    assert provider.messages[-2]["role"] == "user"
    assert "UNTRUSTED_CONVERSATION_HISTORY" in provider.messages[-2]["content"]
    assert memory.events.index("validate_current") < memory.events.index(
        "memory_start:usage-operation-1"
    )
    assert memory.events.index("checkpoint") < memory.events.index("complete")
    assert admission.events.index("fence") < admission.events.index(
        "finish:completed"
    )
    assert provider.events == [
        "prepare",
        "intent",
        "usage_started",
        "provider_io",
        "usage_success",
    ]


def test_execution_observer_receives_only_safe_public_correlation() -> None:
    binding = _binding()
    observer = _Observer()
    use_case, _memory, admission, _provider = _use_case(
        binding,
        observer=observer,
    )

    result = use_case.execute(
        ExecuteConversationTurnCommand(
            envelope=_envelope(binding),
            worker_owner="worker-a",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert observer.events == [
        "execution_admitted",
        "execution_running",
        "execution_completed",
    ]
    for record in observer.records:
        assert set(record) == {
            "event",
            "organization_id",
            "admission_id",
            "execution_id",
            "app_id",
            "workflow_id",
            "deployment_id",
            "deployment_version",
            "session_id",
            "turn_id",
            "node_id",
            "node_type",
            "safe_failure_reason",
        }
        assert record["organization_id"] == binding.organization_id
        assert record["admission_id"] == admission.admission_id
        assert record["execution_id"] == result.execution_id
        assert record["node_id"] == binding.llm_node_id
        assert record["node_type"] == "llmNode"
        assert record["safe_failure_reason"] is None


def test_durable_observer_failure_remains_retryable() -> None:
    binding = _binding()
    use_case, memory, admission, provider = _use_case(
        binding,
        observer=_FailingObserver(),
    )

    with pytest.raises(RuntimeError, match="journal unavailable"):
        use_case.execute(
            ExecuteConversationTurnCommand(
                envelope=_envelope(binding),
                worker_owner="worker-a",
                delivery_attempt_id=uuid.uuid4(),
            )
        )

    assert "admitted" in memory.events
    assert admission.events == ["admit"]
    assert provider.events == []


def test_terminal_redelivery_reconciles_missing_journal_event() -> None:
    binding = _binding()
    observer = _Observer()
    use_case, memory, admission, provider = _use_case(
        binding,
        observer=observer,
    )
    admission.state = ConversationExecutionState.FAILED
    admission.safe_failure_reason = "provider_not_sent"

    result = use_case.execute(
        ExecuteConversationTurnCommand(
            envelope=_envelope(binding),
            worker_owner="worker-b",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert result.state is ConversationExecutionState.FAILED
    assert observer.events == ["execution_failed"]
    assert observer.records[0]["safe_failure_reason"] == "provider_not_sent"
    assert memory.events == ["resolve_terminal", "resolve"]
    assert provider.events == []


def test_reference_only_terminal_redelivery_skips_active_runtime_resolution() -> None:
    binding = _binding()
    observer = _Observer()
    use_case, memory, admission, provider = _use_case(
        binding,
        observer=observer,
    )
    memory.terminal_binding = binding
    memory.terminal_projection = ConversationMemoryTerminalProjection(
        outcome="failed",
        safe_failure_reason="provider_not_sent",
    )
    admission.state = ConversationExecutionState.FAILED
    admission.safe_failure_reason = "provider_not_sent"

    result = use_case.execute(
        ExecuteConversationTurnCommand(
            envelope=_envelope(binding),
            worker_owner="worker-b",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert result.state is ConversationExecutionState.FAILED
    assert memory.events == ["resolve_terminal"]
    assert observer.events == ["execution_failed"]
    assert provider.events == []


def test_reference_only_completed_split_finishes_with_exact_result_identity() -> None:
    binding = _binding()
    use_case, memory, admission, provider = _use_case(binding)
    result_entry_id = uuid.uuid4()
    result_digest = "d" * 64
    memory.terminal_binding = binding
    memory.terminal_projection = ConversationMemoryTerminalProjection(
        outcome="completed",
        safe_failure_reason=None,
        result_entry_id=result_entry_id,
        result_digest=result_digest,
    )
    admission.state = ConversationExecutionState.LEASED

    result = use_case.execute(
        ExecuteConversationTurnCommand(
            envelope=_envelope(binding),
            worker_owner="worker-b",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert result.state is ConversationExecutionState.COMPLETED
    assert admission.last_finish_kwargs["result_entry_id"] == result_entry_id
    assert admission.last_finish_kwargs["result_digest"] == result_digest
    assert admission.last_finish_kwargs["safe_failure_reason"] is None
    assert memory.events == ["resolve_terminal"]
    assert provider.events == []


@pytest.mark.parametrize(
    "failed_event",
    ["execution_admitted", "execution_running"],
)
def test_reference_only_lifecycle_cleanup_fails_safe_after_journal_crash(
    failed_event: str,
) -> None:
    binding = _binding()
    observer = _FailingOnceObserver(failed_event)
    use_case, memory, admission, provider = _use_case(
        binding,
        observer=observer,
    )
    command = ExecuteConversationTurnCommand(
        envelope=_envelope(binding),
        worker_owner="worker-a",
        delivery_attempt_id=uuid.uuid4(),
    )

    with pytest.raises(RuntimeError, match="journal unavailable"):
        use_case.execute(command)

    memory.terminal_binding = memory.binding
    memory.terminal_projection = ConversationMemoryTerminalProjection(
        outcome="failed",
        safe_failure_reason="memory.runtime_authorization_stale",
    )
    memory.reference_requires_failure = True

    result = use_case.execute(
        replace(
            command,
            worker_owner="worker-b",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert result.state is ConversationExecutionState.FAILED
    assert "finalize_reference_failure" in memory.events
    assert provider.events == ([] if failed_event == "execution_admitted" else [])
    assert admission.events[-2:] == ["claim", "finish:failed"]
    assert observer.events[-1] == "execution_failed"


@pytest.mark.parametrize(
    ("usage_state", "expected_state", "expected_context_outcome"),
    [
        ("not_started", ConversationExecutionState.FAILED, "failed"),
        ("failed", ConversationExecutionState.FAILED, "failed"),
        (
            "outcome_unknown",
            ConversationExecutionState.OUTCOME_UNKNOWN,
            "outcome_unknown",
        ),
        ("succeeded", ConversationExecutionState.FAILED, "succeeded"),
    ],
)
def test_reference_lifecycle_cleanup_preserves_canonical_provider_outcome(
    usage_state: str,
    expected_state: ConversationExecutionState,
    expected_context_outcome: str,
) -> None:
    binding = _binding()
    use_case, memory, admission, provider = _use_case(binding)
    provider_attempt_id = uuid.uuid4()
    memory.terminal_binding = binding
    memory.terminal_projection = ConversationMemoryTerminalProjection(
        outcome="failed",
        safe_failure_reason="memory.runtime_authorization_stale",
        provider_attempt_id=provider_attempt_id,
        usage_reference="usage-operation-1",
    )
    memory.reference_requires_failure = True
    provider.reference_usage_state = usage_state

    result = use_case.execute(
        ExecuteConversationTurnCommand(
            envelope=_envelope(binding),
            worker_owner="worker-b",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert result.state is expected_state
    assert provider.events == ["reconcile_reference_terminal"]
    assert memory.finalize_reference_kwargs is not None
    assert memory.finalize_reference_kwargs["provider_attempt_id"] == (
        provider_attempt_id
    )
    assert memory.finalize_reference_kwargs["context_outcome"] == (
        expected_context_outcome
    )
    assert memory.finalize_reference_kwargs["execution_outcome"] == (
        expected_state.value
    )
    assert admission.events == [
        "admit",
        "claim",
        f"finish:{expected_state.value}",
    ]


def test_crash_after_terminal_usage_commit_completes_from_checkpoint_without_provider_io() -> None:
    binding = _binding()
    provider = _Provider(success_error=RuntimeError("fault_after_usage_commit"))
    use_case, memory, admission, _provider = _use_case(binding, provider=provider)
    command = ExecuteConversationTurnCommand(
        envelope=_envelope(binding),
        worker_owner="worker-a",
        delivery_attempt_id=uuid.uuid4(),
    )

    with pytest.raises(RuntimeError, match="fault_after_usage_commit"):
        use_case.execute(command)

    assert memory.checkpoint_value is not None
    assert provider.events.count("provider_io") == 1
    first_attempt_id = admission.attempt_id

    result = use_case.execute(replace(command, delivery_attempt_id=uuid.uuid4()))

    assert result.state is ConversationExecutionState.COMPLETED
    assert admission.attempt_id == first_attempt_id
    assert provider.events.count("provider_io") == 1
    assert provider.events.count("intent") == 1
    assert provider.events[-1] == "resume_checkpoint"
    assert "complete" in memory.events


def test_crash_between_checkpoint_and_usage_terminal_recovers_without_provider_io() -> None:
    binding = _binding()
    provider = _Provider(
        success_error=RuntimeError("fault_before_usage_terminal"),
        success_error_committed=False,
    )
    use_case, memory, _admission, _provider = _use_case(
        binding,
        provider=provider,
    )
    command = ExecuteConversationTurnCommand(
        envelope=_envelope(binding),
        worker_owner="worker-a",
        delivery_attempt_id=uuid.uuid4(),
    )

    with pytest.raises(RuntimeError, match="fault_before_usage_terminal"):
        use_case.execute(command)

    result = use_case.execute(command)

    assert result.state is ConversationExecutionState.COMPLETED
    assert provider.events.count("provider_io") == 1
    assert provider.events.count("intent") == 1
    assert "usage_outcome_unknown" in provider.events
    assert "complete" in memory.events


def test_terminal_context_attempt_checkpoint_completes_without_rewriting_outcome() -> None:
    binding = _binding()
    use_case, memory, _admission, provider = _use_case(binding)
    memory.checkpoint_value = ConversationMemoryCheckpoint(
        entry_id=uuid.uuid4(),
        content_digest="c" * 64,
        context_attempt_id=memory.context_attempt_id,
        context_attempt_version=3,
        usage_reference="usage-operation-1",
        lifecycle_revision=binding.lifecycle_revision,
        turn_version=binding.turn_version,
        state=object(),
        context_attempt_outcome="outcome_unknown",
    )

    result = use_case.execute(
        ExecuteConversationTurnCommand(
            envelope=_envelope(binding),
            worker_owner="worker-a",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert result.state is ConversationExecutionState.COMPLETED
    assert provider.events == ["usage_outcome_unknown", "resume_checkpoint"]
    assert "context_finish:succeeded" not in memory.events
    assert "complete" in memory.events


def test_outcome_unknown_never_replays_provider_and_releases_turn_safely() -> None:
    binding = _binding()
    provider = _Provider(error=ProviderInvocationOutcomeUnknownError())
    use_case, memory, admission, _provider = _use_case(binding, provider=provider)

    with pytest.raises(ProviderInvocationOutcomeUnknownError):
        use_case.execute(
            ExecuteConversationTurnCommand(
                envelope=_envelope(binding),
                worker_owner="worker-a",
                delivery_attempt_id=uuid.uuid4(),
            )
        )

    assert provider.events.count("intent") == 1
    assert provider.events.count("usage_started") == 1
    assert "provider_io" not in provider.events
    assert "context_finish:outcome_unknown" in memory.events
    assert "fail:provider_outcome_unknown" in memory.events
    assert admission.events[-1] == "finish:outcome_unknown"


@pytest.mark.parametrize(
    ("provider_error", "expected_outcome", "safe_failure_reason"),
    [
        (
            ProviderInvocationOutcomeUnknownError(),
            ConversationExecutionState.OUTCOME_UNKNOWN,
            "provider_outcome_unknown",
        ),
        (
            RuntimeError("provider failed before response"),
            ConversationExecutionState.FAILED,
            "provider_not_sent",
        ),
    ],
)
def test_terminal_memory_redelivery_reconciles_admission_and_journal_without_provider_replay(
    provider_error: Exception,
    expected_outcome: ConversationExecutionState,
    safe_failure_reason: str,
) -> None:
    binding = _binding()
    observer = _Observer()
    provider = _Provider(error=provider_error)
    use_case, memory, admission, _provider = _use_case(
        binding,
        provider=provider,
        observer=observer,
    )
    admission.finish_error_once = RuntimeError("admission finish unavailable")
    command = ExecuteConversationTurnCommand(
        envelope=_envelope(binding),
        worker_owner="worker-a",
        delivery_attempt_id=uuid.uuid4(),
    )

    with pytest.raises(RuntimeError, match="admission finish unavailable"):
        use_case.execute(command)

    assert memory.terminal_projection is not None
    assert provider.events.count("intent") == 1
    assert observer.events == [
        "execution_admitted",
        "execution_running",
    ]

    result = use_case.execute(
        replace(
            command,
            worker_owner="worker-b",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert result.state is expected_outcome
    assert provider.events.count("intent") == 1
    assert provider.events.count("provider_io") == 0
    assert memory.events[-1] == "resolve_terminal"
    assert memory.events.count("recover_checkpoint") == 1
    assert memory.events.count("admitted") == 1
    assert memory.events.count("running") == 1
    assert admission.events[-2:] == [
        "claim",
        f"finish:{expected_outcome.value}",
    ]
    assert observer.events[-1] == f"execution_{expected_outcome.value}"
    assert observer.records[-1]["safe_failure_reason"] == safe_failure_reason


def test_context_terminal_crash_recovers_only_after_new_owner_claim() -> None:
    binding = _binding()
    provider = _Provider(error=ProviderInvocationOutcomeUnknownError())
    use_case, memory, admission, _provider = _use_case(
        binding,
        provider=provider,
    )
    memory.fail_error_once = RuntimeError("crash before turn fail")
    command = ExecuteConversationTurnCommand(
        envelope=_envelope(binding),
        worker_owner="worker-a",
        delivery_attempt_id=uuid.uuid4(),
    )

    with pytest.raises(RuntimeError, match="crash before turn fail"):
        use_case.execute(command)

    assert memory.terminal_projection is None
    assert memory.context_terminal_projection is not None
    first_recover_count = memory.events.count("recover_terminal")

    result = use_case.execute(
        replace(
            command,
            worker_owner="worker-b",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert result.state is ConversationExecutionState.OUTCOME_UNKNOWN
    assert admission.lease_owner == "worker-b"
    assert admission.events[-2:] == ["claim", "finish:outcome_unknown"]
    assert memory.events.count("recover_terminal") == first_recover_count + 1
    assert provider.events.count("intent") == 1


def test_active_other_owner_fences_context_terminal_recovery_before_memory_mutation() -> None:
    binding = _binding()
    provider = _Provider(error=ProviderInvocationOutcomeUnknownError())
    use_case, memory, admission, _provider = _use_case(
        binding,
        provider=provider,
    )
    memory.fail_error_once = RuntimeError("crash before turn fail")
    command = ExecuteConversationTurnCommand(
        envelope=_envelope(binding),
        worker_owner="worker-a",
        delivery_attempt_id=uuid.uuid4(),
    )
    with pytest.raises(RuntimeError, match="crash before turn fail"):
        use_case.execute(command)
    admission.reject_other_owner = True
    events_before_duplicate = list(memory.events)

    with pytest.raises(ConversationExecutionFenceError):
        use_case.execute(
            replace(
                command,
                worker_owner="worker-b",
                delivery_attempt_id=uuid.uuid4(),
            )
        )

    assert memory.events == events_before_duplicate + ["resolve_terminal", "resolve"]
    assert memory.terminal_projection is None


def test_stale_owner_callback_fence_failure_has_zero_memory_mutation() -> None:
    binding = _binding()
    use_case, memory, admission, provider = _use_case(binding)
    admission.fence_failure_call = 1

    with pytest.raises(ConversationExecutionFenceError):
        use_case.execute(
            ExecuteConversationTurnCommand(
                envelope=_envelope(binding),
                worker_owner="worker-a",
                delivery_attempt_id=uuid.uuid4(),
            )
        )

    assert provider.events == ["prepare", "intent"]
    assert not any(event.startswith("context_finish:") for event in memory.events)
    assert not any(event.startswith("fail:") for event in memory.events)
    assert admission.state is ConversationExecutionState.LEASED


def test_outcome_unknown_requires_current_fence_before_terminal_memory_mutation() -> None:
    binding = _binding()
    provider = _Provider(error=ProviderInvocationOutcomeUnknownError())
    use_case, memory, admission, _provider = _use_case(
        binding,
        provider=provider,
    )
    admission.fence_failure_call = 2

    with pytest.raises(ConversationExecutionFenceError):
        use_case.execute(
            ExecuteConversationTurnCommand(
                envelope=_envelope(binding),
                worker_owner="worker-a",
                delivery_attempt_id=uuid.uuid4(),
            )
        )

    assert "memory_start:usage-operation-1" in memory.events
    assert "context_finish:outcome_unknown" not in memory.events
    assert "fail:provider_outcome_unknown" not in memory.events
    assert admission.state is ConversationExecutionState.LEASED


def test_current_owner_authorization_failure_terminalizes_without_provider_start() -> None:
    binding = _binding()
    use_case, memory, admission, provider = _use_case(binding)
    memory.validate_error = AccessGrantNotUsableError()

    with pytest.raises(AccessGrantNotUsableError):
        use_case.execute(
            ExecuteConversationTurnCommand(
                envelope=_envelope(binding),
                worker_owner="worker-a",
                delivery_attempt_id=uuid.uuid4(),
            )
        )

    assert provider.events == ["prepare", "intent"]
    assert "memory_start:usage-operation-1" not in memory.events
    assert "context_finish:failed" in memory.events
    assert "fail:memory.session_hidden" in memory.events
    assert admission.state is ConversationExecutionState.FAILED


def test_permanent_prepare_failure_terminalizes_memory_and_admission() -> None:
    binding = _binding()
    provider = _Provider(prepare_error=ProviderExecutionConfigurationError())
    observer = _Observer()
    use_case, memory, admission, _provider = _use_case(
        binding,
        provider=provider,
        observer=observer,
    )

    result = use_case.execute(
        ExecuteConversationTurnCommand(
            envelope=_envelope(binding),
            worker_owner="worker-a",
            delivery_attempt_id=uuid.uuid4(),
        )
    )

    assert result.state is ConversationExecutionState.FAILED
    assert "fail:provider_capability.configuration_required" in memory.events
    assert admission.events[-1] == "finish:failed"
    assert "build_context" not in memory.events
    assert observer.events[-1] == "execution_failed"
    assert observer.records[-1]["safe_failure_reason"] == (
        "provider_capability.configuration_required"
    )


def test_transient_prepare_failure_remains_retryable_without_terminalizing() -> None:
    binding = _binding()
    provider = _Provider(prepare_error=RuntimeError("transient storage outage"))
    use_case, memory, admission, _provider = _use_case(
        binding,
        provider=provider,
    )

    with pytest.raises(RuntimeError, match="transient storage outage"):
        use_case.execute(
            ExecuteConversationTurnCommand(
                envelope=_envelope(binding),
                worker_owner="worker-a",
                delivery_attempt_id=uuid.uuid4(),
            )
        )

    assert not any(event.startswith("fail:") for event in memory.events)
    assert not any(event.startswith("finish:") for event in admission.events)
    assert "build_context" not in memory.events


def test_usage_start_commit_failure_preserves_marker_only_retry_state() -> None:
    binding = _binding()
    provider = _Provider(error=ProviderStartCommitRetryableError())
    use_case, memory, admission, _provider = _use_case(
        binding,
        provider=provider,
    )

    with pytest.raises(ProviderStartCommitRetryableError):
        use_case.execute(
            ExecuteConversationTurnCommand(
                envelope=_envelope(binding),
                worker_owner="worker-a",
                delivery_attempt_id=uuid.uuid4(),
            )
        )

    assert "memory_start:usage-operation-1" in memory.events
    assert not any(event.startswith("fail:") for event in memory.events)
    assert not any(event.startswith("finish:") for event in admission.events)


def test_stale_owner_after_provider_response_is_fenced_before_checkpoint() -> None:
    binding = _binding()
    use_case, memory, admission, provider = _use_case(binding)
    admission.fence_failure_call = 2

    with pytest.raises(ConversationExecutionFenceError):
        use_case.execute(
            ExecuteConversationTurnCommand(
                envelope=_envelope(binding),
                worker_owner="worker-a",
                delivery_attempt_id=uuid.uuid4(),
            )
        )

    assert provider.events[-1] == "provider_io"
    assert "checkpoint" not in memory.events
    assert "usage_success" not in provider.events
    assert "complete" not in memory.events
    assert not any(event.startswith("finish:") for event in admission.events)


def test_task_hint_mismatch_fails_before_admission_or_provider_prepare() -> None:
    binding = _binding()
    use_case, memory, admission, provider = _use_case(binding)
    envelope = replace(_envelope(binding), turn_id=uuid.uuid4())

    with pytest.raises(ValueError, match="memory.execution_binding_mismatch"):
        use_case.execute(
            ExecuteConversationTurnCommand(
                envelope=envelope,
                worker_owner="worker-a",
                delivery_attempt_id=uuid.uuid4(),
            )
        )

    assert memory.events == ["resolve_terminal", "resolve"]
    assert admission.events == []
    assert provider.events == []
