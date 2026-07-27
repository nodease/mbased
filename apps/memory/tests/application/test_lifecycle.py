from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pytest

from apps.memory.application.lifecycle import (
    CloseSessionCommand,
    CloseSessionUseCase,
    CompleteTurnCommand,
    CompleteTurnUseCase,
    CreateSessionCommand,
    CreateSessionUseCase,
    RequestDeleteCommand,
    RequestDeleteUseCase,
    StartTurnCommand,
    StartTurnUseCase,
)
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationMemoryEntry,
    ConversationSession,
    ConversationTurn,
    MemoryTurnDispatchJob,
    ProtectedContent,
    ProtectedEntryContent,
    SessionLifecycle,
)
from apps.memory.domain.errors import (
    DuplicateRequestConflictError,
    SessionNotActiveError,
    StaleLifecycleRevisionError,
    StaleTurnVersionError,
)


def _now() -> datetime:
    return datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def _protected(seed: bytes = b"user") -> ProtectedEntryContent:
    digest_seed = "a" if seed == b"user" else "b"
    return ProtectedEntryContent(
        display=ProtectedContent(
            ciphertext=b"encrypted-display-" + seed,
            key_version="display-key-v1",
            format_version="memory-envelope-v1",
            content_digest=digest_seed * 64,
            plaintext_byte_length=64,
        ),
        model=ProtectedContent(
            ciphertext=b"encrypted-model-" + seed,
            key_version="model-key-v1",
            format_version="memory-envelope-v1",
            content_digest=("c" if seed == b"user" else "d") * 64,
            plaintext_byte_length=72,
        ),
    )


@dataclass
class _PurgeJob:
    id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID


class _Repository:
    def __init__(self) -> None:
        self.sessions: dict[uuid.UUID, ConversationSession] = {}
        self.turns: dict[uuid.UUID, ConversationTurn] = {}
        self.entries: dict[uuid.UUID, ConversationMemoryEntry] = {}
        self.dispatch_jobs: dict[uuid.UUID, MemoryTurnDispatchJob] = {}
        self.purge_jobs: dict[uuid.UUID, object] = {}
        self.fail_on_dispatch = False

    def add_session(self, session: ConversationSession) -> None:
        self.sessions[session.id] = session

    def lock_session(
        self, *, organization_id: uuid.UUID, session_id: uuid.UUID
    ) -> ConversationSession | None:
        session = self.sessions.get(session_id)
        if session is None or session.organization_id != organization_id:
            return None
        return session

    def save_session(self, session: ConversationSession) -> None:
        self.sessions[session.id] = session

    def find_turn_by_request(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        idempotency_key_hash: str,
    ) -> ConversationTurn | None:
        for turn in self.turns.values():
            if (
                turn.organization_id == organization_id
                and turn.session_id == session_id
                and turn.request_identity.idempotency_key_hash == idempotency_key_hash
            ):
                return turn
        return None

    def add_turn(self, turn: ConversationTurn) -> None:
        self.turns[turn.id] = turn

    def lock_turn(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
    ) -> ConversationTurn | None:
        turn = self.turns.get(turn_id)
        if (
            turn is None
            or turn.organization_id != organization_id
            or turn.session_id != session_id
        ):
            return None
        return turn

    def save_turn(self, turn: ConversationTurn) -> None:
        self.turns[turn.id] = turn

    def add_entry(self, entry: ConversationMemoryEntry) -> None:
        self.entries[entry.id] = entry

    def get_entry(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        entry_id: uuid.UUID,
    ) -> ConversationMemoryEntry | None:
        entry = self.entries.get(entry_id)
        if (
            entry is None
            or entry.organization_id != organization_id
            or entry.session_id != session_id
        ):
            return None
        return entry

    def save_entry(self, entry: ConversationMemoryEntry) -> None:
        self.entries[entry.id] = entry

    def add_dispatch_job(self, job: MemoryTurnDispatchJob) -> None:
        if self.fail_on_dispatch:
            raise RuntimeError("safe synthetic dispatch insert failure")
        self.dispatch_jobs[job.id] = job

    def add_purge_job(self, job) -> None:
        self.purge_jobs[job.id] = job


