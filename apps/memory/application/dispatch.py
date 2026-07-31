from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from apps.memory.application.ports import (
    ConversationMemoryRepositoryPort,
    MemoryUnitOfWorkPort,
)
from apps.memory.domain.conversation import DispatchStatus
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
