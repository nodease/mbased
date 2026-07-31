from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from apps.gateway.application.knowledge_document_ingestion.worker import (
    DocumentIngestionLeaseLost,
    DocumentIngestionRetryableFailure,
    ExecuteDocumentIngestionJob,
    RecoveredDocumentIngestionJob,
    RecoverDocumentIngestionJobs,
    WorkerDocumentIngestionJob,
)


NOW = datetime(2026, 7, 16, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self, job: WorkerDocumentIngestionJob | None) -> None:
        self.job = job
        self.transition: tuple[str, str | None] | None = None
        self.recovered = [
            RecoveredDocumentIngestionJob(
                job_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                should_publish=True,
            ),
            RecoveredDocumentIngestionJob(
                job_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                should_publish=False,
            ),
        ]
        self.deleted = 2

    def database_now(self):
        return NOW

    def lock_worker_job(self, job_id):
        if self.job and self.job.job_id == job_id:
            return self.job
        return None

    def mark_running(
        self,
        job,
        *,
        owner_token,
        fencing_token,
        now,
        lease_expires_at,
    ):
        self.job = replace(
            job,
            status="running",
            attempt_count=job.attempt_count + 1,
            owner_token=owner_token,
            fencing_token=fencing_token,
            lease_expires_at=lease_expires_at,
            next_retry_at=None,
        )
        return self.job

    def lock_owned_worker_job(self, job_id, *, owner_token, fencing_token):
        if (
            self.job
            and self.job.job_id == job_id
            and self.job.status == "running"
            and self.job.owner_token == owner_token
            and self.job.fencing_token == fencing_token
        ):
            return self.job
        return None

    def mark_retry_scheduled(self, job, *, now, next_retry_at, reason_code):
        self.transition = ("retry_scheduled", reason_code)
        self.job = replace(
            job,
            status="retry_scheduled",
            owner_token=None,
            fencing_token=None,
            lease_expires_at=None,
            next_retry_at=next_retry_at,
        )

    def mark_dead_lettered(self, job, *, now, reason_code, retryable):
        self.transition = ("dead_lettered", reason_code)
        self.job = replace(
            job,
            status="dead_lettered",
            retryable=retryable,
            owner_token=None,
            fencing_token=None,
            lease_expires_at=None,
        )

    def mark_cancelled(self, job, *, now, reason_code):
        self.transition = ("cancelled", reason_code)
        self.job = replace(
            job,
            status="cancelled",
            owner_token=None,
            fencing_token=None,
            lease_expires_at=None,
        )

    def recover_due(self, *, now, limit):
        return self.recovered[:limit]

    def delete_expired_terminal(self, *, before, limit):
        return min(self.deleted, limit)


class FakeAuthorization:
    def __init__(self, allowed=True) -> None:
        self.allowed = allowed

    def is_allowed(self, job):
        return self.allowed


class FakeRunner:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def run(self, job):
        self.calls += 1
        if self.error:
            raise self.error
        return uuid.uuid4()


class FakePublisher:
    def __init__(self, fails=False) -> None:
        self.fails = fails
        self.published = []

    def publish(self, job_id):
        self.published.append(job_id)
        if self.fails:
            raise RuntimeError("broker unavailable")


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeProgress:
    def __init__(self, error: Exception | None = None) -> None:
        self.cleared = []
        self.error = error

    def clear(self, document_id):
        self.cleared.append(document_id)
        if self.error is not None:
            raise self.error


def _job(**updates) -> WorkerDocumentIngestionJob:
    base = WorkerDocumentIngestionJob(
        job_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        requested_by_user_id=uuid.uuid4(),
        operation="process",
        generation=1,
        status="pending",
        attempt_count=0,
        max_attempts=3,
        retryable=True,
        owner_token=None,
        fencing_token=None,
        lease_expires_at=None,
        next_retry_at=NOW,
    )
    return replace(base, **updates)


def _execute(repository, authorization=None, runner=None, uow=None, progress=None):
    return ExecuteDocumentIngestionJob(
        repository=repository,
        authorization=authorization or FakeAuthorization(),
        runner=runner or FakeRunner(),
        unit_of_work=uow or FakeUnitOfWork(),
        progress=progress,
    )