class _UnitOfWork:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.commit_count = 0
        self.rollback_count = 0
        self._snapshot = None

    def begin(self) -> None:
        self._snapshot = copy.deepcopy(
            (
                self.repository.sessions,
                self.repository.turns,
                self.repository.entries,
                self.repository.dispatch_jobs,
                self.repository.purge_jobs,
            )
        )

    def commit(self) -> None:
        self.commit_count += 1
        self._snapshot = None

    def rollback(self) -> None:
        self.rollback_count += 1
        if self._snapshot is not None:
            (
                self.repository.sessions,
                self.repository.turns,
                self.repository.entries,
                self.repository.dispatch_jobs,
                self.repository.purge_jobs,
            ) = self._snapshot
        self._snapshot = None


def _create_session(repo: _Repository, uow: _UnitOfWork) -> ConversationSession:
    command = CreateSessionCommand(
        session_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=1,
        deployment_snapshot_hash=None,
        mapping_version="mapping-v1",
        memory_policy_version="memory-v1",
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        audience_kind=AudienceKind.PUBLIC_CHATBOT,
        subject_type=None,
        subject_id=None,
        idle_expires_at=_now() + timedelta(hours=24),
        absolute_expires_at=_now() + timedelta(days=7),
        now=_now(),
    )
    return CreateSessionUseCase(repository=repo, uow=uow).execute(command).session


def _start_command(session: ConversationSession) -> StartTurnCommand:
    return StartTurnCommand(
        organization_id=session.organization_id,
        session_id=session.id,
        expected_lifecycle_revision=session.lifecycle_revision,
        turn_id=uuid.uuid4(),
        user_entry_id=uuid.uuid4(),
        dispatch_id=uuid.uuid4(),
        idempotency_key_hash="1" * 64,
        request_fingerprint="2" * 64,
        user_content=_protected(),
        channel="conversation",
        minimum_worker_capability="memory-runtime-v1",
        max_dispatch_attempts=5,
        now=_now(),
    )


def test_create_and_start_turn_commit_session_turn_entry_and_dispatch_together():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    command = _start_command(session)

    result = StartTurnUseCase(repository=repo, uow=uow).execute(command)

    assert result.replayed is False
    assert result.turn_id == command.turn_id
    assert result.dispatch_id == command.dispatch_id
    assert repo.sessions[session.id].active_turn_id == command.turn_id
    assert repo.turns[command.turn_id].user_entry_id == command.user_entry_id
    assert repo.entries[command.user_entry_id].lifecycle.value == "provisional"
    assert repo.dispatch_jobs[command.dispatch_id].turn_id == command.turn_id
    assert uow.commit_count == 2
    assert uow.rollback_count == 0


def test_same_request_replays_existing_turn_and_conflicting_fingerprint_fails():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    command = _start_command(session)
    use_case = StartTurnUseCase(repository=repo, uow=uow)
    first = use_case.execute(command)

    replay = use_case.execute(
        replace(
            command,
            turn_id=uuid.uuid4(),
            user_entry_id=uuid.uuid4(),
            dispatch_id=uuid.uuid4(),
        )
    )

    assert replay.replayed is True
    assert replay.turn_id == first.turn_id
    assert len(repo.turns) == 1
    assert len(repo.dispatch_jobs) == 1

    with pytest.raises(DuplicateRequestConflictError):
        use_case.execute(
            replace(
                command,
                request_fingerprint="3" * 64,
                turn_id=uuid.uuid4(),
                user_entry_id=uuid.uuid4(),
                dispatch_id=uuid.uuid4(),
            )
        )
    assert uow.rollback_count == 1


def test_start_turn_rejects_session_at_idle_expiry_without_partial_writes():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    command = _start_command(session)
    session.idle_expires_at = command.now

    with pytest.raises(SessionNotActiveError):
        StartTurnUseCase(repository=repo, uow=uow).execute(command)

    assert repo.sessions[session.id].active_turn_id is None
    assert repo.turns == {}
    assert repo.entries == {}
    assert repo.dispatch_jobs == {}
    assert uow.rollback_count == 1


def test_dispatch_insert_failure_rolls_back_the_whole_start_turn_unit():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    repo.fail_on_dispatch = True

    with pytest.raises(RuntimeError, match="synthetic dispatch"):
        StartTurnUseCase(repository=repo, uow=uow).execute(_start_command(session))

    restored = repo.sessions[session.id]
    assert restored.active_turn_id is None
    assert restored.next_turn_sequence == 1
    assert repo.turns == {}
    assert repo.entries == {}
    assert repo.dispatch_jobs == {}
    assert uow.rollback_count == 1


