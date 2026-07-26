from __future__ import annotations

import copy
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from apps.memory.application.context import (
    ContextAttemptState,
    MemoryContextProviderAttempt,
)
from apps.memory.application.execution import (
    ConversationExecutionScope,
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
)
from apps.memory.application.public_lifecycle import PublicDeploymentBinding
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationMemoryEntry,
    ConversationSession,
    ConversationTurn,
    EntryLifecycle,
    MemoryTurnDispatchJob,
    ProtectedContent,
    ProtectedEntryContent,
    RequestIdentity,
    TurnStatus,
)
from apps.memory.domain.errors import (
    AccessGrantNotUsableError,
    DispatchStateConflictError,
    StaleTurnVersionError,
)
from apps.memory.domain.public_access import ConversationAccessGrant


NOW = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


def _scope() -> ConversationExecutionScope:
    organization_id = uuid.uuid4()
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    session = ConversationSession.create(
        session_id=uuid.uuid4(),
        organization_id=organization_id,
        app_id=app_id,
        workflow_id=workflow_id,
        deployment_id=deployment_id,
        deployment_version=3,
        deployment_snapshot_hash=None,
        mapping_version="conversation-mapping-v1",
        memory_policy_version="memory-policy-v1",
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        audience_kind=AudienceKind.PUBLIC_CHATBOT,
        subject_type=None,
        subject_id=None,
        idle_expires_at=NOW + timedelta(hours=1),
        absolute_expires_at=NOW + timedelta(days=1),
        now=NOW,
    )
    grant = ConversationAccessGrant.issue(
        grant_id=uuid.uuid4(),
        organization_id=organization_id,
        session_id=session.id,
        deployment_id=deployment_id,
        deployment_version=3,
        audience_kind=AudienceKind.PUBLIC_CHATBOT,
        verifier_hash="a" * 64,
        verifier_key_version="grant-v1",
        expires_at=NOW + timedelta(hours=1),
        now=NOW,
    )
    turn_id = uuid.uuid4()
    dispatch_id = uuid.uuid4()
    user_entry_id = uuid.uuid4()
    session.claim_turn(
        turn_id=turn_id,
        expected_lifecycle_revision=1,
        now=NOW,
    )
    turn = ConversationTurn.start(
        turn_id=turn_id,
        organization_id=organization_id,
        session_id=session.id,
        sequence=1,
        started_lifecycle_revision=1,
        request_identity=RequestIdentity("b" * 64, "c" * 64),
        user_entry_id=user_entry_id,
        dispatch_id=dispatch_id,
        access_grant_id=grant.id,
        request_fingerprint_key_version="admission-v1",
        now=NOW,
    )
    dispatch = MemoryTurnDispatchJob.pending(
        dispatch_id=dispatch_id,
        organization_id=organization_id,
        session_id=session.id,
        turn_id=turn_id,
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
        max_attempts=3,
        now=NOW,
    )
    dispatch.claim(
        owner="gateway",
        deadline=NOW + timedelta(seconds=30),
        now=NOW,
    )
    return ConversationExecutionScope(
        deployment=PublicDeploymentBinding(
            organization_id=organization_id,
            app_id=app_id,
            workflow_id=workflow_id,
            deployment_id=deployment_id,
            deployment_version=3,
            mapping_version="conversation-mapping-v1",
            memory_policy_version="memory-policy-v1",
            memory_contract_version="conversation-memory-v1",
            storage_generation=1,
            runtime_contract_ready=True,
            runtime_start_node_id="start",
            runtime_input_variable="question",
            runtime_llm_node_id="llm",
            runtime_answer_node_id="answer",
            runtime_output_variable="answer",
            runtime_max_turns=5,
            runtime_max_context_tokens=1200,
        ),
        grant=grant,
        session=session,
        turn=turn,
        dispatch=dispatch,
    )


