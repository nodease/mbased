from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.workflow_engine.application.conversation_memory_admission import (
    AdmitConversationExecutionCommand,
    CONVERSATION_EXECUTION_ADMISSION_RETENTION,
    AdmitConversationExecutionUseCase,
    ClaimConversationExecutionCommand,
    ClaimConversationExecutionUseCase,
    ConversationExecutionAdmission,
    ConversationExecutionConflictError,
    ConversationExecutionFenceError,
    FinishConversationExecutionCommand,
    FinishConversationExecutionUseCase,
    PurgeExpiredConversationExecutionAdmissionsCommand,
    PurgeExpiredConversationExecutionAdmissionsUseCase,
)


def _now() -> datetime:
    return datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


class _Repository:
    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, uuid.UUID], ConversationExecutionAdmission] = {}

        self.deleted_calls = []
        self.deleted_count = 0
    def find_by_dispatch(self, *, organization_id, dispatch_id):
        return self.rows.get((organization_id, dispatch_id))

    def lock_by_dispatch(self, *, organization_id, dispatch_id):
        return self.rows.get((organization_id, dispatch_id))

    def add(self, admission):
        self.rows[(admission.organization_id, admission.dispatch_id)] = admission

    def save(self, admission):
        self.rows[(admission.organization_id, admission.dispatch_id)] = admission


    def delete_expired_terminal(self, *, now, limit):
        self.deleted_calls.append((now, limit))
        return self.deleted_count

class _UnitOfWork:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.snapshot = None

    def begin(self):
        self.snapshot = copy.deepcopy(self.repository.rows)

    def commit(self):
        self.snapshot = None

    def rollback(self):
        self.repository.rows = self.snapshot
        self.snapshot = None


def _admit_command(**changes) -> AdmitConversationExecutionCommand:
    values = {
        "admission_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "dispatch_id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "turn_id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(),
        "app_id": uuid.uuid4(),
        "deployment_id": uuid.uuid4(),
        "deployment_version": 3,
        "request_fingerprint": "a" * 64,
        "memory_contract_version": "conversation-memory-v1",
        "mapping_version": "conversation-mapping-v1",
        "memory_policy_version": "memory-policy-v1",
        "storage_generation": 1,
        "minimum_worker_capability": "memory-runtime-v1",
        "now": _now(),
    }
    values.update(changes)
    return AdmitConversationExecutionCommand(**values)


