from __future__ import annotations

import copy
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from apps.memory.application.context import (
    BuildMemoryContextCommand,
    BuildMemoryContextUseCase,
    ClaimMemoryContextCommand,
    ClaimMemoryContextUseCase,
    ContextAttemptState,
    ContextCandidatePair,
    ContextEntryReference,
    FinishContextProviderAttemptCommand,
    FinishContextProviderAttemptUseCase,
    MarkContextProviderStartedCommand,
    MarkContextProviderStartedUseCase,
)
from apps.memory.application.execution import (
    ConversationExecutionScope,
    ObserveConversationExecutionAdmittedCommand,
    ObserveConversationExecutionAdmittedUseCase,
    ObserveConversationExecutionRunningCommand,
    ObserveConversationExecutionRunningUseCase,
    ResolveConversationExecutionCommand,
    ResolveConversationExecutionUseCase,
)
from apps.memory.application.public_lifecycle import PublicDeploymentBinding
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationMemoryEntry,
    ConversationSession,
    ConversationTurn,
    EntryType,
    MemoryTurnDispatchJob,
    ProtectedContent,
    ProtectedEntryContent,
    RequestIdentity,
    TurnStatus,
)
from apps.memory.domain.errors import (
    MemoryContextConflictError,
    MemoryContextUnavailableError,
)
from apps.memory.domain.public_access import ConversationAccessGrant


NOW = datetime(2026, 7, 22, 15, tzinfo=timezone.utc)


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
        deployment_version=4,
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
    session.content_revision = 4
    grant = ConversationAccessGrant.issue(
        grant_id=uuid.uuid4(),
        organization_id=organization_id,
        session_id=session.id,
        deployment_id=deployment_id,
        deployment_version=4,
        audience_kind=AudienceKind.PUBLIC_CHATBOT,
        verifier_hash="a" * 64,
        verifier_key_version="grant-v1",
        expires_at=NOW + timedelta(hours=1),
        now=NOW,
    )
    turn_id = uuid.uuid4()
    dispatch_id = uuid.uuid4()
    session.claim_turn(
        turn_id=turn_id,
        expected_lifecycle_revision=1,
        now=NOW,
    )
    turn = ConversationTurn.start(
        turn_id=turn_id,
        organization_id=organization_id,
        session_id=session.id,
        sequence=3,
        started_lifecycle_revision=1,
        request_identity=RequestIdentity("b" * 64, "c" * 64),
        user_entry_id=uuid.uuid4(),
        dispatch_id=dispatch_id,
        access_grant_id=grant.id,
        request_fingerprint_key_version="admission-v1",
        now=NOW,
    )
    dispatch = MemoryTurnDispatchJob.pending(
        dispatch_id=dispatch_id,
        organization_id=organization_id,
        session_id=session.id,
        turn_id=turn.id,
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
            deployment_version=4,
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
            runtime_max_turns=2,
            runtime_max_context_tokens=10_000,
        ),
        grant=grant,
        session=session,
        turn=turn,
        dispatch=dispatch,
    )


