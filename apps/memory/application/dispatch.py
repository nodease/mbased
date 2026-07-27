from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from apps.memory.application.ports import (
    ConversationMemoryRepositoryPort,
    MemoryUnitOfWorkPort,
)
from apps.memory.domain.conversation import DispatchStatus, MemoryTurnDispatchJob
from apps.memory.domain.errors import DispatchStateConflictError


@dataclass(frozen=True, slots=True)
class ClaimTurnDispatchCommand:
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    owner: str
    deadline: datetime
    now: datetime


@dataclass(frozen=True, slots=True)
class ClaimTurnDispatchResult:
    dispatch_id: uuid.UUID
    status: DispatchStatus
    claim_generation: int
    attempt_count: int


@dataclass(frozen=True, slots=True)
class MarkTurnDispatchPublishedCommand:
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    owner: str
    claim_generation: int
    broker_message_id: str
    now: datetime


@dataclass(frozen=True, slots=True)
class MarkTurnDispatchPublishedResult:
    dispatch_id: uuid.UUID
    status: DispatchStatus
    claim_generation: int


@dataclass(frozen=True, slots=True)
class RecordTurnDispatchPublishFailureCommand:
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    owner: str
    claim_generation: int
    safe_reason_code: str
    now: datetime


@dataclass(frozen=True, slots=True)
class RecordTurnDispatchPublishFailureResult:
    dispatch_id: uuid.UUID
    status: DispatchStatus
    claim_generation: int
    next_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class FinalizeTerminalTurnDispatchCommand:
    organization_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    dispatch_id: uuid.UUID
    now: datetime


@dataclass(frozen=True, slots=True)
class FinalizeTerminalTurnDispatchResult:
    dispatch_id: uuid.UUID
    turn_id: uuid.UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class RecoverExpiredTurnDispatchCommand:
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    now: datetime
    retry_at: datetime | None
    safe_reason_code: str


@dataclass(frozen=True, slots=True)
class RecoverExpiredTurnDispatchResult:
    dispatch_id: uuid.UUID
    status: DispatchStatus
    claim_generation: int
    next_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class AcknowledgeTurnDispatchCommand:
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    claim_generation: int
    broker_message_id: str
    workflow_admission_reference: str
    now: datetime


@dataclass(frozen=True, slots=True)
class AcknowledgeTurnDispatchResult:
    dispatch_id: uuid.UUID
    status: DispatchStatus
    claim_generation: int
    replayed: bool


class _TransactionalDispatchUseCase:
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

    def _lock(self, organization_id: uuid.UUID, dispatch_id: uuid.UUID):
        job = self.repository.lock_dispatch_job(
            organization_id=organization_id,
            dispatch_id=dispatch_id,
        )
        if job is None:
            raise DispatchStateConflictError()
        return job