class _Repository:
    def __init__(self, scope: ConversationExecutionScope) -> None:
        self.scope = scope
        self.resolve_for_update_calls: list[bool] = []
        protected = ProtectedContent(
            ciphertext=b"ciphertext",
            key_version="content-v1",
            format_version="memory-content-fernet-v1",
            content_digest="d" * 64,
            plaintext_byte_length=5,
        )
        self.entry = ConversationMemoryEntry.provisional_user(
            entry_id=scope.turn.user_entry_id,
            organization_id=scope.turn.organization_id,
            session_id=scope.session.id,
            turn_id=scope.turn.id,
            sequence=1,
            channel="conversation",
            content=ProtectedEntryContent(display=protected, model=protected),
            idempotency_key_hash="b" * 64,
            now=NOW,
        )
        self.entries = {self.entry.id: self.entry}
        self.context_attempt = None

    def current_time(self):
        return NOW

    def resolve_execution_scope(self, _command, *, for_update):
        self.resolve_for_update_calls.append(for_update)
        return self.scope

    def resolve_terminal_execution_scope(self, _command, *, for_update):
        self.resolve_for_update_calls.append(for_update)
        return self.scope

    def save_session(self, session):
        self.scope = replace(self.scope, session=session)

    def save_turn(self, turn):
        self.scope = replace(self.scope, turn=turn)

    def save_dispatch_job(self, dispatch):
        self.scope = replace(self.scope, dispatch=dispatch)

    def save_entry(self, entry):
        self.entries[entry.id] = entry
        if entry.id == self.entry.id:
            self.entry = entry

    def get_entry(self, **kwargs):
        return self.entries.get(kwargs["entry_id"])

    def lock_context_attempt(self, attempt_id):
        if self.context_attempt is not None and self.context_attempt.id == attempt_id:
            return self.context_attempt
        return None

    def save_context_attempt(self, attempt):
        self.context_attempt = attempt


class _Uow:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.snapshot = None

    def begin(self):
        self.snapshot = copy.deepcopy(self.repository.scope)

    def commit(self):
        self.snapshot = None

    def rollback(self):
        self.repository.scope = self.snapshot
        self.snapshot = None


def _resolve_command(scope: ConversationExecutionScope, message_id="message-1"):
    return ResolveConversationExecutionCommand(
        organization_id=scope.turn.organization_id,
        dispatch_id=scope.dispatch.id,
        turn_id=scope.turn.id,
        dispatch_claim_generation=scope.dispatch.claim_generation,
        broker_message_id=message_id,
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
    )


def _terminal_command(
    binding,
    *,
    admission_id,
    execution_id,
    attempt_id,
):
    node_invocation_id = uuid.uuid5(
        execution_id,
        f"conversation-node:{binding.llm_node_id}",
    )
    return RecoverConversationExecutionTerminalCommand(
        binding=binding,
        workflow_admission_id=admission_id,
        execution_id=execution_id,
        attempt_id=attempt_id,
        node_invocation_id=node_invocation_id,
        provider_attempt_id=uuid.uuid5(
            execution_id,
            f"provider_execution:{node_invocation_id}:main_generation",
        ),
    )


def test_resolve_admit_run_and_current_input_are_separate_fenced_steps() -> None:
    repository = _Repository(_scope())
    uow = _Uow(repository)
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(_resolve_command(repository.scope))
    admission_id = uuid.uuid4()

    queued = ObserveConversationExecutionAdmittedUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionAdmittedCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            claim_generation=1,
            broker_message_id="message-1",
        )
    )
    binding = replace(binding, turn_version=queued.turn_version)
    execution_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    running = ObserveConversationExecutionRunningUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionRunningCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
        )
    )
    binding = replace(binding, turn_version=running.turn_version)
    current = ReadCurrentTurnInputUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ReadCurrentTurnInputCommand(
            binding=binding,
            execution_id=execution_id,
            attempt_id=attempt_id,
        )
    )

    assert queued.turn_version == 2
    assert running.turn_version == 3
    assert current.entry_id == repository.entry.id
    assert current.model_content.ciphertext == b"ciphertext"