class _Repository:
    def __init__(self) -> None:
        self.scope = _scope()
        self.candidates: tuple[ContextCandidatePair, ...] = ()
        self.entries: dict[uuid.UUID, ConversationMemoryEntry] = {}
        self.plans = {}
        self.leases = {}
        self.attempts = {}

    def current_time(self):
        return NOW

    def resolve_execution_scope(self, _command, *, for_update):
        return self.scope

    def save_session(self, session):
        self.scope = replace(self.scope, session=session)

    def save_turn(self, turn):
        self.scope = replace(self.scope, turn=turn)

    def save_dispatch_job(self, dispatch):
        self.scope = replace(self.scope, dispatch=dispatch)

    def get_entry(self, **kwargs):
        return self.entries.get(kwargs["entry_id"])

    def list_prior_context_candidates(self, **kwargs):
        assert kwargs["organization_id"] == self.scope.session.organization_id
        assert kwargs["session_id"] == self.scope.session.id
        assert kwargs["before_turn_sequence"] == 3
        return self.candidates[: kwargs["limit"]]

    def find_context_plan(self, plan_id):
        return self.plans.get(plan_id)

    def add_context_plan(self, plan):
        self.plans[plan.id] = plan

    def find_context_lease(self, lease_id):
        return self.leases.get(lease_id)

    def lock_context_lease(self, lease_id):
        return self.leases.get(lease_id)

    def add_context_lease(self, lease):
        self.leases[lease.id] = lease

    def save_context_lease(self, lease):
        self.leases[lease.id] = lease

    def get_context_entry(self, reference):
        entry = self.entries.get(reference.entry_id)
        if entry is None or entry.turn_id != reference.turn_id:
            return None
        return entry

    def find_context_attempt(self, attempt_id):
        return self.attempts.get(attempt_id)

    def lock_context_attempt(self, attempt_id):
        return self.attempts.get(attempt_id)

    def add_context_attempt(self, attempt):
        self.attempts[attempt.id] = attempt

    def save_context_attempt(self, attempt):
        self.attempts[attempt.id] = attempt


class _Uow:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.snapshot = None

    def begin(self):
        self.snapshot = copy.deepcopy(
            (
                self.repository.scope,
                self.repository.plans,
                self.repository.leases,
                self.repository.attempts,
            )
        )

    def commit(self):
        self.snapshot = None

    def rollback(self):
        (
            self.repository.scope,
            self.repository.plans,
            self.repository.leases,
            self.repository.attempts,
        ) = self.snapshot
        self.snapshot = None


class _Cipher:
    def __init__(self, values: dict[bytes, str]) -> None:
        self.values = values

    def reveal(self, protected, *, associated_data):
        assert associated_data.startswith("memory-content-v1:")
        return self.values.get(protected.ciphertext)


class _TokenCounter:
    def __init__(self, *, limit_at: str | None = None) -> None:
        self.limit_at = limit_at

    def count(self, value):
        return 20_000 if self.limit_at and self.limit_at in value else len(value)


def _approved_entry(
    repository: _Repository,
    *,
    turn_id: uuid.UUID,
    sequence: int,
    entry_type: EntryType,
    value: str,
) -> ContextEntryReference:
    entry_id = uuid.uuid4()
    protected = ProtectedContent(
        ciphertext=value.encode("utf-8"),
        key_version="content-v1",
        format_version="memory-content-fernet-v1",
        content_digest=("d" if entry_type is EntryType.USER_TURN else "e") * 64,
        plaintext_byte_length=len(value.encode("utf-8")),
    )
    content = ProtectedEntryContent(display=protected, model=protected)
    if entry_type is EntryType.USER_TURN:
        entry = ConversationMemoryEntry.provisional_user(
            entry_id=entry_id,
            organization_id=repository.scope.session.organization_id,
            session_id=repository.scope.session.id,
            turn_id=turn_id,
            sequence=sequence,
            channel="conversation",
            content=content,
            idempotency_key_hash="f" * 64,
            now=NOW,
        )
        entry.approve(content_revision=sequence, now=NOW)
    else:
        entry = ConversationMemoryEntry.approved_assistant(
            entry_id=entry_id,
            organization_id=repository.scope.session.organization_id,
            session_id=repository.scope.session.id,
            turn_id=turn_id,
            sequence=sequence,
            channel="conversation",
            content=content,
            content_revision=sequence,
            idempotency_key_hash="f" * 64,
            now=NOW,
        )
    repository.entries[entry.id] = entry
    return ContextEntryReference(
        entry_id=entry.id,
        turn_id=turn_id,
        sequence=sequence,
        entry_type=entry_type,
        content_revision=sequence,
        content_digest=protected.content_digest,
        dependency_proof_version="conversation-source-free-v1",
    )


