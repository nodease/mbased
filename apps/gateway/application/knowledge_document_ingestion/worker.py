from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from apps.gateway.application.knowledge_document_ingestion.progress import (
    DocumentIngestionProgressPort,
    clear_progress_projection,
)

from apps.shared.domain.knowledge_document_ingestion import (
    DEFAULT_LEASE_SECONDS,
    DEFAULT_RECOVERY_BATCH_SIZE,
    DEFAULT_TERMINAL_RETENTION_DAYS,
    TERMINAL_JOB_STATUSES,
    retry_delay,
)


@dataclass(frozen=True, slots=True)
class WorkerDocumentIngestionJob:
    job_id: uuid.UUID
    organization_id: uuid.UUID
    knowledge_base_id: uuid.UUID | None
    document_id: uuid.UUID | None
    requested_by_user_id: uuid.UUID | None
    operation: str
    generation: int
    status: str
    attempt_count: int
    max_attempts: int
    retryable: bool
    owner_token: str | None
    fencing_token: str | None
    lease_expires_at: datetime | None
    next_retry_at: datetime | None


@dataclass(frozen=True, slots=True)
class WorkerDocumentIngestionResult:
    status: Literal[
        "missing",
        "duplicate",
        "deferred",
        "succeeded",
        "retry_scheduled",
        "dead_lettered",
        "cancelled",
    ]
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveredDocumentIngestionJob:
    job_id: uuid.UUID
    document_id: uuid.UUID | None
    should_publish: bool


class DocumentIngestionLeaseLost(Exception):
    pass