def test_reference_only_probe_returns_none_for_fresh_published_dispatch() -> None:
    repository = _Repository(_scope())
    repository.scope.dispatch.mark_published(
        owner="gateway",
        claim_generation=repository.scope.dispatch.claim_generation,
        broker_message_id="message-1",
        now=NOW,
    )
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(_resolve_command(repository.scope))
    admission_id = uuid.uuid5(
        binding.dispatch_id,
        "conversation-workflow-admission-v1",
    )

    result = ResolveTerminalConversationExecutionUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        ResolveTerminalConversationExecutionCommand(
            organization_id=binding.organization_id,
            dispatch_id=binding.dispatch_id,
            dispatch_claim_generation=binding.dispatch_claim_generation,
            broker_message_id=binding.broker_message_id,
            turn_id=binding.turn_id,
            memory_contract_version=binding.memory_contract_version,
            storage_generation=binding.storage_generation,
            minimum_worker_capability=binding.minimum_worker_capability,
            workflow_admission_id=admission_id,
            execution_id=uuid.uuid5(
                admission_id,
                "conversation-execution-v1",
            ),
            attempt_id=uuid.uuid4(),
        )
    )

    assert result is None


def test_reference_only_probe_allows_fresh_claimed_dispatch_before_publish_commit() -> None:
    repository = _Repository(_scope())
    assert repository.scope.dispatch.broker_message_id is None
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(_resolve_command(repository.scope))
    admission_id = uuid.uuid5(
        binding.dispatch_id,
        "conversation-workflow-admission-v1",
    )

    result = ResolveTerminalConversationExecutionUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        ResolveTerminalConversationExecutionCommand(
            organization_id=binding.organization_id,
            dispatch_id=binding.dispatch_id,
            dispatch_claim_generation=binding.dispatch_claim_generation,
            broker_message_id=binding.broker_message_id,
            turn_id=binding.turn_id,
            memory_contract_version=binding.memory_contract_version,
            storage_generation=binding.storage_generation,
            minimum_worker_capability=binding.minimum_worker_capability,
            workflow_admission_id=admission_id,
            execution_id=uuid.uuid5(
                admission_id,
                "conversation-execution-v1",
            ),
            attempt_id=uuid.uuid4(),
        )
    )

    assert result is None
    assert repository.scope.dispatch.status.value == "claimed"
    assert repository.scope.dispatch.broker_message_id is None


def test_stale_published_dispatch_is_acknowledged_and_failed_atomically() -> None:
    repository = _Repository(_scope())
    repository.scope.dispatch.mark_published(
        owner="gateway",
        claim_generation=repository.scope.dispatch.claim_generation,
        broker_message_id="message-1",
        now=NOW,
    )
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(_resolve_command(repository.scope))
    repository.scope.session.close(
        expected_lifecycle_revision=repository.scope.session.lifecycle_revision,
        now=NOW,
    )
    repository.scope.grant.revoke(now=NOW)
    admission_id = uuid.uuid5(
        binding.dispatch_id,
        "conversation-workflow-admission-v1",
    )
    execution_id = uuid.uuid5(admission_id, "conversation-execution-v1")
    attempt_id = uuid.uuid5(
        execution_id,
        "conversation-execution-attempt-v1",
    )
    resolve_command = ResolveTerminalConversationExecutionCommand(
        organization_id=binding.organization_id,
        dispatch_id=binding.dispatch_id,
        dispatch_claim_generation=binding.dispatch_claim_generation,
        broker_message_id=binding.broker_message_id,
        turn_id=binding.turn_id,
        memory_contract_version=binding.memory_contract_version,
        storage_generation=binding.storage_generation,
        minimum_worker_capability=binding.minimum_worker_capability,
        workflow_admission_id=admission_id,
        execution_id=execution_id,
        attempt_id=attempt_id,
    )
    pending = ResolveTerminalConversationExecutionUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(resolve_command)

    assert pending is not None
    assert pending.requires_dispatch_acknowledgement is True
    FinalizeReferenceConversationExecutionUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        FinalizeReferenceConversationExecutionCommand(
            binding=pending.binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
            safe_failure_reason="memory.runtime_authorization_stale",
            acknowledge_dispatch=True,
        )
    )

    assert repository.scope.dispatch.status.value == "acknowledged"
    assert repository.scope.turn.status is TurnStatus.FAILED
    assert repository.scope.turn.execution_id == execution_id
    assert repository.scope.turn.latest_attempt_id == attempt_id
    assert repository.entry.lifecycle is EntryLifecycle.REJECTED