def _pair(repository: _Repository, sequence: int, user: str, assistant: str):
    turn_id = uuid.uuid4()
    return ContextCandidatePair(
        turn_id=turn_id,
        turn_sequence=sequence,
        status=TurnStatus.COMPLETED,
        user=_approved_entry(
            repository,
            turn_id=turn_id,
            sequence=sequence * 2 - 1,
            entry_type=EntryType.USER_TURN,
            value=user,
        ),
        assistant=_approved_entry(
            repository,
            turn_id=turn_id,
            sequence=sequence * 2,
            entry_type=EntryType.ASSISTANT_TURN,
            value=assistant,
        ),
        dependency_count=0,
    )


def _running_binding(repository: _Repository, uow: _Uow):
    scope = repository.scope
    binding = ResolveConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ResolveConversationExecutionCommand(
            organization_id=scope.turn.organization_id,
            dispatch_id=scope.dispatch.id,
            dispatch_claim_generation=scope.dispatch.claim_generation,
            broker_message_id="message-1",
            turn_id=scope.turn.id,
            memory_contract_version="conversation-memory-v1",
            storage_generation=1,
            minimum_worker_capability="memory-runtime-v1",
        )
    )
    admitted = ObserveConversationExecutionAdmittedUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionAdmittedCommand(
            binding=binding,
            workflow_admission_id=uuid.uuid4(),
            claim_generation=1,
            broker_message_id="message-1",
        )
    )
    binding = replace(binding, turn_version=admitted.turn_version)
    running = ObserveConversationExecutionRunningUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        ObserveConversationExecutionRunningCommand(
            binding=binding,
            workflow_admission_id=uuid.UUID(
                repository.scope.dispatch.workflow_admission_reference
            ),
            execution_id=uuid.uuid4(),
            attempt_id=uuid.uuid4(),
        )
    )
    return replace(binding, turn_version=running.turn_version)


def _build(
    repository: _Repository,
    binding,
    provider_attempt_id,
    *,
    expires_at=NOW + timedelta(minutes=2),
):
    return BuildMemoryContextUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        BuildMemoryContextCommand(
            binding=binding,
            node_invocation_id=uuid.uuid5(provider_attempt_id, "llm-node"),
            provider_capability_reference="capability-1",
            provider_capability_revision="capability-v1",
            provider_attempt_id=provider_attempt_id,
            expires_at=expires_at,
            now=NOW,
        )
    )


def _claim(
    repository,
    binding,
    built,
    provider_attempt_id,
    *,
    token_counter=None,
    claim_deadline_at=NOW + timedelta(minutes=1),
    now=NOW,
):
    return ClaimMemoryContextUseCase(
        repository=repository,
        uow=_Uow(repository),
        content_cipher=_Cipher(
            {entry.content.model.ciphertext: entry.content.model.ciphertext.decode()
             for entry in repository.entries.values()}
        ),
        token_counter=token_counter or _TokenCounter(),
    ).execute(
        ClaimMemoryContextCommand(
            binding=binding,
            plan_id=built.plan_id,
            lease_id=built.lease_id,
            node_invocation_id=uuid.uuid5(provider_attempt_id, "llm-node"),
            provider_capability_reference="capability-1",
            provider_capability_revision="capability-v1",
            provider_attempt_id=provider_attempt_id,
            claim_deadline_at=claim_deadline_at,
            now=now,
        )
    )


def test_build_is_reference_only_and_does_not_preclaim_provider_attempt() -> None:
    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    repository.candidates = (
        _pair(repository, 2, "new-user", "new-answer"),
        _pair(repository, 1, "old-user", "old-answer"),
    )
    provider_attempt_id = uuid.uuid4()

    result = _build(repository, binding, provider_attempt_id)

    plan = repository.plans[result.plan_id]
    lease = repository.leases[result.lease_id]
    assert result.candidate_pair_count == 2
    assert not hasattr(plan, "history_block")
    assert all(reference.user.content_digest for reference in plan.ordered_pairs)
    assert lease.provider_attempt_id is None
    assert lease.claim_generation == 0


