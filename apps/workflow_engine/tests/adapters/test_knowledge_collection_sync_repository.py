from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from sqlalchemy import and_
from sqlalchemy.dialects import postgresql

from apps.workflow_engine.adapters import knowledge_collection_sync_repository as repository_module
from apps.workflow_engine.adapters.knowledge_collection_sync_repository import (
    SqlAlchemyWorkerSyncAuthorization,
    SqlAlchemyWorkerSyncRepository,
)
from apps.workflow_engine.application.knowledge_collection_sync import WorkerSyncJob


def test_supported_collection_query_excludes_source_deleted_state() -> None:
    db = Mock()
    query = Mock()
    db.query.return_value = query
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = (uuid4(),)
    now = datetime.now(timezone.utc)
    job = WorkerSyncJob(
        job_id=uuid4(),
        organization_id=uuid4(),
        collection_id=uuid4(),
        requested_by=uuid4(),
        target_snapshot_revision="a" * 64,
        total_count=1,
        status="queued",
        previous_sync_state="manual",
        attempt_count=0,
        max_attempts=8,
        lease_owner=None,
        lease_expires_at=None,
        next_retry_at=now,
        execution_deadline_at=now + timedelta(minutes=30),
        started_at=None,
    )

    assert SqlAlchemyWorkerSyncRepository(db).collection_is_supported(job) is True

    predicate = and_(*query.filter.call_args.args)
    compiled = predicate.compile(dialect=postgresql.dialect())
    assert "organization_id" in str(compiled)
    assert "sync_state" in str(compiled)
    assert "source_deleted" in compiled.params.values()
    query.with_for_update.assert_called_once_with()


def test_legacy_organization_manager_without_membership_can_claim(
    monkeypatch,
) -> None:
    manager_check = Mock(return_value=True)
    membership_check = Mock(return_value=False)
    monkeypatch.setattr(
        repository_module,
        "has_organization_manager_permission",
        manager_check,
    )
    monkeypatch.setattr(
        repository_module,
        "has_active_organization_membership",
        membership_check,
    )
    job = SimpleNamespace(
        requested_by=uuid4(),
        organization_id=uuid4(),
    )

    assert SqlAlchemyWorkerSyncAuthorization(Mock()).is_allowed(job) is True
    manager_check.assert_called_once()
    membership_check.assert_not_called()


def test_collection_state_projection_does_not_overwrite_source_deleted() -> None:
    collection = SimpleNamespace(sync_state="source_deleted", updated_at=None)
    query = Mock()
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.one_or_none.return_value = collection
    db = Mock()
    db.query.return_value = query
    job_row = SimpleNamespace(
        collection_id=uuid4(),
        organization_id=uuid4(),
    )
    now = datetime.now(timezone.utc)

    SqlAlchemyWorkerSyncRepository(db)._set_collection_state(  # noqa: SLF001
        job_row,
        "synced",
        now=now,
    )

    assert collection.sync_state == "source_deleted"
    assert collection.updated_at is None


def test_finalize_job_persists_reconciled_snapshot_counts() -> None:
    now = datetime.now(timezone.utc)
    job = WorkerSyncJob(
        job_id=uuid4(),
        organization_id=uuid4(),
        collection_id=uuid4(),
        requested_by=uuid4(),
        target_snapshot_revision="a" * 64,
        total_count=1,
        status="running",
        previous_sync_state="manual",
        attempt_count=1,
        max_attempts=8,
        lease_owner="owner",
        lease_expires_at=now + timedelta(minutes=1),
        next_retry_at=None,
        execution_deadline_at=now + timedelta(minutes=30),
        started_at=now,
    )
    row = SimpleNamespace(
        id=job.job_id,
        total_count=1,
        status="running",
        retryable=True,
        safe_reason_code=None,
        lease_owner="owner",
        lease_expires_at=job.lease_expires_at,
        next_retry_at=None,
        completed_at=None,
        updated_at=now,
        completed_count=0,
        failed_count=0,
        skipped_count=0,
    )
    db = Mock()
    db.get.return_value = row
    repository = SqlAlchemyWorkerSyncRepository(db)
    repository._set_collection_state = Mock()  # type: ignore[method-assign]  # noqa: SLF001

    repository.finalize_job(
        job,
        status="failed",
        now=now,
        reason_code="sync.targets_changed",
        completed_count=0,
        failed_count=0,
        skipped_count=1,
    )

    assert row.status == "failed"
    assert row.completed_count == 0
    assert row.failed_count == 0
    assert row.skipped_count == 1
    repository._set_collection_state.assert_called_once()  # type: ignore[attr-defined]  # noqa: SLF001