def test_stale_claimed_dispatch_acks_trusted_task_id_after_publish_commit_race() -> None:
    repository = _Repository(_scope())
    assert repository.scope.dispatch.broker_message_id is None
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(_resolve_command(repository.scope))
    repository.scope.session.close(
        expected_lifecycle_revision=repository.scope.session.lifecycle_revision,
        now=NOW,
    )
    repository.scope.grant.revoke(now=NOW)
    admission_id = uuid.uuid5(
        binding.dispatch_id,
        "conversation-workflow-admission-v1",
    )
    execution_id = uuid.uuid5(admission_id, "conversation-execution-v1")
    attempt_id = uuid.uuid5(
        execution_id,
        "conversation-execution-attempt-v1",
    )
    pending = ResolveTerminalConversationExecutionUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        ResolveTerminalConversationExecutionCommand(
            organization_id=binding.organization_id,
            dispatch_id=binding.dispatch_id,
            dispatch_claim_generation=binding.dispatch_claim_generation,
            broker_message_id=binding.broker_message_id,
            turn_id=binding.turn_id,
            memory_contract_version=binding.memory_contract_version,
            storage_generation=binding.storage_generation,
            minimum_worker_capability=binding.minimum_worker_capability,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
        )
    )

    assert pending is not None
    assert pending.requires_dispatch_acknowledgement is True
    assert pending.binding.broker_message_id == "message-1"
    FinalizeReferenceConversationExecutionUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        FinalizeReferenceConversationExecutionCommand(
            binding=pending.binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
            safe_failure_reason="memory.runtime_authorization_stale",
            acknowledge_dispatch=True,
        )
    )

    assert repository.scope.dispatch.status.value == "acknowledged"
    assert repository.scope.dispatch.broker_message_id == "message-1"
    assert repository.scope.turn.status is TurnStatus.FAILED


def test_revoked_grant_blocks_redelivery_before_workflow_admission() -> None:
    repository = _Repository(_scope())
    repository.scope.grant.revoke(now=NOW)

    with pytest.raises(AccessGrantNotUsableError):
        ResolveConversationExecutionUseCase(
            repository=repository,
            uow=_Uow(repository),
        ).execute(_resolve_command(repository.scope))