class DocumentIngestionRetryableFailure(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class DocumentIngestionPermanentFailure(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class DocumentIngestionCancelled(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class WorkerDocumentIngestionRepositoryPort(Protocol):
    def database_now(self) -> datetime: ...

    def lock_worker_job(
        self, job_id: uuid.UUID
    ) -> WorkerDocumentIngestionJob | None: ...

    def mark_running(
        self,
        job: WorkerDocumentIngestionJob,
        *,
        owner_token: str,
        fencing_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> WorkerDocumentIngestionJob: ...

    def lock_owned_worker_job(
        self,
        job_id: uuid.UUID,
        *,
        owner_token: str,
        fencing_token: str,
    ) -> WorkerDocumentIngestionJob | None: ...

    def mark_retry_scheduled(
        self,
        job: WorkerDocumentIngestionJob,
        *,
        now: datetime,
        next_retry_at: datetime,
        reason_code: str,
    ) -> None: ...

    def mark_dead_lettered(
        self,
        job: WorkerDocumentIngestionJob,
        *,
        now: datetime,
        reason_code: str,
        retryable: bool,
    ) -> None: ...

    def mark_cancelled(
        self,
        job: WorkerDocumentIngestionJob,
        *,
        now: datetime,
        reason_code: str,
    ) -> None: ...

    def recover_due(
        self, *, now: datetime, limit: int
    ) -> list[RecoveredDocumentIngestionJob]: ...

    def delete_expired_terminal(self, *, before: datetime, limit: int) -> int: ...


class WorkerDocumentIngestionAuthorizationPort(Protocol):
    def is_allowed(self, job: WorkerDocumentIngestionJob) -> bool: ...


class WorkerDocumentIngestionRunnerPort(Protocol):
    def run(self, job: WorkerDocumentIngestionJob) -> uuid.UUID | None: ...


class WorkerDocumentIngestionPublisherPort(Protocol):
    def publish(self, job_id: uuid.UUID) -> None: ...


class WorkerDocumentIngestionUnitOfWorkPort(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ExecuteDocumentIngestionJob:
    def __init__(
        self,
        *,
        repository: WorkerDocumentIngestionRepositoryPort,
        authorization: WorkerDocumentIngestionAuthorizationPort,
        runner: WorkerDocumentIngestionRunnerPort,
        unit_of_work: WorkerDocumentIngestionUnitOfWorkPort,
        progress: DocumentIngestionProgressPort | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.runner = runner
        self.unit_of_work = unit_of_work
        self.progress = progress
        self.lease_seconds = lease_seconds

    def execute(
        self,
        job_id: uuid.UUID,
        *,
        owner_token: str,
    ) -> WorkerDocumentIngestionResult:
        job = self.repository.lock_worker_job(job_id)
        if job is None:
            self.unit_of_work.rollback()
            return WorkerDocumentIngestionResult("missing")
        if job.status in TERMINAL_JOB_STATUSES:
            self.unit_of_work.rollback()
            return WorkerDocumentIngestionResult("duplicate")

        now = self.repository.database_now()
        if job.status == "running" and job.lease_expires_at and job.lease_expires_at > now:
            self.unit_of_work.rollback()
            return WorkerDocumentIngestionResult("duplicate")
        if job.status in {"pending", "retry_scheduled"} and job.next_retry_at:
            if job.next_retry_at > now:
                self.unit_of_work.rollback()
                return WorkerDocumentIngestionResult("deferred")

        if job.document_id is None or job.knowledge_base_id is None:
            self.repository.mark_cancelled(
                job, now=now, reason_code="ingestion.document_missing"
            )
            self._commit_and_clear_progress(job)
            return WorkerDocumentIngestionResult(
                "cancelled", "ingestion.document_missing"
            )
        if job.requested_by_user_id is None or not self.authorization.is_allowed(job):
            self.repository.mark_cancelled(
                job, now=now, reason_code="ingestion.authorization_revoked"
            )
            self._commit_and_clear_progress(job)
            return WorkerDocumentIngestionResult(
                "cancelled", "ingestion.authorization_revoked"
            )
        if job.attempt_count >= job.max_attempts:
            self.repository.mark_dead_lettered(
                job,
                now=now,
                reason_code="ingestion.worker_interrupted",
                retryable=True,
            )
            self._commit_and_clear_progress(job)
            return WorkerDocumentIngestionResult(
                "dead_lettered", "ingestion.worker_interrupted"
            )

        fencing_token = str(uuid.uuid4())
        admitted = self.repository.mark_running(
            job,
            owner_token=owner_token,
            fencing_token=fencing_token,
            now=now,
            lease_expires_at=now + timedelta(seconds=self.lease_seconds),
        )
        self.unit_of_work.commit()

        try:
            self.runner.run(admitted)
            return WorkerDocumentIngestionResult("succeeded")
        except DocumentIngestionLeaseLost:
            self.unit_of_work.rollback()
            return WorkerDocumentIngestionResult(
                "duplicate", "ingestion.lease_lost"
            )
        except DocumentIngestionCancelled as exc:
            return self._finish_failure(
                admitted,
                reason_code=exc.reason_code,
                kind="cancelled",
            )
        except DocumentIngestionRetryableFailure as exc:
            return self._finish_failure(
                admitted,
                reason_code=exc.reason_code,
                kind="retryable",
            )
        except DocumentIngestionPermanentFailure as exc:
            return self._finish_failure(
                admitted,
                reason_code=exc.reason_code,
                kind="permanent",
            )
        except Exception:
            return self._finish_failure(
                admitted,
                reason_code="ingestion.internal_error",
                kind="permanent",
            )

    def _finish_failure(
        self,
        admitted: WorkerDocumentIngestionJob,
        *,
        reason_code: str,
        kind: Literal["cancelled", "retryable", "permanent"],
    ) -> WorkerDocumentIngestionResult:
        job = self.repository.lock_owned_worker_job(
            admitted.job_id,
            owner_token=admitted.owner_token or "",
            fencing_token=admitted.fencing_token or "",
        )
        if job is None:
            self.unit_of_work.rollback()
            return WorkerDocumentIngestionResult(
                "duplicate", "ingestion.lease_lost"
            )
        now = self.repository.database_now()
        if kind == "cancelled":
            self.repository.mark_cancelled(job, now=now, reason_code=reason_code)
            self._commit_and_clear_progress(job)
            return WorkerDocumentIngestionResult("cancelled", reason_code)
        if kind == "retryable" and job.attempt_count < job.max_attempts:
            self.repository.mark_retry_scheduled(
                job,
                now=now,
                next_retry_at=now
                + retry_delay(job_id=job.job_id, attempt_count=job.attempt_count),
                reason_code=reason_code,
            )
            self._commit_and_clear_progress(job)
            return WorkerDocumentIngestionResult("retry_scheduled", reason_code)

        self.repository.mark_dead_lettered(
            job,
            now=now,
            reason_code=reason_code,
            retryable=(kind == "retryable"),
        )
        self._commit_and_clear_progress(job)
        return WorkerDocumentIngestionResult("dead_lettered", reason_code)

    def _commit_and_clear_progress(self, job: WorkerDocumentIngestionJob) -> None:
        self.unit_of_work.commit()
        clear_progress_projection(self.progress, job.document_id)


class RecoverDocumentIngestionJobs:
    def __init__(
        self,
        *,
        repository: WorkerDocumentIngestionRepositoryPort,
        publisher: WorkerDocumentIngestionPublisherPort,
        unit_of_work: WorkerDocumentIngestionUnitOfWorkPort,
        progress: DocumentIngestionProgressPort | None = None,
    ) -> None:
        self.repository = repository
        self.publisher = publisher
        self.unit_of_work = unit_of_work
        self.progress = progress

    def execute(self) -> dict[str, int]:
        now = self.repository.database_now()
        try:
            recoveries = self.repository.recover_due(
                now=now,
                limit=DEFAULT_RECOVERY_BATCH_SIZE,
            )
            deleted = self.repository.delete_expired_terminal(
                before=now - timedelta(days=DEFAULT_TERMINAL_RETENTION_DAYS),
                limit=DEFAULT_RECOVERY_BATCH_SIZE,
            )
            self.unit_of_work.commit()
        except Exception:
            self.unit_of_work.rollback()
            raise

        for recovery in recoveries:
            clear_progress_projection(self.progress, recovery.document_id)

        published = 0
        for recovery in recoveries:
            if not recovery.should_publish:
                continue
            try:
                self.publisher.publish(recovery.job_id)
                published += 1
            except Exception:
                continue
        return {
            "recovered": len(recoveries),
            "published": published,
            "deleted": deleted,
        }