def test_claim_materializes_chronological_history_and_is_same_attempt_idempotent() -> None:
    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    repository.candidates = (
        _pair(repository, 2, "new-user", "new-answer"),
        _pair(repository, 1, "old-user", "old-answer"),
    )
    provider_attempt_id = uuid.uuid4()
    built = _build(repository, binding, provider_attempt_id)

    first = _claim(repository, binding, built, provider_attempt_id)
    replay = _claim(repository, binding, built, provider_attempt_id)

    assert first.selected_pair_count == 2
    assert first.history_block.index("old-user") < first.history_block.index("new-user")
    assert "current" not in first.history_block
    assert replay.attempt_id == first.attempt_id
    assert replay.replayed is True
    assert repository.leases[built.lease_id].claim_generation == 1

    with pytest.raises(MemoryContextConflictError):
        _claim(repository, binding, built, uuid.uuid4())


def test_expired_claimed_attempt_reclaims_with_a_new_generation_and_deadline() -> None:
    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    repository.candidates = (_pair(repository, 1, "user", "answer"),)
    provider_attempt_id = uuid.uuid4()
    built = _build(
        repository,
        binding,
        provider_attempt_id,
        expires_at=NOW + timedelta(minutes=5),
    )
    first_deadline = NOW + timedelta(seconds=20)
    first = _claim(
        repository,
        binding,
        built,
        provider_attempt_id,
        claim_deadline_at=first_deadline,
    )
    recovery_now = NOW + timedelta(seconds=211)
    recovery_deadline = recovery_now + timedelta(seconds=20)

    recovered = _claim(
        repository,
        binding,
        built,
        provider_attempt_id,
        claim_deadline_at=recovery_deadline,
        now=recovery_now,
    )

    lease = repository.leases[built.lease_id]
    attempt = repository.attempts[provider_attempt_id]
    assert recovered.attempt_id == first.attempt_id
    assert recovered.attempt_version == first.attempt_version + 1
    assert recovered.replayed is True
    assert lease.claim_generation == 2
    assert lease.claim_deadline_at == recovery_deadline
    assert attempt.claim_generation == 2
    assert attempt.claim_deadline_at == recovery_deadline
    assert MarkContextProviderStartedUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        MarkContextProviderStartedCommand(
            organization_id=binding.organization_id,
            attempt_id=provider_attempt_id,
            expected_version=recovered.attempt_version,
            usage_reference="usage-operation-recovered",
            now=recovery_now + timedelta(seconds=1),
        )
    ) == recovered.attempt_version + 1


def test_expired_provider_started_attempt_only_reenters_same_usage_callback() -> None:
    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    repository.candidates = (_pair(repository, 1, "user", "answer"),)
    provider_attempt_id = uuid.uuid4()
    built = _build(
        repository,
        binding,
        provider_attempt_id,
        expires_at=NOW + timedelta(minutes=5),
    )
    first_deadline = NOW + timedelta(seconds=20)
    claimed = _claim(
        repository,
        binding,
        built,
        provider_attempt_id,
        claim_deadline_at=first_deadline,
    )
    started_version = MarkContextProviderStartedUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        MarkContextProviderStartedCommand(
            organization_id=binding.organization_id,
            attempt_id=provider_attempt_id,
            expected_version=claimed.attempt_version,
            usage_reference="usage-operation-1",
            now=NOW + timedelta(seconds=1),
        )
    )
    prior_attempt = copy.deepcopy(repository.attempts[provider_attempt_id])

    recovery_now = NOW + timedelta(seconds=211)
    recovered = _claim(
        repository,
        binding,
        built,
        provider_attempt_id,
        claim_deadline_at=recovery_now + timedelta(seconds=20),
        now=recovery_now,
    )

    lease = repository.leases[built.lease_id]
    assert recovered.attempt_id == provider_attempt_id
    assert recovered.attempt_version == started_version
    assert recovered.replayed is True
    assert lease.claim_generation == 1
    assert lease.claim_deadline_at == first_deadline
    assert repository.attempts[provider_attempt_id] == prior_attempt
    marker = MarkContextProviderStartedUseCase(
        repository=repository,
        uow=_Uow(repository),
    )
    assert marker.execute(
        MarkContextProviderStartedCommand(
            organization_id=binding.organization_id,
            attempt_id=provider_attempt_id,
            expected_version=recovered.attempt_version,
            usage_reference="usage-operation-1",
            now=recovery_now,
        )
    ) == started_version
    with pytest.raises(MemoryContextConflictError):
        marker.execute(
            MarkContextProviderStartedCommand(
                organization_id=binding.organization_id,
                attempt_id=provider_attempt_id,
                expected_version=recovered.attempt_version,
                usage_reference="different-usage",
                now=recovery_now,
            )
        )


