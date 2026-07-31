from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from apps.shared.domain.knowledge_collection_sync import (
    JOB_LEASE_SECONDS,
    RECOVERY_BATCH_SIZE,
    SYNC_BATCH_SIZE,
    TERMINAL_JOB_STATUSES,
    TERMINAL_RETENTION_DAYS,
    retry_delay,
    terminal_job_status,
)


@dataclass(frozen=True, slots=True)
class WorkerSyncJob:
    job_id: uuid.UUID
    organization_id: uuid.UUID
    collection_id: uuid.UUID
    requested_by: uuid.UUID
    target_snapshot_revision: str
    total_count: int
    status: str
    previous_sync_state: str
    attempt_count: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    next_retry_at: datetime | None
    execution_deadline_at: datetime
    started_at: datetime | None


@dataclass(frozen=True, slots=True)
class WorkerSyncItem:
    item_id: uuid.UUID
    job_id: uuid.UUID
    organization_id: uuid.UUID
    collection_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    document_id: uuid.UUID
    position: int
    target_revision: str
    status: str
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class WorkerItemCounts:
    pending: int
    running: int
    succeeded: int
    failed: int
    skipped: int
    missing: int = 0
    excess: int = 0
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class WorkerSyncResult:
    status: Literal[
        "missing",
        "duplicate",
        "deferred",
        "queued",
        "succeeded",
        "partially_failed",
        "failed",
        "cancelled",
    ]
    reason_code: str | None = None


class SyncTargetChanged(Exception):
    pass


class SyncTargetConfigurationInvalid(Exception):
    pass


class SyncTargetTemporarilyUnavailable(Exception):
    pass


class WorkerSyncAuthorizationPort(Protocol):
    def is_allowed(self, job: WorkerSyncJob) -> bool: ...


