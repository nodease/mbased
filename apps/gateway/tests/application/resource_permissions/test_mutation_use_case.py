from datetime import datetime, timezone
from uuid import uuid4

import pytest

from apps.gateway.application.resource_permissions.mutation import (
    PermissionMutationCommand,
    PermissionMutationNotFound,
    PermissionMutationPersistenceFailed,
    PermissionMutationResult,
    PermissionProjection,
    ResourcePermissionMutationUseCase,
)


def _command(*, operation: str = "upsert") -> PermissionMutationCommand:
    return PermissionMutationCommand(
        actor_id=uuid4(),
        organization_id=uuid4(),
        resource_type="workflow",
        resource_id=uuid4(),
        grantee_type="team",
        grantee_id=uuid4(),
        operation=operation,
        auth_state="builder" if operation == "upsert" else None,
        assigned_at=datetime.now(timezone.utc),
    )


def _projection(command: PermissionMutationCommand) -> PermissionProjection:
    return PermissionProjection(
        permission_id=uuid4(),
        organization_id=command.organization_id,
        resource_type=command.resource_type,
        resource_id=command.resource_id,
        grantee_type=command.grantee_type,
        grantee_id=command.grantee_id,
        auth_state=command.auth_state or "builder",
        assigned_by=command.actor_id,
        assigned_at=command.assigned_at,
        options={},
        flags=0,
    )


class _Repository:
    def __init__(self, result: PermissionMutationResult) -> None:
        self.result = result
        self.commands = []

    def mutate(self, command: PermissionMutationCommand) -> PermissionMutationResult:
        self.commands.append(command)
        return self.result


class _FailingRepository:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def mutate(self, _command: PermissionMutationCommand) -> PermissionMutationResult:
        raise self.error


class _SequenceRepository:
    def __init__(self, results) -> None:
        self.results = iter(results)
        self.commands = []

    def mutate(self, command: PermissionMutationCommand) -> PermissionMutationResult:
        self.commands.append(command)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


class _Audit:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.records = []

    def record(
        self,
        command: PermissionMutationCommand,
        result: PermissionMutationResult,
    ) -> None:
        if self.error is not None:
            raise self.error
        self.records.append((command, result))


class _UnitOfWork:
    def __init__(
        self,
        *,
        flush_error: Exception | None = None,
        commit_error: Exception | None = None,
    ) -> None:
        self.flush_error = flush_error
        self.commit_error = commit_error
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        if self.flush_error is not None:
            raise self.flush_error

    def commit(self) -> None:
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollback_count += 1


@pytest.mark.parametrize("status", ["created", "updated", "deleted"])
def test_changed_mutation_records_audit_flushes_and_commits(status: str) -> None:
    command = _command(operation="delete" if status == "deleted" else "upsert")
    projection = _projection(command)
    result = PermissionMutationResult(
        status=status,
        permission=projection,
        before=None if status == "created" else projection.safe_snapshot(),
        after=None if status == "deleted" else projection.safe_snapshot(),
    )
    repository = _Repository(result)
    audit = _Audit()
    unit_of_work = _UnitOfWork()

    actual = ResourcePermissionMutationUseCase(
        repository,
        audit,
        unit_of_work,
    ).execute(command)

    assert actual is result
    assert repository.commands == [command]
    assert audit.records == [(command, result)]
    assert unit_of_work.flush_count == 1
    assert unit_of_work.commit_count == 1
    assert unit_of_work.rollback_count == 0


def test_unchanged_mutation_does_not_record_audit() -> None:
    command = _command()
    projection = _projection(command)
    result = PermissionMutationResult(
        status="unchanged",
        permission=projection,
        before=projection.safe_snapshot(),
        after=projection.safe_snapshot(),
    )
    audit = _Audit()
    unit_of_work = _UnitOfWork()

    actual = ResourcePermissionMutationUseCase(
        _Repository(result),
        audit,
        unit_of_work,
    ).execute(command)

    assert actual.permission is projection
    assert audit.records == []
    assert unit_of_work.flush_count == 0
    assert unit_of_work.commit_count == 1
    assert unit_of_work.rollback_count == 0


