from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from apps.memory.application.ports import (
    ConversationMemoryRepositoryPort,
    MemoryUnitOfWorkPort,
)
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationMemoryEntry,
    ConversationPurgeJob,
    ConversationSession,
    ConversationTurn,
    EntryLifecycle,
    EntryType,
    MemoryTurnDispatchJob,
    ProtectedContent,
    ProtectedEntryContent,
    RequestIdentity,
    SessionLifecycle,
    TurnStatus,
)
from apps.memory.domain.errors import (
    EntryNotFoundError,
    SessionNotFoundError,
    StaleLifecycleRevisionError,
    StaleTurnVersionError,
)


@dataclass(frozen=True, slots=True)
class CreateSessionCommand:
    session_id: uuid.UUID
    organization_id: uuid.UUID
    app_id: uuid.UUID
    workflow_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int | None
    deployment_snapshot_hash: str | None
    mapping_version: str
    memory_policy_version: str
    memory_contract_version: str
    storage_generation: int
    audience_kind: AudienceKind
    subject_type: str | None
    subject_id: uuid.UUID | None
    idle_expires_at: datetime
    absolute_expires_at: datetime
    now: datetime


@dataclass(frozen=True, slots=True)
class CreateSessionResult:
    session: ConversationSession


@dataclass(frozen=True, slots=True)
class StartTurnCommand:
    organization_id: uuid.UUID
    session_id: uuid.UUID
    expected_lifecycle_revision: int
    turn_id: uuid.UUID
    user_entry_id: uuid.UUID
    dispatch_id: uuid.UUID
    idempotency_key_hash: str
    request_fingerprint: str
    user_content: ProtectedEntryContent
    channel: str
    minimum_worker_capability: str
    max_dispatch_attempts: int
    now: datetime


@dataclass(frozen=True, slots=True)
class StartTurnResult:
    session_id: uuid.UUID
    turn_id: uuid.UUID
    dispatch_id: uuid.UUID
    turn_sequence: int
    turn_version: int
    lifecycle_revision: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class CompleteTurnCommand:
    organization_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    expected_lifecycle_revision: int
    expected_turn_version: int
    outcome: Literal["completed", "failed", "cancelled"]
    assistant_entry_id: uuid.UUID | None
    assistant_content: ProtectedEntryContent | None
    safe_failure_reason: str | None
    now: datetime

    def __post_init__(self) -> None:
        if self.outcome not in {"completed", "failed", "cancelled"}:
            raise ValueError("outcome is invalid")


@dataclass(frozen=True, slots=True)
class CompleteTurnResult:
    session_id: uuid.UUID
    turn_id: uuid.UUID
    lifecycle: SessionLifecycle
    content_revision: int
    turn_version: int


@dataclass(frozen=True, slots=True)
class CloseSessionCommand:
    organization_id: uuid.UUID
    session_id: uuid.UUID
    expected_lifecycle_revision: int
    now: datetime


@dataclass(frozen=True, slots=True)
class CloseSessionResult:
    session_id: uuid.UUID
    lifecycle: SessionLifecycle
    lifecycle_revision: int


@dataclass(frozen=True, slots=True)
class RequestDeleteCommand:
    organization_id: uuid.UUID
    session_id: uuid.UUID
    expected_lifecycle_revision: int
    purge_job_id: uuid.UUID
    receipt_verifier_hash: str
    receipt_verifier_key_version: str
    receipt_expires_at: datetime
    max_attempts: int
    now: datetime


@dataclass(frozen=True, slots=True)
class RequestDeleteResult:
    session_id: uuid.UUID
    lifecycle: SessionLifecycle
    lifecycle_revision: int
    purge_job_id: uuid.UUID


class _TransactionalUseCase:
    def __init__(
        self,
        *,
        repository: ConversationMemoryRepositoryPort,
        uow: MemoryUnitOfWorkPort,
    ) -> None:
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