def test_complete_turn_atomically_approves_user_and_assistant_entries():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    start = _start_command(session)
    StartTurnUseCase(repository=repo, uow=uow).execute(start)
    turn = repo.turns[start.turn_id]
    turn.mark_queued(expected_version=1, now=_now())
    turn.mark_running(
        expected_version=2,
        execution_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        now=_now(),
    )
    assistant_entry_id = uuid.uuid4()

    result = CompleteTurnUseCase(repository=repo, uow=uow).execute(
        CompleteTurnCommand(
            organization_id=session.organization_id,
            session_id=session.id,
            turn_id=turn.id,
            expected_lifecycle_revision=1,
            expected_turn_version=3,
            outcome="completed",
            assistant_entry_id=assistant_entry_id,
            assistant_content=_protected(b"assistant"),
            safe_failure_reason=None,
            now=_now(),
        )
    )

    assert result.content_revision == 1
    assert repo.sessions[session.id].active_turn_id is None
    assert repo.entries[start.user_entry_id].lifecycle.value == "approved"
    assert repo.entries[assistant_entry_id].lifecycle.value == "approved"
    assert repo.turns[turn.id].assistant_entry_id == assistant_entry_id


def test_complete_turn_replays_same_terminal_result_and_rejects_identity_conflict():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    start = _start_command(session)
    StartTurnUseCase(repository=repo, uow=uow).execute(start)
    turn = repo.turns[start.turn_id]
    turn.mark_queued(expected_version=1, now=_now())
    turn.mark_running(
        expected_version=2,
        execution_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        now=_now(),
    )
    assistant_entry_id = uuid.uuid4()
    command = CompleteTurnCommand(
        organization_id=session.organization_id,
        session_id=session.id,
        turn_id=turn.id,
        expected_lifecycle_revision=1,
        expected_turn_version=3,
        outcome="completed",
        assistant_entry_id=assistant_entry_id,
        assistant_content=_protected(b"assistant"),
        safe_failure_reason=None,
        now=_now(),
    )
    use_case = CompleteTurnUseCase(repository=repo, uow=uow)

    first = use_case.execute(command)
    replay = use_case.execute(command)

    assert replay == first
    assert len(repo.entries) == 2
    assert repo.sessions[session.id].content_revision == 1

    assert command.assistant_content is not None
    assert command.assistant_content.display is not None
    conflicting_content = replace(
        command.assistant_content,
        display=replace(
            command.assistant_content.display,
            content_digest="e" * 64,
        ),
    )
    conflicts = (
        replace(command, assistant_entry_id=uuid.uuid4()),
        replace(command, assistant_content=conflicting_content),
        replace(command, expected_turn_version=first.turn_version),
        replace(
            command,
            outcome="failed",
            assistant_entry_id=None,
            assistant_content=None,
            safe_failure_reason="memory.execution_failed",
        ),
    )
    for conflicting_command in conflicts:
        with pytest.raises(StaleTurnVersionError):
            use_case.execute(conflicting_command)


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_complete_turn_replays_matching_failed_or_cancelled_result(outcome: str):
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    start = _start_command(session)
    StartTurnUseCase(repository=repo, uow=uow).execute(start)
    command = CompleteTurnCommand(
        organization_id=session.organization_id,
        session_id=session.id,
        turn_id=start.turn_id,
        expected_lifecycle_revision=1,
        expected_turn_version=1,
        outcome=outcome,  # type: ignore[arg-type]
        assistant_entry_id=None,
        assistant_content=None,
        safe_failure_reason="memory.execution_failed",
        now=_now(),
    )
    use_case = CompleteTurnUseCase(repository=repo, uow=uow)

    first = use_case.execute(command)
    replay = use_case.execute(command)

    assert replay == first
    assert repo.entries[start.user_entry_id].lifecycle.value == "rejected"