def test_execute_many_commits_all_permission_mutations_once() -> None:
    commands = [_command(), _command()]
    results = [
        PermissionMutationResult(
            status="created",
            permission=_projection(command),
            before=None,
            after=_projection(command).safe_snapshot(),
        )
        for command in commands
    ]
    repository = _SequenceRepository(results)
    audit = _Audit()
    unit_of_work = _UnitOfWork()

    actual = ResourcePermissionMutationUseCase(
        repository,
        audit,
        unit_of_work,
    ).execute_many(commands)

    assert actual == results
    assert repository.commands == commands
    assert audit.records == list(zip(commands, results, strict=True))
    assert unit_of_work.flush_count == 2
    assert unit_of_work.commit_count == 1
    assert unit_of_work.rollback_count == 0


def test_execute_many_rolls_back_the_whole_batch_when_one_mutation_fails() -> None:
    commands = [_command(), _command()]
    first_result = PermissionMutationResult(
        status="created",
        permission=_projection(commands[0]),
        before=None,
        after=_projection(commands[0]).safe_snapshot(),
    )
    repository = _SequenceRepository(
        [first_result, RuntimeError("second permission write failed")]
    )
    audit = _Audit()
    unit_of_work = _UnitOfWork()

    with pytest.raises(PermissionMutationPersistenceFailed):
        ResourcePermissionMutationUseCase(
            repository,
            audit,
            unit_of_work,
        ).execute_many(commands)

    assert repository.commands == commands
    assert audit.records == [(commands[0], first_result)]
    assert unit_of_work.commit_count == 0
    assert unit_of_work.rollback_count == 1


@pytest.mark.parametrize(
    "audit_error,flush_error",
    [
        (RuntimeError("audit add failed"), None),
        (None, RuntimeError("audit flush failed")),
    ],
)
def test_audit_persistence_failure_rolls_back(
    audit_error: Exception | None,
    flush_error: Exception | None,
) -> None:
    command = _command()
    projection = _projection(command)
    result = PermissionMutationResult(
        status="created",
        permission=projection,
        before=None,
        after=projection.safe_snapshot(),
    )
    unit_of_work = _UnitOfWork(flush_error=flush_error)

    with pytest.raises(PermissionMutationPersistenceFailed):
        ResourcePermissionMutationUseCase(
            _Repository(result),
            _Audit(error=audit_error),
            unit_of_work,
        ).execute(command)

    assert unit_of_work.commit_count == 0
    assert unit_of_work.rollback_count == 1


def test_not_found_rolls_back_without_rewrapping() -> None:
    command = _command(operation="delete")
    audit = _Audit()
    unit_of_work = _UnitOfWork()

    with pytest.raises(PermissionMutationNotFound):
        ResourcePermissionMutationUseCase(
            _FailingRepository(PermissionMutationNotFound()),
            audit,
            unit_of_work,
        ).execute(command)

    assert audit.records == []
    assert unit_of_work.flush_count == 0
    assert unit_of_work.commit_count == 0
    assert unit_of_work.rollback_count == 1


def test_repository_failure_rolls_back_and_returns_typed_error() -> None:
    command = _command()
    audit = _Audit()
    unit_of_work = _UnitOfWork()

    with pytest.raises(PermissionMutationPersistenceFailed):
        ResourcePermissionMutationUseCase(
            _FailingRepository(RuntimeError("permission write failed")),
            audit,
            unit_of_work,
        ).execute(command)

    assert audit.records == []
    assert unit_of_work.flush_count == 0
    assert unit_of_work.commit_count == 0
    assert unit_of_work.rollback_count == 1


def test_commit_failure_rolls_back_and_returns_typed_error() -> None:
    command = _command()
    projection = _projection(command)
    result = PermissionMutationResult(
        status="created",
        permission=projection,
        before=None,
        after=projection.safe_snapshot(),
    )
    audit = _Audit()
    unit_of_work = _UnitOfWork(
        commit_error=RuntimeError("transaction commit failed")
    )

    with pytest.raises(PermissionMutationPersistenceFailed):
        ResourcePermissionMutationUseCase(
            _Repository(result),
            audit,
            unit_of_work,
        ).execute(command)

    assert audit.records == [(command, result)]
    assert unit_of_work.flush_count == 1
    assert unit_of_work.commit_count == 1
    assert unit_of_work.rollback_count == 1