class WorkerSyncRepositoryPort(Protocol):
    def database_now(self) -> datetime: ...

    def lock_job(self, job_id: uuid.UUID) -> WorkerSyncJob | None: ...

    def collection_is_supported(self, job: WorkerSyncJob) -> bool: ...

    def target_snapshot_matches(self, job: WorkerSyncJob) -> bool: ...

    def reset_stale_job(self, job: WorkerSyncJob, *, now: datetime) -> None: ...

    def mark_running(
        self,
        job: WorkerSyncJob,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None: ...

    def lock_owned_job(self, job_id: uuid.UUID, owner: str) -> WorkerSyncJob | None: ...

    def next_pending_item(self, job_id: uuid.UUID) -> WorkerSyncItem | None: ...

    def lock_item(self, job_id: uuid.UUID, item_id: uuid.UUID) -> WorkerSyncItem | None: ...

    def mark_item_running(self, item: WorkerSyncItem, *, now: datetime) -> None: ...

    def mark_item_succeeded(
        self,
        job: WorkerSyncJob,
        item: WorkerSyncItem,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None: ...

    def mark_item_skipped(
        self,
        job: WorkerSyncJob,
        item: WorkerSyncItem,
        *,
        now: datetime,
        reason_code: str,
        lease_expires_at: datetime,
    ) -> None: ...

    def mark_item_failed(
        self,
        job: WorkerSyncJob,
        item: WorkerSyncItem,
        *,
        now: datetime,
        reason_code: str,
        retryable: bool,
        lease_expires_at: datetime,
    ) -> bool: ...

    def mark_unfinished_items_skipped(
        self,
        job: WorkerSyncJob,
        *,
        now: datetime,
        reason_code: str,
    ) -> None: ...

    def queue_job(
        self,
        job: WorkerSyncJob,
        *,
        now: datetime,
        next_retry_at: datetime,
        reason_code: str | None,
    ) -> None: ...

    def item_counts(
        self, job_id: uuid.UUID, *, expected_total: int
    ) -> WorkerItemCounts: ...

    def finalize_job(
        self,
        job: WorkerSyncJob,
        *,
        status: str,
        now: datetime,
        reason_code: str | None,
        completed_count: int | None = None,
        failed_count: int | None = None,
        skipped_count: int | None = None,
    ) -> None: ...

    def cancel_job(
        self,
        job: WorkerSyncJob,
        *,
        now: datetime,
        reason_code: str,
    ) -> None: ...

    def recover_due(self, *, now: datetime, limit: int) -> list[uuid.UUID]: ...

    def delete_expired_terminal(self, *, before: datetime, limit: int) -> int: ...


class WorkerSyncDocumentPort(Protocol):
    def sync(self, item: WorkerSyncItem, *, actor_id: uuid.UUID) -> None: ...


class WorkerSyncAuditPort(Protocol):
    def record(
        self,
        *,
        job: WorkerSyncJob,
        action: str,
        status: str = "success",
        metadata: dict[str, object] | None = None,
    ) -> None: ...


class WorkerSyncPublisherPort(Protocol):
    def publish(self, job_id: uuid.UUID) -> None: ...


class WorkerSyncUnitOfWorkPort(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ExecuteKnowledgeCollectionSync:
    def __init__(
        self,
        *,
        authorization: WorkerSyncAuthorizationPort,
        repository: WorkerSyncRepositoryPort,
        document: WorkerSyncDocumentPort,
        audit: WorkerSyncAuditPort,
        publisher: WorkerSyncPublisherPort,
        unit_of_work: WorkerSyncUnitOfWorkPort,
    ) -> None:
        self.authorization = authorization
        self.repository = repository
        self.document = document
        self.audit = audit
        self.publisher = publisher
        self.unit_of_work = unit_of_work

    def execute(self, job_id: uuid.UUID, *, owner: str) -> WorkerSyncResult:
        claim = self._claim(job_id, owner=owner)
        if claim[0] is None:
            return claim[1]
        job = claim[0]

        for _ in range(SYNC_BATCH_SIZE):
            owned = self.repository.lock_owned_job(job.job_id, owner)
            if owned is None:
                self.unit_of_work.rollback()
                return WorkerSyncResult("duplicate", "sync.worker_interrupted")
            item = self.repository.next_pending_item(job.job_id)
            if item is None:
                self.unit_of_work.rollback()
                break
            now = self.repository.database_now()
            self.repository.mark_item_running(item, now=now)
            try:
                self.document.sync(item, actor_id=owned.requested_by)
                self.repository.mark_item_succeeded(
                    owned,
                    item,
                    now=now,
                    lease_expires_at=now + timedelta(seconds=JOB_LEASE_SECONDS),
                )
                self.unit_of_work.commit()
            except SyncTargetChanged:
                self.unit_of_work.rollback()
                self._finish_item(
                    job_id,
                    item.item_id,
                    owner=owner,
                    reason_code="sync.targets_changed",
                    retryable=False,
                    skipped=True,
                )
            except SyncTargetConfigurationInvalid:
                self.unit_of_work.rollback()
                queued = self._finish_item(
                    job_id,
                    item.item_id,
                    owner=owner,
                    reason_code="sync.configuration_invalid",
                    retryable=False,
                    skipped=False,
                )
                if queued:
                    return WorkerSyncResult("queued", "sync.configuration_invalid")
            except SyncTargetTemporarilyUnavailable:
                self.unit_of_work.rollback()
                queued = self._finish_item(
                    job_id,
                    item.item_id,
                    owner=owner,
                    reason_code="sync.temporarily_unavailable",
                    retryable=True,
                    skipped=False,
                )
                if queued:
                    return WorkerSyncResult("queued", "sync.temporarily_unavailable")
            except Exception:
                self.unit_of_work.rollback()
                self._finish_item(
                    job_id,
                    item.item_id,
                    owner=owner,
                    reason_code="sync.internal_error",
                    retryable=False,
                    skipped=False,
                )

        return self._continue_or_finalize(job_id, owner=owner)

    def _claim(
        self, job_id: uuid.UUID, *, owner: str
    ) -> tuple[WorkerSyncJob | None, WorkerSyncResult]:
        job = self.repository.lock_job(job_id)
        if job is None:
            self.unit_of_work.rollback()
            return None, WorkerSyncResult("missing")
        now = self.repository.database_now()
        if job.status in TERMINAL_JOB_STATUSES:
            self.unit_of_work.rollback()
            return None, WorkerSyncResult("duplicate", job.status)
        if job.status == "running":
            if job.lease_expires_at is not None and job.lease_expires_at > now:
                self.unit_of_work.rollback()
                return None, WorkerSyncResult("duplicate", "running")
            self.repository.reset_stale_job(job, now=now)
            self.unit_of_work.flush()
            job = self.repository.lock_job(job_id)
            if job is None:
                self.unit_of_work.rollback()
                return None, WorkerSyncResult("missing")
        if job.next_retry_at is not None and job.next_retry_at > now:
            self.unit_of_work.rollback()
            return None, WorkerSyncResult("deferred")
        if job.execution_deadline_at <= now or job.attempt_count >= job.max_attempts:
            self.repository.finalize_job(
                job,
                status="failed",
                now=now,
                reason_code="sync.timeout",
            )
            self.audit.record(
                job=job,
                action="knowledge.collection.sync.completed",
                status="failure",
                metadata={"job_status": "failed", "reason_code": "sync.timeout"},
            )
            self.unit_of_work.commit()
            return None, WorkerSyncResult("failed", "sync.timeout")
        collection_supported = self.repository.collection_is_supported(job)
        if not collection_supported or not self.authorization.is_allowed(job):
            reason = (
                "sync.not_supported"
                if not collection_supported
                else "sync.permission_revoked"
            )
            self.repository.cancel_job(job, now=now, reason_code=reason)
            self.audit.record(
                job=job,
                action="knowledge.collection.sync.cancelled",
                status="failure",
                metadata={"reason_code": reason},
            )
            self.unit_of_work.commit()
            return None, WorkerSyncResult("cancelled", reason)
        if not self.repository.target_snapshot_matches(job):
            return None, self._finalize_target_snapshot_change(job, now=now)

        first_start = job.started_at is None
        self.repository.mark_running(
            job,
            owner=owner,
            now=now,
            lease_expires_at=now + timedelta(seconds=JOB_LEASE_SECONDS),
        )
        if first_start:
            self.audit.record(
                job=job,
                action="knowledge.collection.sync.started",
            )
        self.unit_of_work.commit()
        admitted = self.repository.lock_owned_job(job_id, owner)
        if admitted is None:
            self.unit_of_work.rollback()
            return None, WorkerSyncResult("duplicate", "sync.worker_interrupted")
        self.unit_of_work.rollback()
        return admitted, WorkerSyncResult("queued")

    def _finish_item(
        self,
        job_id: uuid.UUID,
        item_id: uuid.UUID,
        *,
        owner: str,
        reason_code: str,
        retryable: bool,
        skipped: bool,
    ) -> bool:
        job = self.repository.lock_owned_job(job_id, owner)
        item = self.repository.lock_item(job_id, item_id)
        if job is None or item is None:
            self.unit_of_work.rollback()
            return False
        now = self.repository.database_now()
        lease = now + timedelta(seconds=JOB_LEASE_SECONDS)
        if skipped:
            self.repository.mark_item_skipped(
                job,
                item,
                now=now,
                reason_code=reason_code,
                lease_expires_at=lease,
            )
            self.unit_of_work.commit()
            return False
        will_retry = self.repository.mark_item_failed(
            job,
            item,
            now=now,
            reason_code=reason_code,
            retryable=retryable,
            lease_expires_at=lease,
        )
        if will_retry:
            next_retry = now + retry_delay(item.attempt_count + 1)
            self.repository.queue_job(
                job,
                now=now,
                next_retry_at=next_retry,
                reason_code=reason_code,
            )
        self.unit_of_work.commit()
        return will_retry

    def _continue_or_finalize(self, job_id: uuid.UUID, *, owner: str) -> WorkerSyncResult:
        job = self.repository.lock_owned_job(job_id, owner)
        if job is None:
            self.unit_of_work.rollback()
            return WorkerSyncResult("duplicate", "sync.worker_interrupted")
        now = self.repository.database_now()
        if not self.repository.collection_is_supported(job):
            self.repository.cancel_job(
                job,
                now=now,
                reason_code="sync.not_supported",
            )
            self.audit.record(
                job=job,
                action="knowledge.collection.sync.cancelled",
                status="failure",
                metadata={"reason_code": "sync.not_supported"},
            )
            self.unit_of_work.commit()
            return WorkerSyncResult("cancelled", "sync.not_supported")
        if not self.repository.target_snapshot_matches(job):
            return self._finalize_target_snapshot_change(job, now=now)
        counts = self.repository.item_counts(
            job_id,
            expected_total=job.total_count,
        )
        if counts.pending or counts.running:
            self.repository.queue_job(
                job,
                now=now,
                next_retry_at=now,
                reason_code=None,
            )
            self.unit_of_work.commit()
            self._publish(job_id)
            return WorkerSyncResult("queued")

        if counts.excess:
            self.repository.finalize_job(
                job,
                status="failed",
                now=now,
                reason_code="sync.internal_error",
            )
            self.audit.record(
                job=job,
                action="knowledge.collection.sync.completed",
                status="failure",
                metadata={
                    "job_status": "failed",
                    "reason_code": "sync.internal_error",
                },
            )
            self.unit_of_work.commit()
            return WorkerSyncResult("failed", "sync.internal_error")

        terminal_skipped = counts.skipped + counts.missing
        status = terminal_job_status(
            total_count=job.total_count,
            succeeded_count=counts.succeeded,
            failed_count=counts.failed,
            skipped_count=terminal_skipped,
        )
        reason = (
            "sync.targets_changed"
            if counts.missing
            else counts.reason_code if status != "succeeded" else None
        )
        self.repository.finalize_job(
            job,
            status=status,
            now=now,
            reason_code=reason,
            completed_count=counts.succeeded,
            failed_count=counts.failed,
            skipped_count=terminal_skipped,
        )
        self.audit.record(
            job=job,
            action="knowledge.collection.sync.completed",
            status="success" if status == "succeeded" else "failure",
            metadata={
                "job_status": status,
                "result_count_bucket": _count_bucket(job.total_count),
                "reason_code": reason,
            },
        )
        self.unit_of_work.commit()
        return WorkerSyncResult(status, reason)

    def _finalize_target_snapshot_change(
        self,
        job: WorkerSyncJob,
        *,
        now: datetime,
    ) -> WorkerSyncResult:
        self.repository.mark_unfinished_items_skipped(
            job,
            now=now,
            reason_code="sync.targets_changed",
        )
        counts = self.repository.item_counts(
            job.job_id,
            expected_total=job.total_count,
        )
        if counts.excess or counts.succeeded + counts.failed > job.total_count:
            status = "failed"
            reason = "sync.internal_error"
            completed_count = None
            failed_count = None
            skipped_count = None
        else:
            status = "partially_failed" if counts.succeeded else "failed"
            reason = "sync.targets_changed"
            completed_count = counts.succeeded
            failed_count = counts.failed
            skipped_count = job.total_count - counts.succeeded - counts.failed
        self.repository.finalize_job(
            job,
            status=status,
            now=now,
            reason_code=reason,
            completed_count=completed_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
        )
        self.audit.record(
            job=job,
            action="knowledge.collection.sync.completed",
            status="failure",
            metadata={
                "job_status": status,
                "reason_code": reason,
            },
        )
        self.unit_of_work.commit()
        return WorkerSyncResult(status, reason)

    def _publish(self, job_id: uuid.UUID) -> None:
        try:
            self.publisher.publish(job_id)
        except Exception:
            pass


class RecoverKnowledgeCollectionSyncJobs:
    def __init__(
        self,
        *,
        repository: WorkerSyncRepositoryPort,
        publisher: WorkerSyncPublisherPort,
        unit_of_work: WorkerSyncUnitOfWorkPort,
    ) -> None:
        self.repository = repository
        self.publisher = publisher
        self.unit_of_work = unit_of_work

    def execute(self) -> dict[str, int]:
        now = self.repository.database_now()
        try:
            job_ids = self.repository.recover_due(
                now=now,
                limit=RECOVERY_BATCH_SIZE,
            )
            deleted = self.repository.delete_expired_terminal(
                before=now - timedelta(days=TERMINAL_RETENTION_DAYS),
                limit=RECOVERY_BATCH_SIZE,
            )
            self.unit_of_work.commit()
        except Exception:
            self.unit_of_work.rollback()
            raise
        published = 0
        for job_id in job_ids:
            try:
                self.publisher.publish(job_id)
                published += 1
            except Exception:
                continue
        return {"recovered": len(job_ids), "published": published, "deleted": deleted}


def _count_bucket(value: int) -> str:
    if value <= 0:
        return "0"
    if value == 1:
        return "1"
    if value <= 10:
        return "2-10"
    if value <= 50:
        return "11-50"
    return "51-100"
