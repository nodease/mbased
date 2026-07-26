from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from apps.gateway.adapters.queue import conversation_turn_publisher as adapter
from apps.memory.domain.conversation import DispatchStatus, MemoryTurnDispatchJob
from apps.shared.domain.conversation_memory_task import (
    CONVERSATION_TURN_TASK_QUEUE,
)


NOW = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Celery:
    def __init__(self) -> None:
        self.calls = []

    def send_task(self, name, args=None, kwargs=None, **options):
        self.calls.append((name, args, kwargs, options))
        return SimpleNamespace(id="broker-result-id")


class _FailingCelery(_Celery):
    def send_task(self, name, args=None, kwargs=None, **options):
        self.calls.append((name, args, kwargs, options))
        raise RuntimeError("broker publish failed before acknowledgement")


def test_publisher_claims_then_sends_only_the_reference_envelope(monkeypatch) -> None:
    events = []

    class _Claim:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            events.append(("claim", command))
            return SimpleNamespace(claim_generation=2)

    class _Mark:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            events.append(("mark", command))

    monkeypatch.setattr(adapter, "ClaimTurnDispatchUseCase", _Claim)
    monkeypatch.setattr(adapter, "MarkTurnDispatchPublishedUseCase", _Mark)
    monkeypatch.setattr(
        adapter,
        "SqlAlchemyConversationMemoryRepository",
        lambda _session: object(),
    )
    monkeypatch.setattr(
        adapter, "SqlAlchemyMemoryUnitOfWork", lambda _session: object()
    )
    session = _Session()
    celery = _Celery()
    values = {
        "organization_id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "dispatch_id": uuid.uuid4(),
        "turn_id": uuid.uuid4(),
        "memory_contract_version": "conversation-memory-v1",
        "storage_generation": 1,
        "minimum_worker_capability": "memory-runtime-v1",
    }

    adapter.CeleryConversationTurnPublisher(
        celery_app=celery,
        session_factory=lambda: session,
        clock=lambda: NOW,
    ).publish(**values)

    name, args, _kwargs, options = celery.calls[0]
    assert name == "workflow.execute_conversation_turn"
    assert set(args[0]) == {
        "envelope_version",
        "organization_id",
        "dispatch_id",
        "turn_id",
        "claim_generation",
        "broker_message_id",
        "memory_contract_version",
        "storage_generation",
        "minimum_worker_capability",
    }
    assert args[0]["claim_generation"] == 2
    assert options["queue"] == CONVERSATION_TURN_TASK_QUEUE
    assert options["ignore_result"] is True
    assert [event[0] for event in events] == ["claim", "mark"]
    assert session.closed is True


def test_publisher_releases_its_current_claim_after_send_failure(
    monkeypatch,
) -> None:
    events = []

    class _Claim:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            events.append(("claim", command))
            return SimpleNamespace(claim_generation=2)

    class _Mark:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            events.append(("mark", command))

    class _RecordFailure:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            events.append(("record_failure", command))
            return SimpleNamespace(status=DispatchStatus.RECONCILE_REQUIRED)

    monkeypatch.setattr(adapter, "ClaimTurnDispatchUseCase", _Claim)
    monkeypatch.setattr(adapter, "MarkTurnDispatchPublishedUseCase", _Mark)
    monkeypatch.setattr(
        adapter,
        "RecordTurnDispatchPublishFailureUseCase",
        _RecordFailure,
        raising=False,
    )
    monkeypatch.setattr(
        adapter,
        "SqlAlchemyConversationMemoryRepository",
        lambda _session: object(),
    )
    monkeypatch.setattr(
        adapter, "SqlAlchemyMemoryUnitOfWork", lambda _session: object()
    )
    session = _Session()

    with pytest.raises(RuntimeError, match="workflow.task_publish_failed"):
        adapter.CeleryConversationTurnPublisher(
            celery_app=_FailingCelery(),
            session_factory=lambda: session,
            clock=lambda: NOW,
        ).publish(
            organization_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            dispatch_id=uuid.uuid4(),
            turn_id=uuid.uuid4(),
            memory_contract_version="conversation-memory-v1",
            storage_generation=1,
            minimum_worker_capability="memory-runtime-v1",
        )

    assert [event[0] for event in events] == ["claim", "record_failure"]
    assert session.closed is True