def test_running_projection_hands_off_to_reclaimed_workflow_attempt() -> None:
    repository = _Repository(_scope())
    uow = _Uow(repository)
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(_resolve_command(repository.scope))
    admission_id = uuid.uuid4()
    queued = ObserveConversationExecutionAdmittedUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionAdmittedCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            claim_generation=1,
            broker_message_id="message-1",
        )
    )
    binding = replace(binding, turn_version=queued.turn_version)
    execution_id = uuid.uuid4()
    first_attempt_id = uuid.uuid4()
    running_use_case = ObserveConversationExecutionRunningUseCase(
        repository=repository,
        uow=uow,
    )
    first = running_use_case.execute(
        ObserveConversationExecutionRunningCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=first_attempt_id,
        )
    )
    first_binding = replace(binding, turn_version=first.turn_version)

    replay = running_use_case.execute(
        ObserveConversationExecutionRunningCommand(
            binding=first_binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=first_attempt_id,
        )
    )

    replacement_attempt_id = uuid.uuid4()
    replacement = running_use_case.execute(
        ObserveConversationExecutionRunningCommand(
            binding=first_binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=replacement_attempt_id,
        )
    )
    replacement_binding = replace(binding, turn_version=replacement.turn_version)
    assert replay.replayed is True
    assert replay.turn_version == first.turn_version
    assert replacement.replayed is False
    assert replacement.turn_version == first.turn_version + 1
    assert repository.scope.turn.execution_id == execution_id
    assert repository.scope.turn.latest_attempt_id == replacement_attempt_id
    assert repository.resolve_for_update_calls[-3:] == [True, True, True]

    with pytest.raises(StaleTurnVersionError):
        ReadCurrentTurnInputUseCase(
            repository=repository,
            uow=uow,
        ).execute(
            ReadCurrentTurnInputCommand(
                binding=replacement_binding,
                execution_id=execution_id,
                attempt_id=first_attempt_id,
            )
        )

    with pytest.raises(AccessGrantNotUsableError):
        running_use_case.execute(
            ObserveConversationExecutionRunningCommand(
                binding=first_binding,
                workflow_admission_id=admission_id,
                execution_id=execution_id,
                attempt_id=first_attempt_id,
            )
        )

    repository.scope.turn.complete(
        expected_version=replacement.turn_version,
        assistant_entry_id=uuid.uuid4(),
        now=NOW,
    )
    terminal_binding = replace(
        binding,
        turn_version=repository.scope.turn.version,
    )
    with pytest.raises(StaleTurnVersionError):
        running_use_case.execute(
            ObserveConversationExecutionRunningCommand(
                binding=terminal_binding,
                workflow_admission_id=admission_id,
                execution_id=execution_id,
                attempt_id=uuid.uuid4(),
            )
        )


@pytest.mark.parametrize(
    ("safe_failure_reason", "expected_outcome"),
    [
        ("provider_not_sent", "failed"),
        ("provider_outcome_unknown", "outcome_unknown"),
    ],
)
def test_terminal_projection_recovery_requires_current_execution_identity(
    safe_failure_reason: str,
    expected_outcome: str,
) -> None:
    repository = _Repository(_scope())
    uow = _Uow(repository)
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(_resolve_command(repository.scope))
    admission_id = uuid.uuid4()
    queued = ObserveConversationExecutionAdmittedUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionAdmittedCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            claim_generation=1,
            broker_message_id="message-1",
        )
    )
    binding = replace(binding, turn_version=queued.turn_version)
    execution_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    running = ObserveConversationExecutionRunningUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionRunningCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
        )
    )
    running_binding = replace(binding, turn_version=running.turn_version)
    repository.scope.turn.fail(
        expected_version=running.turn_version,
        safe_reason_code=safe_failure_reason,
        now=NOW,
    )
    repository.scope.session.release_turn(
        turn_id=repository.scope.turn.id,
        expected_lifecycle_revision=repository.scope.session.lifecycle_revision,
        content_changed=False,
        now=NOW,
    )
    terminal_binding = running_binding
    use_case = RecoverConversationExecutionTerminalUseCase(
        repository=repository,
        uow=uow,
    )

    projection = use_case.execute(
        _terminal_command(
            terminal_binding,
            admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
        )
    )

    assert projection is not None
    assert projection.outcome == expected_outcome
    assert projection.safe_failure_reason == safe_failure_reason
    assert repository.resolve_for_update_calls[-1] is True

    with pytest.raises(StaleTurnVersionError):
        use_case.execute(
            _terminal_command(
                terminal_binding,
                admission_id=admission_id,
                execution_id=uuid.uuid4(),
                attempt_id=attempt_id,
            )
        )

    with pytest.raises(DispatchStateConflictError):
        use_case.execute(
            _terminal_command(
                terminal_binding,
                admission_id=uuid.uuid4(),
                execution_id=execution_id,
                attempt_id=attempt_id,
            )
        )

    repository.scope.grant.revoke(now=NOW)
    with pytest.raises(AccessGrantNotUsableError):
        use_case.execute(
            _terminal_command(
                terminal_binding,
                admission_id=admission_id,
                execution_id=execution_id,
                attempt_id=attempt_id,
            )
        )


