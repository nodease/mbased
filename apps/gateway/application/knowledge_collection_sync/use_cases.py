from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, Sequence

from apps.shared.domain.knowledge_collection_sync import (
    JOB_DEADLINE_SECONDS,
    MAX_SYNC_TARGETS,
)
from apps.shared.services.knowledge_collection_sync_targets import (
    CollectionSyncTarget,
    CollectionSyncTargetScan,
)


@dataclass(frozen=True, slots=True)
class CollectionSyncCommand:
    actor_id: uuid.UUID
    organization_id: uuid.UUID
    collection_id: uuid.UUID
    idempotency_key: uuid.UUID


@dataclass(frozen=True, slots=True)
class CollectionSyncStatusQuery:
    actor_id: uuid.UUID
    organization_id: uuid.UUID
    collection_id: uuid.UUID
    job_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class CollectionSyncCollectionSnapshot:
    collection_id: uuid.UUID
    lifecycle_state: str
    sync_state: str
    is_system_managed: bool
    is_source_managed: bool


@dataclass(frozen=True, slots=True)
class CollectionSyncJobSnapshot:
    job_id: uuid.UUID
    collection_id: uuid.UUID
    status: str
    total_count: int
    completed_count: int
    failed_count: int
    skipped_count: int
    retryable: bool
    safe_reason_code: str | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class CollectionSyncRequestResult:
    job: CollectionSyncJobSnapshot
    reused: bool
    dispatch_deferred: bool


class CollectionSyncHidden(Exception):
    pass


class CollectionSyncPermissionDenied(Exception):
    pass


