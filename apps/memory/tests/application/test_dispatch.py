from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.memory.application.dispatch import (
    ClaimTurnDispatchCommand,
    ClaimTurnDispatchUseCase,
    MarkTurnDispatchPublishedCommand,
    MarkTurnDispatchPublishedUseCase,
    RecoverExpiredTurnDispatchCommand,
    RecoverExpiredTurnDispatchUseCase,
)
from apps.memory.domain.conversation import MemoryTurnDispatchJob
from apps.memory.domain.errors import DispatchStateConflictError


def _now() -> datetime:
    return datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def _job(max_attempts: int = 2) -> MemoryTurnDispatchJob:
    return MemoryTurnDispatchJob.pending(
        dispatch_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
        max_attempts=max_attempts,
        now=_now(),
    )


class _Repository:
    def __init__(self, job: MemoryTurnDispatchJob) -> None:
        self.job = job
        self.save_count = 0

    def lock_dispatch_job(self, *, organization_id, dispatch_id):
        if self.job.organization_id != organization_id or self.job.id != dispatch_id:
            return None
        return self.job

    def save_dispatch_job(self, job):
        self.job = job
        self.save_count += 1


class _UnitOfWork:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.commit_count = 0
        self.rollback_count = 0
        self._snapshot = None

    def begin(self):
        self._snapshot = copy.deepcopy(self.repository.job)

    def commit(self):
        self.commit_count += 1
        self._snapshot = None

    def rollback(self):
        self.rollback_count += 1
        if self._snapshot is not None:
            self.repository.job = self._snapshot
        self._snapshot = None


def test_claim_and_publish_dispatch_use_fenced_application_commands():
    job = _job()
    repository = _Repository(job)
    uow = _UnitOfWork(repository)

    claimed = ClaimTurnDispatchUseCase(repository=repository, uow=uow).execute(
        ClaimTurnDispatchCommand(
            organization_id=job.organization_id,
            dispatch_id=job.id,
            owner="dispatcher-a",
            deadline=_now() + timedelta(seconds=30),
            now=_now(),
        )
    )
    assert claimed.status.value == "claimed"
    assert claimed.claim_generation == 1

    published = MarkTurnDispatchPublishedUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        MarkTurnDispatchPublishedCommand(
            organization_id=job.organization_id,
            dispatch_id=job.id,
            owner="dispatcher-a",
            claim_generation=1,
            broker_message_id="opaque-message-reference",
            now=_now() + timedelta(seconds=1),
        )
    )
    assert published.status.value == "published"
    assert repository.job.claim_owner is None
    assert uow.commit_count == 2


def test_stale_publish_rolls_back_without_exposing_adapter_state():
    job = _job()
    repository = _Repository(job)
    uow = _UnitOfWork(repository)
    ClaimTurnDispatchUseCase(repository=repository, uow=uow).execute(
        ClaimTurnDispatchCommand(
            organization_id=job.organization_id,
            dispatch_id=job.id,
            owner="dispatcher-a",
            deadline=_now() + timedelta(seconds=30),
            now=_now(),
        )
    )

    with pytest.raises(DispatchStateConflictError):
        MarkTurnDispatchPublishedUseCase(
            repository=repository,
            uow=uow,
        ).execute(
            MarkTurnDispatchPublishedCommand(
                organization_id=job.organization_id,
                dispatch_id=job.id,
                owner="dispatcher-a",
                claim_generation=0,
                broker_message_id="opaque-message-reference",
                now=_now() + timedelta(seconds=1),
            )
        )

    assert repository.job.status.value == "claimed"
    assert uow.rollback_count == 1


def test_expired_claim_recovery_is_a_transactional_application_command():
    job = _job()
    repository = _Repository(job)
    uow = _UnitOfWork(repository)
    deadline = _now() + timedelta(seconds=30)
    ClaimTurnDispatchUseCase(repository=repository, uow=uow).execute(
        ClaimTurnDispatchCommand(
            organization_id=job.organization_id,
            dispatch_id=job.id,
            owner="dispatcher-a",
            deadline=deadline,
            now=_now(),
        )
    )
    retry_at = deadline + timedelta(seconds=10)

    result = RecoverExpiredTurnDispatchUseCase(
        repository=repository,
        uow=uow,
    ).execute(
        RecoverExpiredTurnDispatchCommand(
            organization_id=job.organization_id,
            dispatch_id=job.id,
            now=deadline,
            retry_at=retry_at,
            safe_reason_code="memory.dispatch_claim_expired",
        )
    )

    assert result.status.value == "reconcile_required"
    assert result.next_attempt_at == retry_at
    assert repository.save_count == 2