class CreateSessionUseCase(_TransactionalUseCase):
    def execute(self, command: CreateSessionCommand) -> CreateSessionResult:
        def operation() -> CreateSessionResult:
            session = ConversationSession.create(
                session_id=command.session_id,
                organization_id=command.organization_id,
                app_id=command.app_id,
                workflow_id=command.workflow_id,
                deployment_id=command.deployment_id,
                deployment_version=command.deployment_version,
                deployment_snapshot_hash=command.deployment_snapshot_hash,
                mapping_version=command.mapping_version,
                memory_policy_version=command.memory_policy_version,
                memory_contract_version=command.memory_contract_version,
                storage_generation=command.storage_generation,
                audience_kind=command.audience_kind,
                subject_type=command.subject_type,
                subject_id=command.subject_id,
                idle_expires_at=command.idle_expires_at,
                absolute_expires_at=command.absolute_expires_at,
                now=command.now,
            )
            self.repository.add_session(session)
            return CreateSessionResult(session=session)

        return self._execute(operation)


class StartTurnUseCase(_TransactionalUseCase):
    def execute(self, command: StartTurnCommand) -> StartTurnResult:
        def operation() -> StartTurnResult:
            request_identity = RequestIdentity(
                idempotency_key_hash=command.idempotency_key_hash,
                request_fingerprint=command.request_fingerprint,
            )
            session = self.repository.lock_session(
                organization_id=command.organization_id,
                session_id=command.session_id,
            )
            if session is None:
                raise SessionNotFoundError()

            existing = self.repository.find_turn_by_request(
                organization_id=command.organization_id,
                session_id=command.session_id,
                idempotency_key_hash=request_identity.idempotency_key_hash,
            )
            if existing is not None:
                existing.request_identity.ensure_replay_matches(
                    request_identity.request_fingerprint
                )
                return StartTurnResult(
                    session_id=session.id,
                    turn_id=existing.id,
                    dispatch_id=existing.dispatch_id,
                    turn_sequence=existing.sequence,
                    turn_version=existing.version,
                    lifecycle_revision=session.lifecycle_revision,
                    replayed=True,
                )

            sequence = session.claim_turn(
                turn_id=command.turn_id,
                expected_lifecycle_revision=command.expected_lifecycle_revision,
                now=command.now,
            )
            turn = ConversationTurn.start(
                turn_id=command.turn_id,
                organization_id=command.organization_id,
                session_id=command.session_id,
                sequence=sequence,
                started_lifecycle_revision=session.lifecycle_revision,
                request_identity=request_identity,
                user_entry_id=command.user_entry_id,
                dispatch_id=command.dispatch_id,
                now=command.now,
            )
            entry = ConversationMemoryEntry.provisional_user(
                entry_id=command.user_entry_id,
                organization_id=command.organization_id,
                session_id=command.session_id,
                turn_id=command.turn_id,
                sequence=(sequence * 2) - 1,
                channel=command.channel,
                content=command.user_content,
                idempotency_key_hash=request_identity.idempotency_key_hash,
                now=command.now,
            )
            dispatch = MemoryTurnDispatchJob.pending(
                dispatch_id=command.dispatch_id,
                organization_id=command.organization_id,
                session_id=command.session_id,
                turn_id=command.turn_id,
                memory_contract_version=session.memory_contract_version,
                storage_generation=session.storage_generation,
                minimum_worker_capability=command.minimum_worker_capability,
                max_attempts=command.max_dispatch_attempts,
                now=command.now,
            )
            self.repository.save_session(session)
            self.repository.add_turn(turn)
            self.repository.add_entry(entry)
            self.repository.add_dispatch_job(dispatch)
            return StartTurnResult(
                session_id=session.id,
                turn_id=turn.id,
                dispatch_id=dispatch.id,
                turn_sequence=turn.sequence,
                turn_version=turn.version,
                lifecycle_revision=session.lifecycle_revision,
                replayed=False,
            )

        return self._execute(operation)