def test_terminal_context_attempt_atomically_fails_running_turn_on_redelivery() -> None:
    repository = _Repository(_scope())
    uow = _Uow(repository)
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(_resolve_command(repository.scope))
    admission_id = uuid.uuid4()
    queued = ObserveConversationExecutionAdmittedUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionAdmittedCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            claim_generation=1,
            broker_message_id="message-1",
        )
    )
    binding = replace(binding, turn_version=queued.turn_version)
    execution_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    running = ObserveConversationExecutionRunningUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionRunningCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
        )
    )
    running_binding = replace(binding, turn_version=running.turn_version)
    command = _terminal_command(
        running_binding,
        admission_id=admission_id,
        execution_id=execution_id,
        attempt_id=attempt_id,
    )
    repository.context_attempt = MemoryContextProviderAttempt(
        id=command.provider_attempt_id,
        organization_id=running_binding.organization_id,
        session_id=running_binding.session_id,
        turn_id=running_binding.turn_id,
        lease_id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        node_invocation_id=command.node_invocation_id,
        provider_capability_reference="capability-1",
        provider_capability_revision="3",
        status=ContextAttemptState.OUTCOME_UNKNOWN,
        version=3,
        claim_generation=1,
        claim_deadline_at=NOW + timedelta(minutes=1),
        provider_started_at=NOW,
        usage_reference="usage-operation-1",
        safe_failure_reason="provider_outcome_unknown",
        terminal_at=NOW,
    )

    projection = RecoverConversationExecutionTerminalUseCase(
        repository=repository,
        uow=uow,
    ).execute(command)

    assert projection is not None
    assert projection.outcome == "outcome_unknown"
    assert repository.scope.turn.status is TurnStatus.FAILED
    assert repository.scope.turn.version == running.turn_version + 1
    assert repository.scope.session.active_turn_id is None
    assert repository.entry.lifecycle is EntryLifecycle.REJECTED
    assert repository.resolve_for_update_calls[-1] is True


