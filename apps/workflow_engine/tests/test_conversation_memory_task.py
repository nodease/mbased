from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from apps.shared.celery_app import celery_app
from apps.shared.domain.conversation_memory_task import ConversationTurnTaskEnvelope
from apps.workflow_engine import tasks
from apps.workflow_engine.application.conversation_memory_admission import (
    ConversationExecutionState,
)
from apps.workflow_engine.composition.conversation_memory import (
    CONVERSATION_PROVIDER_MAX_TIMEOUT_SECONDS,
)


def _payload() -> dict:
    return ConversationTurnTaskEnvelope(
        organization_id=uuid.uuid4(),
        dispatch_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        claim_generation=2,
        broker_message_id="conversation-turn-message-1",
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
    ).to_payload()


def test_conversation_turn_task_is_registered_with_bounded_recovery_retry() -> None:
    task = celery_app.tasks["workflow.execute_conversation_turn"]

    assert task.name == tasks.execute_conversation_turn.name
    assert task.max_retries == 3
    assert task.ignore_result is True
    assert task.acks_late is True
    assert task.reject_on_worker_lost is True


def test_registered_task_executes_reference_envelope_through_production_composition(
    monkeypatch,
) -> None:
    captured = {}

    class _UseCase:
        def execute(self, command):
            captured["command"] = command
            captured["result"] = SimpleNamespace(
                admission_id=uuid.uuid4(),
                execution_id=uuid.uuid4(),
                turn_id=command.envelope.turn_id,
                state=ConversationExecutionState.COMPLETED,
            )
            return captured["result"]

    monkeypatch.setattr(
        tasks,
        "build_conversation_turn_use_case",
        lambda: _UseCase(),
    )
    payload = _payload()

    result = tasks.execute_conversation_turn.run(payload)

    assert captured["command"].envelope == ConversationTurnTaskEnvelope.from_payload(
        payload
    )
    assert result == {
        "status": "completed",
        "admission_id": str(captured["result"].admission_id),
    }


def test_duplicate_delivery_uses_a_distinct_worker_fence_owner(monkeypatch) -> None:
    owners = []

    class _UseCase:
        def execute(self, command):
            owners.append(command.worker_owner)
            return SimpleNamespace(
                admission_id=uuid.uuid4(),
                state=ConversationExecutionState.COMPLETED,
            )

    monkeypatch.setattr(
        tasks,
        "build_conversation_turn_use_case",
        lambda: _UseCase(),
    )
    payload = _payload()

    tasks.execute_conversation_turn.run(payload)
    tasks.execute_conversation_turn.run(payload)

    assert len(owners) == 2
    assert owners[0] != owners[1]


def test_task_schedules_bounded_recovery_without_exposing_exception_text(
    monkeypatch,
) -> None:
    captured = {}

    class _UseCase:
        def execute(self, _command):
            raise RuntimeError("sensitive provider detail")

    class _RetryScheduled(Exception):
        pass

    def schedule_retry(*, exc, countdown):
        captured["exc"] = exc
        captured["countdown"] = countdown
        raise _RetryScheduled

    monkeypatch.setattr(
        tasks,
        "build_conversation_turn_use_case",
        lambda: _UseCase(),
    )
    monkeypatch.setattr(tasks.execute_conversation_turn, "retry", schedule_retry)

    with pytest.raises(_RetryScheduled):
        tasks.execute_conversation_turn.run(_payload())

    assert str(captured["exc"]) == "workflow.execution_failed"
    assert "sensitive provider detail" not in str(captured["exc"])
    assert (
        captured["countdown"]
        == tasks.CONVERSATION_EXECUTION_LEASE_SECONDS + 1
    )
    assert (
        captured["countdown"]
        > CONVERSATION_PROVIDER_MAX_TIMEOUT_SECONDS
    )
