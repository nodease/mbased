from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from apps.gateway.application.knowledge_collection_sync.use_cases import (
    CollectionSyncCollectionSnapshot,
    CollectionSyncCommand,
    CollectionSyncHidden,
    CollectionSyncJobSnapshot,
    CollectionSyncPermissionDenied,
    CollectionSyncPolicyBlocked,
    CollectionSyncStatusQuery,
    CollectionSyncTarget,
    CollectionSyncTargetScan,
    ReadKnowledgeCollectionSyncStatus,
    RequestKnowledgeCollectionSync,
)

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


class Authorization:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def is_allowed(self, **_kwargs) -> bool:
        return self.allowed


class UnitOfWork:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def flush(self) -> None:
        self.events.append("flush")

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


class Audit:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.records: list[dict] = []

    def record(self, **kwargs) -> None:
        self.events.append(f"audit:{kwargs['action']}")
        self.records.append(kwargs)


class Publisher:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.job_ids: list[uuid.UUID] = []

    def publish(self, job_id: uuid.UUID) -> None:
        self.events.append("publish")
        self.job_ids.append(job_id)
        if self.fail:
            raise RuntimeError("broker detail must not escape")


class Repository:
    def __init__(self) -> None:
        self.collection = CollectionSyncCollectionSnapshot(
            collection_id=uuid.uuid4(),
            lifecycle_state="active",
            sync_state="manual",
            is_system_managed=False,
            is_source_managed=False,
        )
        self.existing: CollectionSyncJobSnapshot | None = None
        self.active: CollectionSyncJobSnapshot | None = None
        self.scan = CollectionSyncTargetScan(
            targets=(
                CollectionSyncTarget(
                    collection_item_id=uuid.uuid4(),
                    knowledge_base_id=uuid.uuid4(),
                    document_id=uuid.uuid4(),
                    item_rank=0,
                    item_created_at=NOW,
                    document_updated_at=NOW,
                ),
            )
        )
        self.created: CollectionSyncJobSnapshot | None = None
        self.create_calls = 0
        self.state_updates: list[str] = []

    def database_now(self) -> datetime:
        return NOW

    def lock_collection(self, organization_id, collection_id):
        return self.collection

    def get_collection(self, organization_id, collection_id):
        return self.collection

    def find_by_request_hash(self, organization_id, collection_id, request_key_hash):
        assert len(request_key_hash) == 64
        return self.existing

    def find_active(self, organization_id, collection_id):
        return self.active

    def scan_targets(self, organization_id, collection_id, *, limit):
        assert limit == 101
        return self.scan

    def create_job(self, **kwargs):
        self.create_calls += 1
        self.created = _job(collection_id=kwargs["command"].collection_id)
        return self.created

    def set_collection_sync_state(
        self, organization_id, collection_id, sync_state, *, now
    ):
        self.state_updates.append(sync_state)

    def latest_job(self, organization_id, collection_id):
        return self.existing

    def get_job(self, organization_id, collection_id, job_id):
        if self.existing and self.existing.job_id == job_id:
            return self.existing
        return None


def _job(*, collection_id: uuid.UUID, status: str = "queued"):
    return CollectionSyncJobSnapshot(
        job_id=uuid.uuid4(),
        collection_id=collection_id,
        status=status,
        total_count=1,
        completed_count=0,
        failed_count=0,
        skipped_count=0,
        retryable=True,
        safe_reason_code=None,
        requested_at=NOW,
        started_at=None,
        completed_at=None,
    )


def _command(repository: Repository) -> CollectionSyncCommand:
    return CollectionSyncCommand(
        actor_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        collection_id=repository.collection.collection_id,
        idempotency_key=uuid.uuid4(),
    )


def _request_use_case(
    repository: Repository,
    *,
    allowed: bool = True,
    publish_fails: bool = False,
):
    events: list[str] = []
    return (
        RequestKnowledgeCollectionSync(
            authorization=Authorization(allowed),
            repository=repository,
            audit=Audit(events),
            publisher=Publisher(events, fail=publish_fails),
            unit_of_work=UnitOfWork(events),
        ),
        events,
    )


def test_request_commits_durable_job_before_publish() -> None:
    repository = Repository()
    use_case, events = _request_use_case(repository)

    result = use_case.execute(_command(repository))

    assert result.job == repository.created
    assert result.reused is False
    assert result.dispatch_deferred is False
    assert repository.create_calls == 1
    assert repository.state_updates == ["pending"]
    assert events.index("commit") < events.index("publish")


def test_publish_failure_leaves_committed_job_for_recovery() -> None:
    repository = Repository()
    use_case, events = _request_use_case(repository, publish_fails=True)

    result = use_case.execute(_command(repository))

    assert result.dispatch_deferred is True
    assert events.count("commit") == 1
    assert "rollback" not in events


def test_same_key_or_active_job_is_reused_without_new_target_snapshot() -> None:
    repository = Repository()
    repository.existing = _job(collection_id=repository.collection.collection_id)
    use_case, events = _request_use_case(repository)

    result = use_case.execute(_command(repository))

    assert result.reused is True
    assert result.job is repository.existing
    assert repository.create_calls == 0
    assert "audit:knowledge.collection.sync.reused" in events


def test_permission_denial_is_audited_without_scanning_targets() -> None:
    repository = Repository()
    use_case, events = _request_use_case(repository, allowed=False)

    with pytest.raises(CollectionSyncPermissionDenied):
        use_case.execute(_command(repository))

    assert repository.create_calls == 0
    assert "audit:knowledge.collection.sync.denied" in events
    assert events[-1] == "commit"


@pytest.mark.parametrize(
    "scan,reason",
    [
        (CollectionSyncTargetScan(targets=()), "sync.no_eligible_targets"),
        (
            CollectionSyncTargetScan(targets=(), has_source_managed_child=True),
            "sync.not_supported",
        ),
        (
            CollectionSyncTargetScan(targets=(), has_api_document=True),
            "sync.not_supported",
        ),
        (
            CollectionSyncTargetScan(
                targets=(),
                has_multi_document_target=True,
            ),
            "sync.not_supported",
        ),
        (
            CollectionSyncTargetScan(targets=(), exceeds_limit=True),
            "sync.target_limit_exceeded",
        ),
    ],
)
def test_unsupported_or_empty_target_sets_fail_closed(scan, reason) -> None:
    repository = Repository()
    repository.scan = scan
    use_case, _events = _request_use_case(repository)

    with pytest.raises(CollectionSyncPolicyBlocked) as caught:
        use_case.execute(_command(repository))

    assert caught.value.reason_code == reason
    assert repository.create_calls == 0


def test_status_read_hides_mismatched_job_and_requires_current_authority() -> None:
    repository = Repository()
    query = CollectionSyncStatusQuery(
        actor_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        collection_id=repository.collection.collection_id,
        job_id=uuid.uuid4(),
    )
    events: list[str] = []
    denied = ReadKnowledgeCollectionSyncStatus(
        authorization=Authorization(False),
        repository=repository,
        unit_of_work=UnitOfWork(events),
    )
    with pytest.raises(CollectionSyncPermissionDenied):
        denied.execute(query)

    allowed = ReadKnowledgeCollectionSyncStatus(
        authorization=Authorization(True),
        repository=repository,
        unit_of_work=UnitOfWork(events),
    )
    with pytest.raises(CollectionSyncHidden):
        allowed.execute(query)