def test_reference_only_completed_projection_preserves_exact_result_identity() -> None:
    repository = _Repository(_scope())
    uow = _Uow(repository)
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(_resolve_command(repository.scope))
    admission_id = uuid.uuid5(
        binding.dispatch_id,
        "conversation-workflow-admission-v1",
    )
    execution_id = uuid.uuid5(admission_id, "conversation-execution-v1")
    attempt_id = uuid.uuid5(
        execution_id,
        "conversation-execution-attempt-v1",
    )
    queued = ObserveConversationExecutionAdmittedUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionAdmittedCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            claim_generation=1,
            broker_message_id="message-1",
        )
    )
    running_binding = replace(binding, turn_version=queued.turn_version)
    running = ObserveConversationExecutionRunningUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionRunningCommand(
            binding=running_binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
        )
    )
    assistant = ConversationMemoryEntry.provisional_assistant(
        entry_id=uuid.uuid4(),
        organization_id=binding.organization_id,
        session_id=binding.session_id,
        turn_id=binding.turn_id,
        sequence=2,
        channel="conversation",
        content=repository.entry.content,
        idempotency_key_hash="e" * 64,
        now=NOW,
    )
    repository.entries[assistant.id] = assistant
    repository.scope.turn.complete(
        expected_version=running.turn_version,
        assistant_entry_id=assistant.id,
        now=NOW,
    )
    repository.scope.session.release_turn(
        turn_id=binding.turn_id,
        expected_lifecycle_revision=repository.scope.session.lifecycle_revision,
        content_changed=True,
        now=NOW,
    )
    repository.entry.approve(
        content_revision=repository.scope.session.content_revision,
        now=NOW,
    )
    assistant.approve(
        content_revision=repository.scope.session.content_revision,
        now=NOW,
    )
    repository.scope.session.close(
        expected_lifecycle_revision=repository.scope.session.lifecycle_revision,
        now=NOW,
    )
    repository.scope.grant.revoke(now=NOW)

    recovered = ResolveTerminalConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ResolveTerminalConversationExecutionCommand(
            organization_id=binding.organization_id,
            dispatch_id=binding.dispatch_id,
            dispatch_claim_generation=binding.dispatch_claim_generation,
            broker_message_id=binding.broker_message_id,
            turn_id=binding.turn_id,
            memory_contract_version=binding.memory_contract_version,
            storage_generation=binding.storage_generation,
            minimum_worker_capability=binding.minimum_worker_capability,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
        )
    )

    assert recovered is not None
    assert recovered.binding.turn_version + 1 == repository.scope.turn.version
    assert recovered.projection.outcome == "completed"
    assert recovered.projection.result_entry_id == assistant.id
    assert recovered.projection.result_digest is not None
    assert len(recovered.projection.result_digest) == 64
    assert recovered.projection.safe_failure_reason is None


def test_queued_reference_failure_split_replays_with_deterministic_execution_identity() -> None:
    repository = _Repository(_scope())
    uow = _Uow(repository)
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(_resolve_command(repository.scope))
    admission_id = uuid.uuid5(
        binding.dispatch_id,
        "conversation-workflow-admission-v1",
    )
    execution_id = uuid.uuid5(admission_id, "conversation-execution-v1")
    attempt_id = uuid.uuid5(
        execution_id,
        "conversation-execution-attempt-v1",
    )
    ObserveConversationExecutionAdmittedUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionAdmittedCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            claim_generation=1,
            broker_message_id="message-1",
        )
    )
    repository.scope.session.close(
        expected_lifecycle_revision=repository.scope.session.lifecycle_revision,
        now=NOW,
    )
    repository.scope.grant.revoke(now=NOW)
    reference_command = ResolveTerminalConversationExecutionCommand(
        organization_id=binding.organization_id,
        dispatch_id=binding.dispatch_id,
        dispatch_claim_generation=binding.dispatch_claim_generation,
        broker_message_id=binding.broker_message_id,
        turn_id=binding.turn_id,
        memory_contract_version=binding.memory_contract_version,
        storage_generation=binding.storage_generation,
        minimum_worker_capability=binding.minimum_worker_capability,
        workflow_admission_id=admission_id,
        execution_id=execution_id,
        attempt_id=attempt_id,
    )
    pending = ResolveTerminalConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(reference_command)

    assert pending is not None
    assert pending.requires_memory_failure is True
    FinalizeReferenceConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        FinalizeReferenceConversationExecutionCommand(
            binding=pending.binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
            safe_failure_reason="memory.runtime_authorization_stale",
        )
    )
    replay = ResolveTerminalConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(reference_command)

    assert repository.scope.turn.status is TurnStatus.FAILED
    assert repository.scope.turn.execution_id == execution_id
    assert repository.scope.turn.latest_attempt_id == attempt_id
    assert replay is not None
    assert replay.requires_memory_failure is False
    assert replay.projection.outcome == "failed"
    assert replay.projection.safe_failure_reason == (
        "memory.runtime_authorization_stale"
    )