def test_complete_turn_rejects_late_write_at_idle_expiry():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    start = _start_command(session)
    StartTurnUseCase(repository=repo, uow=uow).execute(start)
    turn = repo.turns[start.turn_id]
    turn.mark_queued(expected_version=1, now=_now())
    turn.mark_running(
        expected_version=2,
        execution_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        now=_now(),
    )
    session.idle_expires_at = _now()
    assistant_entry_id = uuid.uuid4()

    with pytest.raises(SessionNotActiveError):
        CompleteTurnUseCase(repository=repo, uow=uow).execute(
            CompleteTurnCommand(
                organization_id=session.organization_id,
                session_id=session.id,
                turn_id=turn.id,
                expected_lifecycle_revision=1,
                expected_turn_version=3,
                outcome="completed",
                assistant_entry_id=assistant_entry_id,
                assistant_content=_protected(b"assistant"),
                safe_failure_reason=None,
                now=_now(),
            )
        )

    assert repo.sessions[session.id].active_turn_id == turn.id
    assert repo.sessions[session.id].content_revision == 0
    assert repo.turns[turn.id].status.value == "running"
    assert repo.entries[start.user_entry_id].lifecycle.value == "provisional"
    assert assistant_entry_id not in repo.entries


def test_failed_turn_releases_active_claim_without_approving_user_content():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    start = _start_command(session)
    StartTurnUseCase(repository=repo, uow=uow).execute(start)

    result = CompleteTurnUseCase(repository=repo, uow=uow).execute(
        CompleteTurnCommand(
            organization_id=session.organization_id,
            session_id=session.id,
            turn_id=start.turn_id,
            expected_lifecycle_revision=1,
            expected_turn_version=1,
            outcome="failed",
            assistant_entry_id=None,
            assistant_content=None,
            safe_failure_reason="memory.execution_failed",
            now=_now(),
        )
    )

    assert result.content_revision == 0
    assert repo.entries[start.user_entry_id].lifecycle.value == "rejected"
    assert repo.sessions[session.id].active_turn_id is None


def test_complete_turn_rejects_unknown_outcome_without_mutating_state():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    start = _start_command(session)
    StartTurnUseCase(repository=repo, uow=uow).execute(start)

    with pytest.raises(ValueError, match="outcome"):
        CompleteTurnCommand(
            organization_id=session.organization_id,
            session_id=session.id,
            turn_id=start.turn_id,
            expected_lifecycle_revision=1,
            expected_turn_version=1,
            outcome="unknown",  # type: ignore[arg-type]
            assistant_entry_id=None,
            assistant_content=None,
            safe_failure_reason="memory.execution_failed",
            now=_now(),
        )

    assert repo.sessions[session.id].active_turn_id == start.turn_id
    assert repo.entries[start.user_entry_id].lifecycle.value == "provisional"


def test_delete_request_tombstones_session_and_creates_purge_job_atomically():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    purge_job_id = uuid.uuid4()

    result = RequestDeleteUseCase(repository=repo, uow=uow).execute(
        RequestDeleteCommand(
            organization_id=session.organization_id,
            session_id=session.id,
            expected_lifecycle_revision=1,
            purge_job_id=purge_job_id,
            receipt_verifier_hash="4" * 64,
            receipt_verifier_key_version="key-v1",
            receipt_expires_at=_now() + timedelta(days=8),
            max_attempts=8,
            now=_now(),
        )
    )

    assert result.lifecycle is SessionLifecycle.DELETE_PENDING
    assert result.purge_job_id == purge_job_id
    assert purge_job_id in repo.purge_jobs
    assert uow.commit_count == 2


def test_close_session_blocks_new_turns_and_late_completion():
    repo = _Repository()
    uow = _UnitOfWork(repo)
    session = _create_session(repo, uow)
    start = _start_command(session)
    StartTurnUseCase(repository=repo, uow=uow).execute(start)

    result = CloseSessionUseCase(repository=repo, uow=uow).execute(
        CloseSessionCommand(
            organization_id=session.organization_id,
            session_id=session.id,
            expected_lifecycle_revision=1,
            now=_now(),
        )
    )

    assert result.lifecycle is SessionLifecycle.CLOSED
    assert result.lifecycle_revision == 2
    assert repo.sessions[session.id].active_turn_id is None

    with pytest.raises(StaleLifecycleRevisionError) as late_completion:
        CompleteTurnUseCase(repository=repo, uow=uow).execute(
            CompleteTurnCommand(
                organization_id=session.organization_id,
                session_id=session.id,
                turn_id=start.turn_id,
                expected_lifecycle_revision=1,
                expected_turn_version=1,
                outcome="failed",
                assistant_entry_id=None,
                assistant_content=None,
                safe_failure_reason="memory.execution_failed",
                now=_now(),
            )
        )
    assert getattr(late_completion.value, "code", None) == (
        "memory.stale_lifecycle_revision"
    )