def test_expired_terminal_attempt_cannot_reclaim_send_authority() -> None:
    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    repository.candidates = (_pair(repository, 1, "user", "answer"),)
    provider_attempt_id = uuid.uuid4()
    built = _build(
        repository,
        binding,
        provider_attempt_id,
        expires_at=NOW + timedelta(minutes=5),
    )
    first_deadline = NOW + timedelta(seconds=20)
    claimed = _claim(
        repository,
        binding,
        built,
        provider_attempt_id,
        claim_deadline_at=first_deadline,
    )
    started_version = MarkContextProviderStartedUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        MarkContextProviderStartedCommand(
            organization_id=binding.organization_id,
            attempt_id=provider_attempt_id,
            expected_version=claimed.attempt_version,
            usage_reference="usage-operation-1",
            now=NOW + timedelta(seconds=1),
        )
    )
    FinishContextProviderAttemptUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        FinishContextProviderAttemptCommand(
            organization_id=binding.organization_id,
            attempt_id=provider_attempt_id,
            expected_version=started_version,
            outcome=ContextAttemptState.SUCCEEDED,
            safe_failure_reason=None,
            now=NOW + timedelta(seconds=2),
        )
    )
    prior_attempt = copy.deepcopy(repository.attempts[provider_attempt_id])

    recovery_now = NOW + timedelta(seconds=211)
    with pytest.raises(MemoryContextConflictError):
        _claim(
            repository,
            binding,
            built,
            provider_attempt_id,
            claim_deadline_at=recovery_now + timedelta(seconds=20),
            now=recovery_now,
        )

    lease = repository.leases[built.lease_id]
    assert lease.claim_generation == 1
    assert lease.claim_deadline_at == first_deadline
    assert repository.attempts[provider_attempt_id] == prior_attempt


@pytest.mark.parametrize(
    "stale_fence",
    ["authorization", "execution"],
)
def test_expired_claim_recovery_rechecks_current_authorization_and_execution(
    stale_fence: str,
) -> None:
    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    repository.candidates = (_pair(repository, 1, "user", "answer"),)
    provider_attempt_id = uuid.uuid4()
    built = _build(
        repository,
        binding,
        provider_attempt_id,
        expires_at=NOW + timedelta(minutes=5),
    )
    first_deadline = NOW + timedelta(seconds=20)
    _claim(
        repository,
        binding,
        built,
        provider_attempt_id,
        claim_deadline_at=first_deadline,
    )
    recovery_now = NOW + timedelta(seconds=211)
    if stale_fence == "authorization":
        repository.scope.grant.expires_at = recovery_now
    else:
        repository.scope.turn.status = TurnStatus.COMPLETED

    with pytest.raises(MemoryContextUnavailableError):
        _claim(
            repository,
            binding,
            built,
            provider_attempt_id,
            claim_deadline_at=recovery_now + timedelta(seconds=20),
            now=recovery_now,
        )

    lease = repository.leases[built.lease_id]
    assert lease.claim_generation == 1
    assert lease.claim_deadline_at == first_deadline


