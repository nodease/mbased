from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from apps.workflow_engine.application.knowledge_collection_sync import (
    ExecuteKnowledgeCollectionSync,
    SyncTargetTemporarilyUnavailable,
    WorkerItemCounts,
    WorkerSyncJob,
    WorkerSyncItem,
)

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _job(**overrides) -> WorkerSyncJob:
    values = {
        "job_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "collection_id": uuid.uuid4(),
        "requested_by": uuid.uuid4(),
        "target_snapshot_revision": "a" * 64,
        "total_count": 1,
        "status": "queued",
        "previous_sync_state": "manual",
        "attempt_count": 0,
        "max_attempts": 20,
        "lease_owner": None,
        "lease_expires_at": None,
        "next_retry_at": NOW,
        "execution_deadline_at": NOW + timedelta(minutes=30),
        "started_at": None,
    }
    values.update(overrides)
    return WorkerSyncJob(**values)


def _item(job: WorkerSyncJob, *, position: int = 0) -> WorkerSyncItem:
    return WorkerSyncItem(
        item_id=uuid.uuid4(),
        job_id=job.job_id,
        organization_id=job.organization_id,
        collection_id=job.collection_id,
        knowledge_base_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        position=position,
        target_revision="a" * 64,
        status="pending",
        attempt_count=0,
        max_attempts=3,
    )


def _dependencies(job: WorkerSyncJob):
    authorization = Mock()
    authorization.is_allowed.return_value = True
    repository = Mock()
    repository.lock_job.return_value = job
    repository.database_now.return_value = NOW
    repository.collection_is_supported.return_value = True
    repository.target_snapshot_matches.return_value = True
    document = Mock()
    audit = Mock()
    publisher = Mock()
    unit_of_work = Mock()
    use_case = ExecuteKnowledgeCollectionSync(
        authorization=authorization,
        repository=repository,
        document=document,
        audit=audit,
        publisher=publisher,
        unit_of_work=unit_of_work,
    )
    return use_case, authorization, repository, document, audit, publisher, unit_of_work