class CompleteTurnUseCase(_TransactionalUseCase):
    def execute(self, command: CompleteTurnCommand) -> CompleteTurnResult:
        def operation() -> CompleteTurnResult:
            session = self.repository.lock_session(
                organization_id=command.organization_id,
                session_id=command.session_id,
            )
            if session is None:
                raise SessionNotFoundError()
            turn = self.repository.lock_turn(
                organization_id=command.organization_id,
                session_id=command.session_id,
                turn_id=command.turn_id,
            )
            if turn is None:
                raise SessionNotFoundError()
            user_entry = self.repository.get_entry(
                organization_id=command.organization_id,
                session_id=command.session_id,
                entry_id=turn.user_entry_id,
            )
            if user_entry is None:
                raise EntryNotFoundError()

            replay = _terminal_complete_turn_replay(
                repository=self.repository,
                command=command,
                session=session,
                turn=turn,
                user_entry=user_entry,
            )
            if replay is not None:
                return replay

            if command.outcome == "completed":
                if (
                    command.assistant_entry_id is None
                    or command.assistant_content is None
                ):
                    raise ValueError(
                        "completed turn requires protected assistant content"
                    )
                turn.complete(
                    expected_version=command.expected_turn_version,
                    assistant_entry_id=command.assistant_entry_id,
                    now=command.now,
                )
                session.release_turn(
                    turn_id=turn.id,
                    expected_lifecycle_revision=command.expected_lifecycle_revision,
                    content_changed=True,
                    now=command.now,
                )
                user_entry.approve(
                    content_revision=session.content_revision,
                    now=command.now,
                )
                assistant_entry = ConversationMemoryEntry.approved_assistant(
                    entry_id=command.assistant_entry_id,
                    organization_id=command.organization_id,
                    session_id=command.session_id,
                    turn_id=turn.id,
                    sequence=turn.sequence * 2,
                    channel=user_entry.channel,
                    content=command.assistant_content,
                    content_revision=session.content_revision,
                    idempotency_key_hash=_assistant_entry_key(turn.id),
                    now=command.now,
                )
                self.repository.add_entry(assistant_entry)
            else:
                if command.assistant_entry_id is not None or command.assistant_content:
                    raise ValueError(
                        "non-completed turn cannot persist assistant content"
                    )
                if not command.safe_failure_reason:
                    raise ValueError("terminal failure requires safe reason")
                if command.outcome == "failed":
                    turn.fail(
                        expected_version=command.expected_turn_version,
                        safe_reason_code=command.safe_failure_reason,
                        now=command.now,
                    )
                else:
                    turn.cancel(
                        expected_version=command.expected_turn_version,
                        safe_reason_code=command.safe_failure_reason,
                        now=command.now,
                    )
                session.release_turn(
                    turn_id=turn.id,
                    expected_lifecycle_revision=command.expected_lifecycle_revision,
                    content_changed=False,
                    now=command.now,
                )
                user_entry.reject(now=command.now)

            self.repository.save_turn(turn)
            self.repository.save_session(session)
            self.repository.save_entry(user_entry)
            return CompleteTurnResult(
                session_id=session.id,
                turn_id=turn.id,
                lifecycle=session.lifecycle,
                content_revision=session.content_revision,
                turn_version=turn.version,
            )

        return self._execute(operation)


def _terminal_complete_turn_replay(
    *,
    repository: ConversationMemoryRepositoryPort,
    command: CompleteTurnCommand,
    session: ConversationSession,
    turn: ConversationTurn,
    user_entry: ConversationMemoryEntry,
) -> CompleteTurnResult | None:
    if not turn.terminal:
        return None
    if session.lifecycle_revision != command.expected_lifecycle_revision:
        raise StaleLifecycleRevisionError()
    if turn.version != command.expected_turn_version + 1:
        raise StaleTurnVersionError()
    if turn.status is not TurnStatus(command.outcome):
        raise StaleTurnVersionError()
    if session.active_turn_id == turn.id:
        raise StaleTurnVersionError()

    content_revision = session.content_revision
    if command.outcome == "completed":
        if command.assistant_entry_id is None or command.assistant_content is None:
            raise StaleTurnVersionError()
        if turn.assistant_entry_id != command.assistant_entry_id:
            raise StaleTurnVersionError()
        assistant_entry = repository.get_entry(
            organization_id=command.organization_id,
            session_id=command.session_id,
            entry_id=command.assistant_entry_id,
        )
        if assistant_entry is None:
            raise EntryNotFoundError()
        if (
            user_entry.lifecycle is not EntryLifecycle.APPROVED
            or user_entry.content_revision is None
            or assistant_entry.turn_id != turn.id
            or assistant_entry.entry_type is not EntryType.ASSISTANT_TURN
            or assistant_entry.lifecycle is not EntryLifecycle.APPROVED
            or assistant_entry.content_revision is None
            or assistant_entry.content_revision != user_entry.content_revision
            or not _same_protected_content_identity(
                assistant_entry.content,
                command.assistant_content,
            )
        ):
            raise StaleTurnVersionError()
        content_revision = assistant_entry.content_revision
    elif (
        command.assistant_entry_id is not None
        or command.assistant_content is not None
        or not command.safe_failure_reason
        or turn.assistant_entry_id is not None
        or turn.safe_failure_reason != command.safe_failure_reason
        or user_entry.lifecycle is not EntryLifecycle.REJECTED
        or user_entry.content_revision is not None
    ):
        raise StaleTurnVersionError()

    return CompleteTurnResult(
        session_id=session.id,
        turn_id=turn.id,
        lifecycle=session.lifecycle,
        content_revision=content_revision,
        turn_version=turn.version,
    )