def test_reference_failure_preserves_unknown_usage_and_rejects_late_checkpoint() -> None:
    repository = _Repository(_scope())
    uow = _Uow(repository)
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(_resolve_command(repository.scope))
    admission_id = uuid.uuid5(
        binding.dispatch_id,
        "conversation-workflow-admission-v1",
    )
    execution_id = uuid.uuid5(admission_id, "conversation-execution-v1")
    attempt_id = uuid.uuid5(
        execution_id,
        "conversation-execution-attempt-v1",
    )
    queued = ObserveConversationExecutionAdmittedUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionAdmittedCommand(
            binding=binding,
            workflow_admission_id=admission_id,
            claim_generation=1,
            broker_message_id="message-1",
        )
    )
    running_binding = replace(binding, turn_version=queued.turn_version)
    running = ObserveConversationExecutionRunningUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionRunningCommand(
            binding=running_binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
        )
    )
    running_binding = replace(
        running_binding,
        turn_version=running.turn_version,
    )
    recovery_identity = _terminal_command(
        running_binding,
        admission_id=admission_id,
        execution_id=execution_id,
        attempt_id=attempt_id,
    )
    repository.context_attempt = MemoryContextProviderAttempt(
        id=recovery_identity.provider_attempt_id,
        organization_id=binding.organization_id,
        session_id=binding.session_id,
        turn_id=binding.turn_id,
        lease_id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        node_invocation_id=recovery_identity.node_invocation_id,
        provider_capability_reference="capability-1",
        provider_capability_revision="3",
        status=ContextAttemptState.PROVIDER_STARTED,
        version=2,
        claim_generation=1,
        claim_deadline_at=NOW + timedelta(minutes=1),
        provider_started_at=NOW,
        usage_reference="usage-operation-1",
    )
    checkpoint = ConversationMemoryEntry.provisional_assistant(
        entry_id=uuid.uuid5(
            execution_id,
            "conversation-assistant-checkpoint-v1",
        ),
        organization_id=binding.organization_id,
        session_id=binding.session_id,
        turn_id=binding.turn_id,
        sequence=2,
        channel="conversation",
        content=repository.entry.content,
        idempotency_key_hash="e" * 64,
        now=NOW,
    )
    repository.entries[checkpoint.id] = checkpoint
    repository.scope.session.close(
        expected_lifecycle_revision=repository.scope.session.lifecycle_revision,
        now=NOW,
    )
    repository.scope.grant.revoke(now=NOW)

    pending = ResolveTerminalConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ResolveTerminalConversationExecutionCommand(
            organization_id=binding.organization_id,
            dispatch_id=binding.dispatch_id,
            dispatch_claim_generation=binding.dispatch_claim_generation,
            broker_message_id=binding.broker_message_id,
            turn_id=binding.turn_id,
            memory_contract_version=binding.memory_contract_version,
            storage_generation=binding.storage_generation,
            minimum_worker_capability=binding.minimum_worker_capability,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
        )
    )

    assert pending is not None
    assert pending.projection.provider_attempt_id == (
        recovery_identity.provider_attempt_id
    )
    assert pending.projection.usage_reference == "usage-operation-1"
    projection = FinalizeReferenceConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        FinalizeReferenceConversationExecutionCommand(
            binding=pending.binding,
            workflow_admission_id=admission_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
            safe_failure_reason="provider_outcome_unknown",
            provider_attempt_id=recovery_identity.provider_attempt_id,
            context_outcome="outcome_unknown",
            execution_outcome="outcome_unknown",
        )
    )

    assert projection.outcome == "outcome_unknown"
    assert repository.context_attempt.status is ContextAttemptState.OUTCOME_UNKNOWN
    assert repository.context_attempt.safe_failure_reason == (
        "provider_outcome_unknown"
    )
    assert checkpoint.lifecycle is EntryLifecycle.REJECTED
    assert repository.entry.lifecycle is EntryLifecycle.REJECTED
    assert repository.scope.turn.status is TurnStatus.FAILED
    assert repository.scope.turn.safe_failure_reason == (
        "provider_outcome_unknown"
    )