def test_publisher_immediately_finalizes_an_exhausted_dispatch(
    monkeypatch,
) -> None:
    events = []

    class _Claim:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            events.append(("claim", command))
            return SimpleNamespace(claim_generation=3)

    class _RecordFailure:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            events.append(("record_failure", command))
            return SimpleNamespace(status=DispatchStatus.TERMINAL)

    class _Finalize:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, command):
            events.append(("finalize", command))

    monkeypatch.setattr(adapter, "ClaimTurnDispatchUseCase", _Claim)
    monkeypatch.setattr(
        adapter,
        "RecordTurnDispatchPublishFailureUseCase",
        _RecordFailure,
    )
    monkeypatch.setattr(
        adapter,
        "FinalizeTerminalTurnDispatchUseCase",
        _Finalize,
    )
    monkeypatch.setattr(
        adapter,
        "SqlAlchemyConversationMemoryRepository",
        lambda _session: object(),
    )
    monkeypatch.setattr(
        adapter,
        "SqlAlchemyMemoryUnitOfWork",
        lambda _session: object(),
    )
    session = _Session()
    session_id = uuid.uuid4()
    turn_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="workflow.task_publish_failed"):
        adapter.CeleryConversationTurnPublisher(
            celery_app=_FailingCelery(),
            session_factory=lambda: session,
            clock=lambda: NOW,
        ).publish(
            organization_id=uuid.uuid4(),
            session_id=session_id,
            dispatch_id=uuid.uuid4(),
            turn_id=turn_id,
            memory_contract_version="conversation-memory-v1",
            storage_generation=1,
            minimum_worker_capability="memory-runtime-v1",
        )

    assert [event[0] for event in events] == [
        "claim",
        "record_failure",
        "finalize",
    ]
    assert events[-1][1].session_id == session_id
    assert events[-1][1].turn_id == turn_id
    assert session.closed is True


def test_publish_failure_is_immediately_claimable_by_the_exact_retry_publisher(
    monkeypatch,
) -> None:
    job = MemoryTurnDispatchJob.pending(
        dispatch_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
        max_attempts=3,
        now=NOW,
    )

    class _Repository:
        def lock_dispatch_job(self, *, organization_id, dispatch_id):
            if job.organization_id != organization_id or job.id != dispatch_id:
                return None
            return job

        def save_dispatch_job(self, saved):
            assert saved is job

    class _UnitOfWork:
        def begin(self):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

    repository = _Repository()
    monkeypatch.setattr(
        adapter,
        "SqlAlchemyConversationMemoryRepository",
        lambda _session: repository,
    )
    monkeypatch.setattr(
        adapter, "SqlAlchemyMemoryUnitOfWork", lambda _session: _UnitOfWork()
    )
    values = {
        "organization_id": job.organization_id,
        "session_id": job.session_id,
        "dispatch_id": job.id,
        "turn_id": job.turn_id,
        "memory_contract_version": job.memory_contract_version,
        "storage_generation": job.storage_generation,
        "minimum_worker_capability": job.minimum_worker_capability,
    }

    with pytest.raises(RuntimeError, match="workflow.task_publish_failed"):
        adapter.CeleryConversationTurnPublisher(
            celery_app=_FailingCelery(),
            session_factory=_Session,
            clock=lambda: NOW,
        ).publish(**values)

    assert job.status is DispatchStatus.RECONCILE_REQUIRED
    assert job.next_attempt_at == NOW
    retry_celery = _Celery()
    adapter.CeleryConversationTurnPublisher(
        celery_app=retry_celery,
        session_factory=_Session,
        clock=lambda: NOW,
    ).publish(**values)

    assert len(retry_celery.calls) == 1
    assert job.status is DispatchStatus.PUBLISHED
    assert job.claim_generation == 2