def test_duplicate_terminal_delivery_does_not_run_ingestion() -> None:
    job = _job(status="succeeded")
    repository = FakeRepository(job)
    runner = FakeRunner()

    result = _execute(repository, runner=runner).execute(job.job_id, owner_token="o")

    assert result.status == "duplicate"
    assert runner.calls == 0


def test_future_retry_is_deferred_without_claim() -> None:
    job = _job(status="retry_scheduled", next_retry_at=NOW + timedelta(minutes=1))
    repository = FakeRepository(job)
    runner = FakeRunner()

    result = _execute(repository, runner=runner).execute(job.job_id, owner_token="o")

    assert result.status == "deferred"
    assert runner.calls == 0


def test_worker_start_authorization_revoke_cancels_before_runner() -> None:
    job = _job()
    repository = FakeRepository(job)
    runner = FakeRunner()

    result = _execute(
        repository,
        authorization=FakeAuthorization(False),
        runner=runner,
    ).execute(job.job_id, owner_token="o")

    assert result.status == "cancelled"
    assert result.reason_code == "ingestion.authorization_revoked"
    assert runner.calls == 0


def test_admitted_job_runs_once() -> None:
    job = _job()
    repository = FakeRepository(job)
    runner = FakeRunner()

    result = _execute(repository, runner=runner).execute(job.job_id, owner_token="o")

    assert result.status == "succeeded"
    assert runner.calls == 1
    assert repository.job is not None
    assert repository.job.attempt_count == 1


def test_retryable_failure_waits_for_due_recovery_publish() -> None:
    job = _job()
    repository = FakeRepository(job)
    runner = FakeRunner(
        DocumentIngestionRetryableFailure("ingestion.source_temporarily_unavailable")
    )
    progress = FakeProgress()

    result = _execute(
        repository,
        runner=runner,
        progress=progress,
    ).execute(job.job_id, owner_token="o")

    assert result.status == "retry_scheduled"
    assert repository.transition == (
        "retry_scheduled",
        "ingestion.source_temporarily_unavailable",
    )
    assert progress.cleared == [job.document_id]


def test_last_retryable_attempt_is_dead_lettered() -> None:
    job = _job(attempt_count=2, max_attempts=3)
    repository = FakeRepository(job)
    runner = FakeRunner(DocumentIngestionRetryableFailure("ingestion.timeout"))

    result = _execute(repository, runner=runner).execute(job.job_id, owner_token="o")

    assert result.status == "dead_lettered"
    assert repository.transition == ("dead_lettered", "ingestion.timeout")


def test_lease_lost_does_not_apply_terminal_transition() -> None:
    job = _job()
    repository = FakeRepository(job)
    runner = FakeRunner(DocumentIngestionLeaseLost())
    progress = FakeProgress()

    result = _execute(repository, runner=runner, progress=progress).execute(
        job.job_id, owner_token="o"
    )

    assert result.status == "duplicate"
    assert repository.transition is None
    assert progress.cleared == []


def test_progress_projection_failure_does_not_change_committed_retry_result() -> None:
    job = _job()
    repository = FakeRepository(job)
    runner = FakeRunner(
        DocumentIngestionRetryableFailure("ingestion.source_temporarily_unavailable")
    )
    uow = FakeUnitOfWork()
    progress = FakeProgress(RuntimeError("redis unavailable"))

    result = _execute(
        repository,
        runner=runner,
        uow=uow,
        progress=progress,
    ).execute(job.job_id, owner_token="o")

    assert result.status == "retry_scheduled"
    assert repository.transition == (
        "retry_scheduled",
        "ingestion.source_temporarily_unavailable",
    )
    assert uow.commits == 2
    assert progress.cleared == [job.document_id]


def test_recovery_commits_before_publish_and_tolerates_publish_failure() -> None:
    repository = FakeRepository(None)
    publisher = FakePublisher(fails=True)
    uow = FakeUnitOfWork()
    progress = FakeProgress()

    result = RecoverDocumentIngestionJobs(
        repository=repository,
        publisher=publisher,
        unit_of_work=uow,
        progress=progress,
    ).execute()

    assert result == {"recovered": 2, "published": 0, "deleted": 2}
    assert uow.commits == 1
    assert publisher.published == [repository.recovered[0].job_id]
    assert progress.cleared == [
        recovery.document_id for recovery in repository.recovered
    ]