def test_unexpired_running_delivery_is_duplicate_noop() -> None:
    job = _job(
        status="running",
        lease_owner="owner-a",
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    use_case, _authorization, repository, document, *_rest = _dependencies(job)

    result = use_case.execute(job.job_id, owner="owner-b")

    assert result.status == "duplicate"
    assert result.reason_code == "running"
    document.sync.assert_not_called()
    repository.mark_running.assert_not_called()


def test_permission_revoked_at_claim_cancels_before_document_access() -> None:
    job = _job()
    use_case, authorization, repository, document, audit, _publisher, uow = _dependencies(
        job
    )
    authorization.is_allowed.return_value = False

    result = use_case.execute(job.job_id, owner="owner-a")

    assert result.status == "cancelled"
    assert result.reason_code == "sync.permission_revoked"
    repository.cancel_job.assert_called_once()
    document.sync.assert_not_called()
    audit.record.assert_called_once()
    uow.commit.assert_called_once()


def test_changed_target_snapshot_fails_before_document_access() -> None:
    job = _job()
    use_case, _authorization, repository, document, audit, _publisher, uow = (
        _dependencies(job)
    )
    repository.target_snapshot_matches.return_value = False
    repository.item_counts.return_value = WorkerItemCounts(
        pending=0,
        running=0,
        succeeded=0,
        failed=0,
        skipped=1,
    )

    result = use_case.execute(job.job_id, owner="owner-a")

    assert result.status == "failed"
    assert result.reason_code == "sync.targets_changed"
    repository.mark_unfinished_items_skipped.assert_called_once()
    repository.mark_running.assert_not_called()
    document.sync.assert_not_called()
    repository.finalize_job.assert_called_once_with(
        job,
        status="failed",
        now=NOW,
        reason_code="sync.targets_changed",
        completed_count=0,
        failed_count=0,
        skipped_count=1,
    )
    audit.record.assert_called_once()
    uow.commit.assert_called_once()


def test_successful_target_and_terminal_state_share_application_commit_flow() -> None:
    job = _job()
    item = _item(job)
    use_case, _authorization, repository, document, audit, publisher, uow = _dependencies(
        job
    )
    repository.lock_owned_job.side_effect = [job, job, job, job]
    repository.next_pending_item.side_effect = [item, None]
    repository.item_counts.return_value = WorkerItemCounts(
        pending=0,
        running=0,
        succeeded=1,
        failed=0,
        skipped=0,
    )

    result = use_case.execute(job.job_id, owner="owner-a")

    assert result.status == "succeeded"
    document.sync.assert_called_once_with(item, actor_id=job.requested_by)
    repository.mark_item_succeeded.assert_called_once()
    repository.finalize_job.assert_called_once()
    publisher.publish.assert_not_called()
    assert uow.commit.call_count == 3
    assert audit.record.call_count == 2


def test_missing_snapshot_item_cannot_finalize_as_success() -> None:
    job = _job(total_count=1)
    use_case, _authorization, repository, document, audit, _publisher, uow = (
        _dependencies(job)
    )
    repository.lock_owned_job.side_effect = [job, job, job]
    repository.next_pending_item.return_value = None
    repository.item_counts.return_value = WorkerItemCounts(
        pending=0,
        running=0,
        succeeded=0,
        failed=0,
        skipped=0,
        missing=1,
        reason_code="sync.targets_changed",
    )

    result = use_case.execute(job.job_id, owner="owner-a")

    assert result.status == "failed"
    assert result.reason_code == "sync.targets_changed"
    document.sync.assert_not_called()
    repository.finalize_job.assert_called_once_with(
        job,
        status="failed",
        now=NOW,
        reason_code="sync.targets_changed",
        completed_count=0,
        failed_count=0,
        skipped_count=1,
    )
    assert audit.record.call_count == 2
    assert uow.commit.call_count == 2


def test_target_added_after_processing_prevents_successful_finalization() -> None:
    job = _job()
    item = _item(job)
    use_case, _authorization, repository, document, audit, _publisher, uow = (
        _dependencies(job)
    )
    repository.target_snapshot_matches.side_effect = [True, False]
    repository.lock_owned_job.side_effect = [job, job, job, job]
    repository.next_pending_item.side_effect = [item, None]
    repository.item_counts.return_value = WorkerItemCounts(
        pending=0,
        running=0,
        succeeded=1,
        failed=0,
        skipped=0,
    )

    result = use_case.execute(job.job_id, owner="owner-a")

    assert result.status == "partially_failed"
    assert result.reason_code == "sync.targets_changed"
    document.sync.assert_called_once_with(item, actor_id=job.requested_by)
    repository.finalize_job.assert_called_once_with(
        job,
        status="partially_failed",
        now=NOW,
        reason_code="sync.targets_changed",
        completed_count=1,
        failed_count=0,
        skipped_count=0,
    )
    assert audit.record.call_count == 2
    assert uow.commit.call_count == 3


def test_retryable_target_failure_is_durable_and_waits_for_recovery_dispatch() -> None:
    job = _job()
    item = _item(job)
    use_case, _authorization, repository, document, _audit, publisher, uow = _dependencies(
        job
    )
    repository.lock_owned_job.side_effect = [job, job, job]
    repository.next_pending_item.return_value = item
    repository.lock_item.return_value = item
    repository.mark_item_failed.return_value = True
    document.sync.side_effect = SyncTargetTemporarilyUnavailable()

    result = use_case.execute(job.job_id, owner="owner-a")

    assert result.status == "queued"
    assert result.reason_code == "sync.temporarily_unavailable"
    repository.queue_job.assert_called_once()
    publisher.publish.assert_not_called()
    assert uow.rollback.call_count >= 2
    assert uow.commit.call_count == 2


def test_batch_is_bounded_to_five_then_publishes_continuation() -> None:
    job = _job(total_count=6)
    items = [_item(job, position=index) for index in range(5)]
    use_case, _authorization, repository, document, _audit, publisher, _uow = _dependencies(
        job
    )
    repository.lock_owned_job.side_effect = [job] * 7
    repository.next_pending_item.side_effect = items
    repository.item_counts.return_value = WorkerItemCounts(
        pending=1,
        running=0,
        succeeded=5,
        failed=0,
        skipped=0,
    )

    result = use_case.execute(job.job_id, owner="owner-a")

    assert result.status == "queued"
    assert document.sync.call_count == 5
    repository.queue_job.assert_called_once()
    publisher.publish.assert_called_once_with(job.job_id)