def test_item_attempt_is_incremented_once_per_persisted_outcome() -> None:
    now = datetime.now(timezone.utc)
    item_id = uuid4()
    job_id = uuid4()
    item_row = SimpleNamespace(
        id=item_id,
        status="pending",
        attempt_count=0,
        max_attempts=3,
        retryable=True,
        safe_reason_code=None,
        started_at=None,
        completed_at=None,
        updated_at=now,
    )
    job_row = SimpleNamespace(
        id=job_id,
        failed_count=0,
        lease_expires_at=None,
        updated_at=now,
    )
    db = Mock()
    db.get.side_effect = lambda _model, row_id: (
        item_row if row_id == item_id else job_row
    )
    repository = SqlAlchemyWorkerSyncRepository(db)
    job = SimpleNamespace(job_id=job_id)
    item = SimpleNamespace(item_id=item_id)

    for expected_attempt, expected_retry in ((1, True), (2, True), (3, False)):
        repository.mark_item_running(item, now=now)
        assert item_row.attempt_count == expected_attempt - 1
        will_retry = repository.mark_item_failed(
            job,
            item,
            now=now,
            reason_code="sync.temporarily_unavailable",
            retryable=True,
            lease_expires_at=now + timedelta(minutes=1),
        )
        assert item_row.attempt_count == expected_attempt
        assert will_retry is expected_retry

    assert job_row.failed_count == 1


def test_successful_item_attempt_is_incremented_at_outcome_commit() -> None:
    now = datetime.now(timezone.utc)
    item_id = uuid4()
    job_id = uuid4()
    item_row = SimpleNamespace(
        id=item_id,
        status="pending",
        attempt_count=0,
        retryable=True,
        safe_reason_code=None,
        started_at=None,
        completed_at=None,
        updated_at=now,
    )
    job_row = SimpleNamespace(
        id=job_id,
        completed_count=0,
        lease_expires_at=None,
        updated_at=now,
    )
    db = Mock()
    db.get.side_effect = lambda _model, row_id: (
        item_row if row_id == item_id else job_row
    )
    repository = SqlAlchemyWorkerSyncRepository(db)
    job = SimpleNamespace(job_id=job_id)
    item = SimpleNamespace(item_id=item_id)

    repository.mark_item_running(item, now=now)
    repository.mark_item_succeeded(
        job,
        item,
        now=now,
        lease_expires_at=now + timedelta(minutes=1),
    )

    assert item_row.attempt_count == 1
    assert job_row.completed_count == 1


def test_item_counts_limits_failure_reason_to_one_row() -> None:
    status_query = Mock()
    status_query.filter.return_value = status_query
    status_query.group_by.return_value = status_query
    status_query.all.return_value = [("failed", 2)]
    reason_query = Mock()
    reason_query.filter.return_value = reason_query
    reason_query.order_by.return_value = reason_query
    reason_query.limit.return_value = reason_query
    reason_query.scalar.return_value = "sync.temporarily_unavailable"
    db = Mock()
    db.query.side_effect = [status_query, reason_query]

    counts = SqlAlchemyWorkerSyncRepository(db).item_counts(
        uuid4(),
        expected_total=2,
    )

    assert counts.failed == 2
    assert counts.reason_code == "sync.temporarily_unavailable"
    reason_query.limit.assert_called_once_with(1)
