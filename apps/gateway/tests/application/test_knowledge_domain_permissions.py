import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.gateway.application.knowledge_administration.domain_permissions import (
    DomainPermissionCommand,
    DomainPermissionPersistenceFailed,
    DomainPermissionSubjectHidden,
    KnowledgeDomainPermissionUseCase,
    OrganizationManagerRequired,
)


class FakeAuthorization:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def is_organization_manager(self, actor_id, organization_id):
        return self.allowed

    def effective_actions(self, actor_id, organization_id):
        return {"catalog_manage"} if self.allowed else set()


class FakeRepository:
    def __init__(self, *, subject_exists=True, existing=None):
        self.subject_exists = subject_exists
        self.existing = existing
        self.saved = []
        self.deleted = []
        self.subject_lock_calls = []

    def list_permissions(self, organization_id):
        return []

    def lock_active_subject_for_grant(
        self, organization_id, subject_type, subject_id
    ):
        self.subject_lock_calls.append((organization_id, subject_type, subject_id))
        if not self.subject_exists:
            return None
        return "Knowledge Team"

    def upsert(self, command):
        row_id = self.existing or uuid.uuid4()
        self.saved.append(command)
        return row_id, self.existing is None

    def revoke(self, command):
        if self.existing is None:
            return None
        self.deleted.append(command)
        return self.existing


class FakeAudit:
    def __init__(self, *, fails=False):
        self.fails = fails
        self.events = []

    def record(self, **kwargs):
        if self.fails:
            raise RuntimeError("audit unavailable")
        self.events.append(kwargs)


class FakeUnitOfWork:
    def __init__(self):
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def flush(self):
        self.flush_count += 1

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


def _command(**overrides):
    values = {
        "actor_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "subject_type": "team",
        "subject_id": uuid.uuid4(),
        "permission_action": "catalog_manage",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
    }
    values.update(overrides)
    return DomainPermissionCommand(**values)


def _use_case(*, allowed=True, repository=None, audit=None):
    uow = FakeUnitOfWork()
    return (
        KnowledgeDomainPermissionUseCase(
            FakeAuthorization(allowed),
            repository or FakeRepository(),
            audit or FakeAudit(),
            uow,
        ),
        uow,
    )


def test_manager_grant_commits_permission_and_audit_together():
    repository = FakeRepository()
    audit = FakeAudit()
    use_case, uow = _use_case(repository=repository, audit=audit)

    result = use_case.grant(_command())

    assert result.status == "created"
    assert len(repository.saved) == 1
    assert len(audit.events) == 1
    assert uow.flush_count == 1
    assert uow.commit_count == 1
    assert uow.rollback_count == 0


def test_non_manager_cannot_grant_domain_permission():
    use_case, uow = _use_case(allowed=False)

    with pytest.raises(OrganizationManagerRequired):
        use_case.grant(_command())

    assert uow.rollback_count == 1


def test_cross_scope_or_inactive_subject_is_hidden():
    use_case, uow = _use_case(repository=FakeRepository(subject_exists=False))

    with pytest.raises(DomainPermissionSubjectHidden):
        use_case.grant(_command())

    assert uow.rollback_count == 1


def test_audit_failure_rolls_back_permission_mutation():
    use_case, uow = _use_case(audit=FakeAudit(fails=True))

    with pytest.raises(DomainPermissionPersistenceFailed):
        use_case.grant(_command())

    assert uow.commit_count == 0
    assert uow.rollback_count == 1


def test_revoke_absent_permission_is_idempotent_without_audit():
    audit = FakeAudit()
    repository = FakeRepository(subject_exists=False, existing=None)
    use_case, uow = _use_case(repository=repository, audit=audit)

    result = use_case.revoke(_command(expires_at=None))

    assert result.status == "unchanged"
    assert audit.events == []
    assert repository.subject_lock_calls == []
    assert uow.rollback_count == 1
    assert uow.commit_count == 0


def test_manager_can_revoke_existing_permission_for_inactive_subject():
    permission_id = uuid.uuid4()
    repository = FakeRepository(subject_exists=False, existing=permission_id)
    audit = FakeAudit()
    use_case, uow = _use_case(repository=repository, audit=audit)
    command = _command(expires_at=None)

    result = use_case.revoke(command)

    assert result.status == "deleted"
    assert result.permission_id == permission_id
    assert repository.subject_lock_calls == []
    assert repository.deleted == [command]
    assert audit.events == [
        {
            "command": command,
            "permission_id": permission_id,
            "operation": "deleted",
        }
    ]
    assert uow.flush_count == 1
    assert uow.commit_count == 1
    assert uow.rollback_count == 0


def test_inactive_subject_revoke_rolls_back_when_audit_fails():
    repository = FakeRepository(subject_exists=False, existing=uuid.uuid4())
    use_case, uow = _use_case(repository=repository, audit=FakeAudit(fails=True))

    with pytest.raises(DomainPermissionPersistenceFailed):
        use_case.revoke(_command(expires_at=None))

    assert repository.subject_lock_calls == []
    assert uow.flush_count == 0
    assert uow.commit_count == 0
    assert uow.rollback_count == 1


def test_non_manager_cannot_revoke_domain_permission():
    repository = FakeRepository(subject_exists=False, existing=uuid.uuid4())
    use_case, uow = _use_case(allowed=False, repository=repository)

    with pytest.raises(OrganizationManagerRequired):
        use_case.revoke(_command(expires_at=None))

    assert repository.deleted == []
    assert repository.subject_lock_calls == []
    assert uow.rollback_count == 1