class ListDueTurnDispatchJobsUseCase(_TransactionalDispatchUseCase):
    def execute(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[MemoryTurnDispatchJob, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("dispatch reconciliation limit must be between 1 and 500")
        return self._execute(
            lambda: self.repository.list_due_dispatch_jobs(now=now, limit=limit)
        )


class ClaimTurnDispatchUseCase(_TransactionalDispatchUseCase):
    def execute(
        self,
        command: ClaimTurnDispatchCommand,
    ) -> ClaimTurnDispatchResult:
        def operation() -> ClaimTurnDispatchResult:
            job = self._lock(command.organization_id, command.dispatch_id)
            generation = job.claim(
                owner=command.owner,
                deadline=command.deadline,
                now=command.now,
            )
            self.repository.save_dispatch_job(job)
            return ClaimTurnDispatchResult(
                dispatch_id=job.id,
                status=job.status,
                claim_generation=generation,
                attempt_count=job.attempt_count,
            )

        return self._execute(operation)


class MarkTurnDispatchPublishedUseCase(_TransactionalDispatchUseCase):
    def execute(
        self,
        command: MarkTurnDispatchPublishedCommand,
    ) -> MarkTurnDispatchPublishedResult:
        def operation() -> MarkTurnDispatchPublishedResult:
            job = self._lock(command.organization_id, command.dispatch_id)
            job.mark_published(
                owner=command.owner,
                claim_generation=command.claim_generation,
                broker_message_id=command.broker_message_id,
                now=command.now,
            )
            self.repository.save_dispatch_job(job)
            return MarkTurnDispatchPublishedResult(
                dispatch_id=job.id,
                status=job.status,
                claim_generation=job.claim_generation,
            )

        return self._execute(operation)


class RecordTurnDispatchPublishFailureUseCase(_TransactionalDispatchUseCase):
    def execute(
        self,
        command: RecordTurnDispatchPublishFailureCommand,
    ) -> RecordTurnDispatchPublishFailureResult:
        def operation() -> RecordTurnDispatchPublishFailureResult:
            job = self._lock(command.organization_id, command.dispatch_id)
            job.record_publish_failure(
                owner=command.owner,
                claim_generation=command.claim_generation,
                safe_reason_code=command.safe_reason_code,
                now=command.now,
            )
            self.repository.save_dispatch_job(job)
            return RecordTurnDispatchPublishFailureResult(
                dispatch_id=job.id,
                status=job.status,
                claim_generation=job.claim_generation,
                next_attempt_at=job.next_attempt_at,
            )

        return self._execute(operation)


class FinalizeTerminalTurnDispatchUseCase(_TransactionalDispatchUseCase):
    def execute(
        self,
        command: FinalizeTerminalTurnDispatchCommand,
    ) -> FinalizeTerminalTurnDispatchResult:
        def operation() -> FinalizeTerminalTurnDispatchResult:
            session = self.repository.lock_session(
                organization_id=command.organization_id,
                session_id=command.session_id,
            )
            if session is None:
                raise DispatchStateConflictError()
            turn = self.repository.lock_turn(
                organization_id=command.organization_id,
                session_id=command.session_id,
                turn_id=command.turn_id,
            )
            if turn is None:
                raise DispatchStateConflictError()
            user_entry = self.repository.get_entry(
                organization_id=command.organization_id,
                session_id=command.session_id,
                entry_id=turn.user_entry_id,
            )
            if user_entry is None or user_entry.turn_id != turn.id:
                raise DispatchStateConflictError()
            job = self._lock(command.organization_id, command.dispatch_id)
            if (
                job.status is not DispatchStatus.TERMINAL
                or job.session_id != session.id
                or job.turn_id != turn.id
                or turn.dispatch_id != job.id
                or not job.safe_failure_reason
            ):
                raise DispatchStateConflictError()
            if turn.terminal:
                if (
                    turn.safe_failure_reason != job.safe_failure_reason
                    or session.active_turn_id == turn.id
                ):
                    raise DispatchStateConflictError()
                return FinalizeTerminalTurnDispatchResult(
                    dispatch_id=job.id,
                    turn_id=turn.id,
                    replayed=True,
                )
            turn.fail(
                expected_version=turn.version,
                safe_reason_code=job.safe_failure_reason,
                now=command.now,
            )
            session.release_terminal_turn(
                turn_id=turn.id,
                expected_lifecycle_revision=session.lifecycle_revision,
                now=command.now,
            )
            user_entry.reject(now=command.now)
            self.repository.save_turn(turn)
            self.repository.save_session(session)
            self.repository.save_entry(user_entry)
            return FinalizeTerminalTurnDispatchResult(
                dispatch_id=job.id,
                turn_id=turn.id,
                replayed=False,
            )

        return self._execute(operation)


class RecoverExpiredTurnDispatchUseCase(_TransactionalDispatchUseCase):
    def execute(
        self,
        command: RecoverExpiredTurnDispatchCommand,
    ) -> RecoverExpiredTurnDispatchResult:
        def operation() -> RecoverExpiredTurnDispatchResult:
            job = self._lock(command.organization_id, command.dispatch_id)
            job.recover_expired_claim(
                now=command.now,
                retry_at=command.retry_at,
                safe_reason_code=command.safe_reason_code,
            )
            self.repository.save_dispatch_job(job)
            return RecoverExpiredTurnDispatchResult(
                dispatch_id=job.id,
                status=job.status,
                claim_generation=job.claim_generation,
                next_attempt_at=job.next_attempt_at,
            )

        return self._execute(operation)


class AcknowledgeTurnDispatchUseCase(_TransactionalDispatchUseCase):
    def execute(
        self,
        command: AcknowledgeTurnDispatchCommand,
    ) -> AcknowledgeTurnDispatchResult:
        def operation() -> AcknowledgeTurnDispatchResult:
            job = self._lock(command.organization_id, command.dispatch_id)
            replayed = job.acknowledge(
                claim_generation=command.claim_generation,
                broker_message_id=command.broker_message_id,
                workflow_admission_reference=command.workflow_admission_reference,
                now=command.now,
            )
            self.repository.save_dispatch_job(job)
            return AcknowledgeTurnDispatchResult(
                dispatch_id=job.id,
                status=job.status,
                claim_generation=job.claim_generation,
                replayed=replayed,
            )

        return self._execute(operation)