def test_newest_first_barrier_never_skips_an_invalid_or_oversized_pair() -> None:
    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    newest = _pair(repository, 2, "new-user", "new-answer")
    older = _pair(repository, 1, "old-user", "old-answer")
    repository.candidates = (newest, older)
    repository.entries.pop(newest.assistant.entry_id)
    attempt_id = uuid.uuid4()
    built = _build(repository, binding, attempt_id)

    with pytest.raises(MemoryContextUnavailableError):
        _claim(repository, binding, built, attempt_id)

    assert repository.attempts == {}


@pytest.mark.parametrize(
    "mutation",
    ["cross_organization", "invalidated", "expired"],
)
def test_claim_revalidates_candidate_scope_and_lifecycle_after_build(
    mutation: str,
) -> None:
    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    pair = _pair(repository, 1, "user", "answer")
    repository.candidates = (pair,)
    attempt_id = uuid.uuid4()
    built = _build(repository, binding, attempt_id)
    entry = repository.entries[pair.user.entry_id]
    if mutation == "cross_organization":
        entry.organization_id = uuid.uuid4()
    elif mutation == "invalidated":
        entry.invalidated_at = NOW
    else:
        entry.expires_at = NOW

    with pytest.raises(MemoryContextUnavailableError):
        _claim(repository, binding, built, attempt_id)

    assert repository.attempts == {}


def test_legacy_entry_without_explicit_source_free_proof_is_not_context() -> None:
    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    pair = _pair(repository, 1, "user", "answer")
    pair = replace(
        pair,
        user=replace(pair.user, dependency_proof_version=None),
    )
    repository.candidates = (pair,)

    with pytest.raises(MemoryContextUnavailableError):
        _build(repository, binding, uuid.uuid4())

    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    repository.candidates = (
        _pair(repository, 2, "oversized-new", "new-answer"),
        _pair(repository, 1, "old-user", "old-answer"),
    )
    attempt_id = uuid.uuid4()
    built = _build(repository, binding, attempt_id)
    with pytest.raises(MemoryContextUnavailableError):
        _claim(
            repository,
            binding,
            built,
            attempt_id,
            token_counter=_TokenCounter(limit_at="oversized-new"),
        )
    assert repository.attempts == {}


def test_provider_markers_are_versioned_idempotent_and_conflict_safe() -> None:
    repository = _Repository()
    binding = _running_binding(repository, _Uow(repository))
    repository.candidates = (_pair(repository, 1, "user", "answer"),)
    attempt_id = uuid.uuid4()
    built = _build(repository, binding, attempt_id)
    claimed = _claim(repository, binding, built, attempt_id)
    marker = MarkContextProviderStartedUseCase(
        repository=repository,
        uow=_Uow(repository),
    )

    version = marker.execute(
        MarkContextProviderStartedCommand(
            organization_id=binding.organization_id,
            attempt_id=attempt_id,
            expected_version=claimed.attempt_version,
            usage_reference="usage-operation-1",
            now=NOW,
        )
    )
    replay_version = marker.execute(
        MarkContextProviderStartedCommand(
            organization_id=binding.organization_id,
            attempt_id=attempt_id,
            expected_version=claimed.attempt_version,
            usage_reference="usage-operation-1",
            now=NOW,
        )
    )
    assert replay_version == version

    with pytest.raises(MemoryContextConflictError):
        marker.execute(
            MarkContextProviderStartedCommand(
                organization_id=binding.organization_id,
                attempt_id=attempt_id,
                expected_version=version,
                usage_reference="different-usage",
                now=NOW,
            )
        )

    terminal_version = FinishContextProviderAttemptUseCase(
        repository=repository,
        uow=_Uow(repository),
    ).execute(
        FinishContextProviderAttemptCommand(
            organization_id=binding.organization_id,
            attempt_id=attempt_id,
            expected_version=version,
            outcome=ContextAttemptState.SUCCEEDED,
            safe_failure_reason=None,
            now=NOW,
        )
    )
    assert terminal_version == version + 1
    assert repository.attempts[attempt_id].status is ContextAttemptState.SUCCEEDED