def test_duplicate_dispatch_converges_on_one_durable_execution() -> None:
    repository = _Repository()
    use_case = AdmitConversationExecutionUseCase(
        repository=repository,
        uow=_UnitOfWork(repository),
    )
    command = _admit_command()

    first = use_case.execute(command)
    replay = use_case.execute(
        _admit_command(
            **{
                name: getattr(command, name)
                for name in command.__dataclass_fields__
                if name != "admission_id"
            }
        )
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.admission_id == first.admission_id
    assert replay.execution_id == first.execution_id
    assert len(repository.rows) == 1
    admission = repository.rows[(command.organization_id, command.dispatch_id)]
    assert admission.retention_expires_at is None


def test_same_dispatch_with_different_fingerprint_or_binding_fails_closed() -> None:
    repository = _Repository()
    use_case = AdmitConversationExecutionUseCase(
        repository=repository,
        uow=_UnitOfWork(repository),
    )
    command = _admit_command()
    use_case.execute(command)

    for changed in (
        _admit_command(
            **{
                name: getattr(command, name)
                for name in command.__dataclass_fields__
                if name not in {"admission_id", "request_fingerprint"}
            },
            request_fingerprint="b" * 64,
        ),
        _admit_command(
            **{
                name: getattr(command, name)
                for name in command.__dataclass_fields__
                if name not in {"admission_id", "deployment_version"}
            },
            deployment_version=4,
        ),
    ):
        with pytest.raises(ConversationExecutionConflictError):
            use_case.execute(changed)


def test_expired_lease_can_be_reclaimed_and_stale_generation_is_fenced() -> None:
    repository = _Repository()
    uow = _UnitOfWork(repository)
    command = _admit_command()
    AdmitConversationExecutionUseCase(repository=repository, uow=uow).execute(command)
    claim_use_case = ClaimConversationExecutionUseCase(
        repository=repository,
        uow=uow,
    )

    first = claim_use_case.execute(
        ClaimConversationExecutionCommand(
            organization_id=command.organization_id,
            dispatch_id=command.dispatch_id,
            owner="worker-a",
            attempt_id=uuid.uuid4(),
            lease_deadline=_now() + timedelta(seconds=30),
            now=_now(),
        )
    )
    with pytest.raises(ConversationExecutionFenceError):
        claim_use_case.execute(
            ClaimConversationExecutionCommand(
                organization_id=command.organization_id,
                dispatch_id=command.dispatch_id,
                owner="worker-b",
                attempt_id=first.attempt_id,
                lease_deadline=_now() + timedelta(seconds=31),
                now=_now() + timedelta(seconds=1),
            )
        )
    second = claim_use_case.execute(
        ClaimConversationExecutionCommand(
            organization_id=command.organization_id,
            dispatch_id=command.dispatch_id,
            owner="worker-b",
            attempt_id=uuid.uuid4(),
            lease_deadline=_now() + timedelta(seconds=61),
            now=_now() + timedelta(seconds=31),
        )
    )

    assert first.lease_generation == 1
    assert second.lease_generation == 2
    with pytest.raises(ConversationExecutionFenceError):
        FinishConversationExecutionUseCase(repository=repository, uow=uow).execute(
            FinishConversationExecutionCommand(
                organization_id=command.organization_id,
                dispatch_id=command.dispatch_id,
                owner="worker-a",
                lease_generation=first.lease_generation,
                outcome="failed",
                result_entry_id=None,
                result_digest=None,
                safe_failure_reason="memory.worker_lost",
                now=_now() + timedelta(seconds=32),
            )
        )


def test_terminal_replay_is_idempotent_but_conflicting_result_is_rejected() -> None:
    repository = _Repository()
    uow = _UnitOfWork(repository)
    command = _admit_command()
    AdmitConversationExecutionUseCase(repository=repository, uow=uow).execute(command)
    claim = ClaimConversationExecutionUseCase(repository=repository, uow=uow).execute(
        ClaimConversationExecutionCommand(
            organization_id=command.organization_id,
            dispatch_id=command.dispatch_id,
            owner="worker",
            attempt_id=uuid.uuid4(),
            lease_deadline=_now() + timedelta(seconds=30),
            now=_now(),
        )
    )
    result_entry_id = uuid.uuid4()
    finish = FinishConversationExecutionCommand(
        organization_id=command.organization_id,
        dispatch_id=command.dispatch_id,
        owner="worker",
        lease_generation=claim.lease_generation,
        outcome="completed",
        result_entry_id=result_entry_id,
        result_digest="c" * 64,
        safe_failure_reason=None,
        now=_now(),
    )
    use_case = FinishConversationExecutionUseCase(repository=repository, uow=uow)

    first = use_case.execute(finish)
    replay = use_case.execute(finish)

    admission = repository.rows[(command.organization_id, command.dispatch_id)]
    assert first.replayed is False
    assert replay.replayed is True
    assert admission.terminal_at == finish.now
    assert admission.retention_expires_at == (
        finish.now + CONVERSATION_EXECUTION_ADMISSION_RETENTION
    )
    with pytest.raises(ConversationExecutionConflictError):
        use_case.execute(
            FinishConversationExecutionCommand(
                **{
                    **{
                        name: getattr(finish, name)
                        for name in finish.__dataclass_fields__
                        if name != "result_digest"
                    },
                    "result_digest": "d" * 64,
                }
            )
        )


def test_retention_purge_deletes_only_one_bounded_batch() -> None:
    repository = _Repository()
    repository.deleted_count = 7
    use_case = PurgeExpiredConversationExecutionAdmissionsUseCase(
        repository=repository,
        uow=_UnitOfWork(repository),
    )

    result = use_case.execute(
        PurgeExpiredConversationExecutionAdmissionsCommand(now=_now(), limit=100)
    )

    assert result.deleted_count == 7
    assert repository.deleted_calls == [(_now(), 100)]


@pytest.mark.parametrize(
    "now,limit",
    [
        (datetime(2026, 7, 22, 12), 100),
        (_now(), 0),
        (_now(), 501),
    ],
)
def test_retention_purge_rejects_unbounded_or_naive_requests(
    now: datetime, limit: int
) -> None:
    repository = _Repository()
    use_case = PurgeExpiredConversationExecutionAdmissionsUseCase(
        repository=repository,
        uow=_UnitOfWork(repository),
    )

    with pytest.raises(ValueError, match="retention policy is invalid"):
        use_case.execute(
            PurgeExpiredConversationExecutionAdmissionsCommand(now=now, limit=limit)
        )

    assert repository.deleted_calls == []
