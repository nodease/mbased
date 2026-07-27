from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from apps.memory import tasks
from apps.memory.domain.conversation import DispatchStatus, MemoryTurnDispatchJob
from apps.memory.domain.errors import DispatchStateConflictError
from apps.shared.celery_app import celery_app


NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


def _job(*, max_attempts: int = 3) -> MemoryTurnDispatchJob:
    return MemoryTurnDispatchJob.pending(
        dispatch_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
        max_attempts=max_attempts,
        now=NOW,
    )


class _Publisher:
    def __init__(self, *, conflict_once: bool = False) -> None:
        self.calls = []
        self.conflict_once = conflict_once

    def publish(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.conflict_once:
            self.conflict_once = False
            raise DispatchStateConflictError()


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_reconciler_republishes_pending_and_due_reconcile_jobs() -> None:
    pending = _job()
    reconcile = _job()
    reconcile.claim(
        owner="dispatcher-a",
        deadline=NOW + timedelta(seconds=30),
        now=NOW,
    )
    reconcile.record_publish_failure(
        owner="dispatcher-a",
        claim_generation=1,
        safe_reason_code="memory.dispatch_publish_failed",
        now=NOW + timedelta(seconds=1),
    )
    publisher = _Publisher()

    counts = tasks._reconcile_due_dispatch_jobs(
        (pending, reconcile),
        publisher=publisher,
        session_factory=_Session,
        now=NOW + timedelta(minutes=1),
    )

    assert counts == {
        "published": 2,
        "recovered": 0,
        "terminal": 0,
        "conflict": 0,
        "failed": 0,
    }
    assert [call["dispatch_id"] for call in publisher.calls] == [
        pending.id,
        reconcile.id,
    ]


def test_reconciler_recovers_expired_claim_with_bounded_future_backoff(monkeypatch):
    job = _job(max_attempts=3)
    job.claim(
        owner="dispatcher-a",
        deadline=NOW + timedelta(seconds=1),
        now=NOW,
    )
    commands = []
    sessions = []

    class Recover:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            commands.append(command)
            return SimpleNamespace(status=DispatchStatus.RECONCILE_REQUIRED)

    monkeypatch.setattr(tasks, "RecoverExpiredTurnDispatchUseCase", Recover)

    def session_factory():
        session = _Session()
        sessions.append(session)
        return session

    counts = tasks._reconcile_due_dispatch_jobs(
        (job,),
        publisher=_Publisher(),
        session_factory=session_factory,
        now=NOW + timedelta(seconds=2),
    )

    assert counts["recovered"] == 1
    assert counts["failed"] == 0
    assert commands[0].retry_at > commands[0].now
    assert commands[0].retry_at - commands[0].now <= timedelta(minutes=5)
    assert sessions[0].closed is True


def test_reconciler_finalizes_turn_when_expired_claim_exhausts_attempts(monkeypatch):
    job = _job(max_attempts=1)
    job.claim(
        owner="dispatcher-a",
        deadline=NOW + timedelta(seconds=1),
        now=NOW,
    )
    finalized = []

    class Recover:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            assert command.retry_at is None
            return SimpleNamespace(status=DispatchStatus.TERMINAL)

    class Finalize:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            finalized.append(command)

    monkeypatch.setattr(tasks, "RecoverExpiredTurnDispatchUseCase", Recover)
    monkeypatch.setattr(tasks, "FinalizeTerminalTurnDispatchUseCase", Finalize)

    counts = tasks._reconcile_due_dispatch_jobs(
        (job,),
        publisher=_Publisher(),
        session_factory=_Session,
        now=NOW + timedelta(seconds=2),
    )

    assert counts["terminal"] == 1
    assert finalized[0].dispatch_id == job.id
    assert finalized[0].turn_id == job.turn_id


def test_reconciler_isolates_a_concurrent_claim_conflict_per_item() -> None:
    first = _job()
    second = _job()
    publisher = _Publisher(conflict_once=True)

    counts = tasks._reconcile_due_dispatch_jobs(
        (first, second),
        publisher=publisher,
        session_factory=_Session,
        now=NOW,
    )

    assert counts["conflict"] == 1
    assert counts["published"] == 1
    assert [call["dispatch_id"] for call in publisher.calls] == [first.id, second.id]


def test_dispatch_reconciliation_has_a_memory_owned_periodic_task() -> None:
    entry = celery_app.conf.beat_schedule["memory-turn-dispatch-reconciliation"]

    assert entry == {
        "task": "memory.turn_dispatch.reconcile",
        "schedule": 15.0,
        "options": {"queue": "log"},
    }
def test_reconciler_retries_cleanup_for_an_already_terminal_dispatch(monkeypatch):
    job = _job(max_attempts=1)
    job.claim(
        owner="dispatcher-a",
        deadline=NOW + timedelta(seconds=1),
        now=NOW,
    )
    job.recover_expired_claim(
        now=NOW + timedelta(seconds=2),
        retry_at=None,
        safe_reason_code="memory.dispatch_claim_expired",
    )
    finalized = []

    class Finalize:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            finalized.append(command)

    monkeypatch.setattr(tasks, "FinalizeTerminalTurnDispatchUseCase", Finalize)

    counts = tasks._reconcile_due_dispatch_jobs(
        (job,),
        publisher=_Publisher(),
        session_factory=_Session,
        now=NOW + timedelta(seconds=3),
    )

    assert counts["terminal"] == 1
    assert finalized[0].dispatch_id == job.id