def _same_protected_content_identity(
    stored: ProtectedEntryContent | None,
    requested: ProtectedEntryContent,
) -> bool:
    if stored is None:
        return False
    return _protected_projection_identity(
        stored.display
    ) == _protected_projection_identity(
        requested.display
    ) and _protected_projection_identity(
        stored.model
    ) == _protected_projection_identity(requested.model)


def _protected_projection_identity(
    projection: ProtectedContent | None,
) -> tuple[str, str, int] | None:
    if projection is None:
        return None
    return (
        projection.format_version,
        projection.content_digest,
        projection.plaintext_byte_length,
    )


class CloseSessionUseCase(_TransactionalUseCase):
    def execute(self, command: CloseSessionCommand) -> CloseSessionResult:
        def operation() -> CloseSessionResult:
            session = self.repository.lock_session(
                organization_id=command.organization_id,
                session_id=command.session_id,
            )
            if session is None:
                raise SessionNotFoundError()
            session.close(
                expected_lifecycle_revision=command.expected_lifecycle_revision,
                now=command.now,
            )
            self.repository.save_session(session)
            return CloseSessionResult(
                session_id=session.id,
                lifecycle=session.lifecycle,
                lifecycle_revision=session.lifecycle_revision,
            )

        return self._execute(operation)


class RequestDeleteUseCase(_TransactionalUseCase):
    def execute(self, command: RequestDeleteCommand) -> RequestDeleteResult:
        def operation() -> RequestDeleteResult:
            session = self.repository.lock_session(
                organization_id=command.organization_id,
                session_id=command.session_id,
            )
            if session is None:
                raise SessionNotFoundError()
            session.request_delete(
                expected_lifecycle_revision=command.expected_lifecycle_revision,
                now=command.now,
            )
            purge_job = ConversationPurgeJob.pending(
                purge_job_id=command.purge_job_id,
                organization_id=command.organization_id,
                session_id=command.session_id,
                session_reference_digest=_session_reference_digest(
                    command.organization_id,
                    command.session_id,
                ),
                app_id=session.app_id,
                deployment_id=session.deployment_id,
                deployment_version=session.deployment_version,
                audience_kind=session.audience_kind,
                receipt_verifier_hash=command.receipt_verifier_hash,
                receipt_verifier_key_version=command.receipt_verifier_key_version,
                receipt_expires_at=command.receipt_expires_at,
                max_attempts=command.max_attempts,
                now=command.now,
            )
            self.repository.save_session(session)
            self.repository.add_purge_job(purge_job)
            return RequestDeleteResult(
                session_id=session.id,
                lifecycle=session.lifecycle,
                lifecycle_revision=session.lifecycle_revision,
                purge_job_id=purge_job.id,
            )

        return self._execute(operation)


def _assistant_entry_key(turn_id: uuid.UUID) -> str:
    return hashlib.sha256(f"assistant:{turn_id}".encode("ascii")).hexdigest()


def _session_reference_digest(
    organization_id: uuid.UUID,
    session_id: uuid.UUID,
) -> str:
    return hashlib.sha256(f"{organization_id}:{session_id}".encode("ascii")).hexdigest()