class CollectionSyncPolicyBlocked(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class CollectionSyncPersistenceFailed(Exception):
    pass


class CollectionSyncAuthorizationPort(Protocol):
    def is_allowed(
        self,
        *,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
    ) -> bool: ...


class CollectionSyncRepositoryPort(Protocol):
    def database_now(self) -> datetime: ...

    def lock_collection(
        self, organization_id: uuid.UUID, collection_id: uuid.UUID
    ) -> CollectionSyncCollectionSnapshot | None: ...

    def get_collection(
        self, organization_id: uuid.UUID, collection_id: uuid.UUID
    ) -> CollectionSyncCollectionSnapshot | None: ...

    def find_by_request_hash(
        self,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        request_key_hash: str,
    ) -> CollectionSyncJobSnapshot | None: ...

    def find_active(
        self, organization_id: uuid.UUID, collection_id: uuid.UUID
    ) -> CollectionSyncJobSnapshot | None: ...

    def scan_targets(
        self,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        *,
        limit: int,
    ) -> CollectionSyncTargetScan: ...

    def create_job(
        self,
        *,
        command: CollectionSyncCommand,
        request_key_hash: str,
        target_snapshot_revision: str,
        previous_sync_state: str,
        targets: Sequence[CollectionSyncTarget],
        now: datetime,
        execution_deadline_at: datetime,
    ) -> CollectionSyncJobSnapshot: ...

    def set_collection_sync_state(
        self,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        sync_state: str,
        *,
        now: datetime,
    ) -> None: ...

    def latest_job(
        self, organization_id: uuid.UUID, collection_id: uuid.UUID
    ) -> CollectionSyncJobSnapshot | None: ...

    def get_job(
        self,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> CollectionSyncJobSnapshot | None: ...


class CollectionSyncAuditPort(Protocol):
    def record(
        self,
        *,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        action: str,
        job_id: uuid.UUID | None = None,
        metadata: dict[str, object] | None = None,
        status: str = "success",
    ) -> None: ...


class CollectionSyncPublisherPort(Protocol):
    def publish(self, job_id: uuid.UUID) -> None: ...


class CollectionSyncUnitOfWorkPort(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class RequestKnowledgeCollectionSync:
    def __init__(
        self,
        *,
        authorization: CollectionSyncAuthorizationPort,
        repository: CollectionSyncRepositoryPort,
        audit: CollectionSyncAuditPort,
        publisher: CollectionSyncPublisherPort,
        unit_of_work: CollectionSyncUnitOfWorkPort,
    ) -> None:
        self.authorization = authorization
        self.repository = repository
        self.audit = audit
        self.publisher = publisher
        self.unit_of_work = unit_of_work

    def execute(self, command: CollectionSyncCommand) -> CollectionSyncRequestResult:
        try:
            collection = self.repository.lock_collection(
                command.organization_id, command.collection_id
            )
            if collection is None:
                self.unit_of_work.rollback()
                raise CollectionSyncHidden()
            if not self.authorization.is_allowed(
                actor_id=command.actor_id,
                organization_id=command.organization_id,
                collection_id=command.collection_id,
            ):
                self._record_denial(command, "permission.denied")
                raise CollectionSyncPermissionDenied()
            self._require_supported_collection(command, collection)

            request_key_hash = _request_key_hash(command.idempotency_key)
            existing = self.repository.find_by_request_hash(
                command.organization_id,
                command.collection_id,
                request_key_hash,
            )
            if existing is None:
                existing = self.repository.find_active(
                    command.organization_id, command.collection_id
                )
            if existing is not None:
                self.audit.record(
                    actor_id=command.actor_id,
                    organization_id=command.organization_id,
                    collection_id=command.collection_id,
                    job_id=existing.job_id,
                    action="knowledge.collection.sync.reused",
                    metadata={"job_status": existing.status},
                )
                self.unit_of_work.commit()
                deferred = self._publish_if_active(existing)
                return CollectionSyncRequestResult(existing, True, deferred)

            scan = self.repository.scan_targets(
                command.organization_id,
                command.collection_id,
                limit=MAX_SYNC_TARGETS + 1,
            )
            blocking_reason = scan.blocking_reason
            if len(scan.targets) > MAX_SYNC_TARGETS:
                blocking_reason = "sync.target_limit_exceeded"
            if blocking_reason is not None:
                self._record_policy_block(command, blocking_reason)
                raise CollectionSyncPolicyBlocked(blocking_reason)

            now = self.repository.database_now()
            job = self.repository.create_job(
                command=command,
                request_key_hash=request_key_hash,
                target_snapshot_revision=scan.snapshot_revision(command.collection_id),
                previous_sync_state=collection.sync_state,
                targets=scan.targets,
                now=now,
                execution_deadline_at=now + timedelta(seconds=JOB_DEADLINE_SECONDS),
            )
            self.repository.set_collection_sync_state(
                command.organization_id,
                command.collection_id,
                "pending",
                now=now,
            )
            self.audit.record(
                actor_id=command.actor_id,
                organization_id=command.organization_id,
                collection_id=command.collection_id,
                job_id=job.job_id,
                action="knowledge.collection.sync.requested",
                metadata={"target_count_bucket": _count_bucket(len(scan.targets))},
            )
            self.unit_of_work.flush()
            self.unit_of_work.commit()
        except (
            CollectionSyncHidden,
            CollectionSyncPermissionDenied,
            CollectionSyncPolicyBlocked,
        ):
            raise
        except Exception as exc:
            self.unit_of_work.rollback()
            raise CollectionSyncPersistenceFailed() from exc

        deferred = self._publish(job.job_id)
        return CollectionSyncRequestResult(job, False, deferred)

    def _require_supported_collection(
        self,
        command: CollectionSyncCommand,
        collection: CollectionSyncCollectionSnapshot,
    ) -> None:
        if (
            collection.lifecycle_state != "active"
            or collection.sync_state == "source_deleted"
            or collection.is_system_managed
            or collection.is_source_managed
        ):
            self._record_policy_block(command, "sync.not_supported")
            raise CollectionSyncPolicyBlocked("sync.not_supported")

    def _record_denial(self, command: CollectionSyncCommand, reason: str) -> None:
        self.audit.record(
            actor_id=command.actor_id,
            organization_id=command.organization_id,
            collection_id=command.collection_id,
            action="knowledge.collection.sync.denied",
            metadata={"reason_code": reason},
            status="failure",
        )
        self.unit_of_work.commit()

    def _record_policy_block(self, command: CollectionSyncCommand, reason: str) -> None:
        self.audit.record(
            actor_id=command.actor_id,
            organization_id=command.organization_id,
            collection_id=command.collection_id,
            action="knowledge.collection.sync.denied",
            metadata={"reason_code": reason},
            status="failure",
        )
        self.unit_of_work.commit()

    def _publish_if_active(self, job: CollectionSyncJobSnapshot) -> bool:
        if job.status not in {"queued", "running"}:
            return False
        return self._publish(job.job_id)

    def _publish(self, job_id: uuid.UUID) -> bool:
        try:
            self.publisher.publish(job_id)
        except Exception:
            return True
        return False


class ReadKnowledgeCollectionSyncStatus:
    def __init__(
        self,
        *,
        authorization: CollectionSyncAuthorizationPort,
        repository: CollectionSyncRepositoryPort,
        unit_of_work: CollectionSyncUnitOfWorkPort,
    ) -> None:
        self.authorization = authorization
        self.repository = repository
        self.unit_of_work = unit_of_work

    def execute(
        self, query: CollectionSyncStatusQuery
    ) -> CollectionSyncJobSnapshot | None:
        collection = self.repository.get_collection(
            query.organization_id, query.collection_id
        )
        if collection is None:
            self.unit_of_work.rollback()
            raise CollectionSyncHidden()
        if not self.authorization.is_allowed(
            actor_id=query.actor_id,
            organization_id=query.organization_id,
            collection_id=query.collection_id,
        ):
            self.unit_of_work.rollback()
            raise CollectionSyncPermissionDenied()
        job = (
            self.repository.latest_job(query.organization_id, query.collection_id)
            if query.job_id is None
            else self.repository.get_job(
                query.organization_id, query.collection_id, query.job_id
            )
        )
        self.unit_of_work.rollback()
        if query.job_id is not None and job is None:
            raise CollectionSyncHidden()
        return job


def _request_key_hash(value: uuid.UUID) -> str:
    return hashlib.sha256(b"kc-sync-request-v1\x00" + value.bytes).hexdigest()


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
