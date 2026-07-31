from __future__ import annotations

import hmac
import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from apps.shared.db.models.knowledge import (
    KnowledgeCollection,
    KnowledgeCollectionSyncJob,
    KnowledgeCollectionSyncJobItem,
)
from apps.shared.domain.knowledge_collection_sync import (
    MAX_SYNC_TARGETS,
    TERMINAL_JOB_STATUSES,
    collection_sync_state_for_job,
    safe_reason_code,
)
from apps.shared.services.knowledge_collection_sync_targets import (
    scan_collection_sync_targets,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.permissions import (
    get_effective_knowledge_domain_actions,
    has_active_organization_membership,
    has_organization_manager_permission,
)
from apps.workflow_engine.application.knowledge_collection_sync import (
    WorkerItemCounts,
    WorkerSyncJob,
    WorkerSyncItem,
)


class SqlAlchemyWorkerSyncUnitOfWork:
    def __init__(self, db: Session) -> None:
        self.db = db

    def flush(self) -> None:
        self.db.flush()

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


class SqlAlchemyWorkerSyncAuthorization:
    def __init__(self, db: Session) -> None:
        self.db = db

    def is_allowed(self, job: WorkerSyncJob) -> bool:
        if has_organization_manager_permission(
            self.db, job.requested_by, job.organization_id
        ):
            return True
        if not has_active_organization_membership(
            self.db, job.requested_by, job.organization_id
        ):
            return False
        if "sync_manage" in get_effective_knowledge_domain_actions(
            self.db, job.requested_by, job.organization_id
        ):
            return True
        collection = (
            self.db.query(KnowledgeCollection)
            .filter(
                KnowledgeCollection.id == job.collection_id,
                KnowledgeCollection.organization_id == job.organization_id,
            )
            .first()
        )
        if collection is None:
            return False
        return KnowledgePermissionHelper(
            self.db,
            user_id=job.requested_by,
            organization_id=job.organization_id,
        ).evaluate_collection_action(
            collection, "sync", include_archived=True
        ).allowed


class SqlAlchemyWorkerSyncRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def database_now(self) -> datetime:
        value = self.db.query(func.now()).scalar()
        return value or datetime.now(timezone.utc)

    def lock_job(self, job_id: uuid.UUID) -> WorkerSyncJob | None:
        row = (
            self.db.query(KnowledgeCollectionSyncJob)
            .filter(KnowledgeCollectionSyncJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        return self._job(row)

    def collection_is_supported(self, job: WorkerSyncJob) -> bool:
        return (
            self.db.query(KnowledgeCollection.id)
            .filter(
                KnowledgeCollection.id == job.collection_id,
                KnowledgeCollection.organization_id == job.organization_id,
                KnowledgeCollection.lifecycle_state == "active",
                KnowledgeCollection.sync_state != "source_deleted",
                KnowledgeCollection.is_system_managed.is_(False),
                KnowledgeCollection.source_identity_id.is_(None),
                KnowledgeCollection.source_connector_ref.is_(None),
            )
            .with_for_update()
            .first()
            is not None
        )

    def target_snapshot_matches(self, job: WorkerSyncJob) -> bool:
        scan = scan_collection_sync_targets(
            self.db,
            job.organization_id,
            job.collection_id,
            limit=MAX_SYNC_TARGETS + 1,
        )
        return scan.is_supported and hmac.compare_digest(
            scan.snapshot_revision(job.collection_id),
            job.target_snapshot_revision,
        )

    def reset_stale_job(self, job: WorkerSyncJob, *, now: datetime) -> None:
        row = self._required_job(job.job_id)
        self.db.query(KnowledgeCollectionSyncJobItem).filter(
            KnowledgeCollectionSyncJobItem.job_id == job.job_id,
            KnowledgeCollectionSyncJobItem.organization_id == job.organization_id,
            KnowledgeCollectionSyncJobItem.status == "running",
        ).update(
            {
                KnowledgeCollectionSyncJobItem.status: "pending",
                KnowledgeCollectionSyncJobItem.started_at: None,
                KnowledgeCollectionSyncJobItem.updated_at: now,
            },
            synchronize_session=False,
        )
        row.status = "queued"
        row.lease_owner = None
        row.lease_expires_at = None
        row.next_retry_at = now
        row.safe_reason_code = "sync.worker_interrupted"
        row.updated_at = now

    def mark_running(
        self,
        job: WorkerSyncJob,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        row = self._required_job(job.job_id)
        row.status = "running"
        row.lease_owner = owner
        row.lease_expires_at = lease_expires_at
        row.next_retry_at = None
        row.safe_reason_code = None
        row.attempt_count += 1
        row.started_at = row.started_at or now
        row.updated_at = now
        self._set_collection_state(row, "syncing", now=now)

    def lock_owned_job(self, job_id: uuid.UUID, owner: str) -> WorkerSyncJob | None:
        row = (
            self.db.query(KnowledgeCollectionSyncJob)
            .filter(
                KnowledgeCollectionSyncJob.id == job_id,
                KnowledgeCollectionSyncJob.status == "running",
                KnowledgeCollectionSyncJob.lease_owner == owner,
            )
            .with_for_update()
            .one_or_none()
        )
        return self._job(row)

    def next_pending_item(self, job_id: uuid.UUID) -> WorkerSyncItem | None:
        row = (
            self.db.query(KnowledgeCollectionSyncJobItem)
            .filter(
                KnowledgeCollectionSyncJobItem.job_id == job_id,
                KnowledgeCollectionSyncJobItem.status == "pending",
            )
            .order_by(KnowledgeCollectionSyncJobItem.position.asc())
            .with_for_update()
            .first()
        )
        return self._item(row)

    def lock_item(
        self, job_id: uuid.UUID, item_id: uuid.UUID
    ) -> WorkerSyncItem | None:
        row = (
            self.db.query(KnowledgeCollectionSyncJobItem)
            .filter(
                KnowledgeCollectionSyncJobItem.id == item_id,
                KnowledgeCollectionSyncJobItem.job_id == job_id,
            )
            .with_for_update()
            .one_or_none()
        )
        return self._item(row)

    def mark_item_running(self, item: WorkerSyncItem, *, now: datetime) -> None:
        row = self._required_item(item.item_id)
        row.status = "running"
        row.started_at = row.started_at or now
        row.safe_reason_code = None
        row.updated_at = now

    def mark_item_succeeded(
        self,
        job: WorkerSyncJob,
        item: WorkerSyncItem,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        row = self._required_item(item.item_id)
        job_row = self._required_job(job.job_id)
        row.attempt_count += 1
        row.status = "succeeded"
        row.retryable = False
        row.safe_reason_code = None
        row.completed_at = now
        row.updated_at = now
        job_row.completed_count += 1
        job_row.lease_expires_at = lease_expires_at
        job_row.updated_at = now

    def mark_item_skipped(
        self,
        job: WorkerSyncJob,
        item: WorkerSyncItem,
        *,
        now: datetime,
        reason_code: str,
        lease_expires_at: datetime,
    ) -> None:
        row = self._required_item(item.item_id)
        job_row = self._required_job(job.job_id)
        row.attempt_count += 1
        row.status = "skipped"
        row.retryable = False
        row.safe_reason_code = safe_reason_code(reason_code)
        row.completed_at = now
        row.updated_at = now
        job_row.skipped_count += 1
        job_row.lease_expires_at = lease_expires_at
        job_row.updated_at = now

    def mark_item_failed(
        self,
        job: WorkerSyncJob,
        item: WorkerSyncItem,
        *,
        now: datetime,
        reason_code: str,
        retryable: bool,
        lease_expires_at: datetime,
    ) -> bool:
        row = self._required_item(item.item_id)
        job_row = self._required_job(job.job_id)
        row.attempt_count += 1
        will_retry = retryable and row.attempt_count < row.max_attempts
        row.status = "pending" if will_retry else "failed"
        row.retryable = will_retry
        row.safe_reason_code = safe_reason_code(reason_code)
        row.completed_at = None if will_retry else now
        row.updated_at = now
        if not will_retry:
            job_row.failed_count += 1
        job_row.lease_expires_at = lease_expires_at
        job_row.updated_at = now
        return will_retry

    def mark_unfinished_items_skipped(
        self,
        job: WorkerSyncJob,
        *,
        now: datetime,
        reason_code: str,
    ) -> None:
        self.db.query(KnowledgeCollectionSyncJobItem).filter(
            KnowledgeCollectionSyncJobItem.job_id == job.job_id,
            KnowledgeCollectionSyncJobItem.organization_id == job.organization_id,
            KnowledgeCollectionSyncJobItem.status.in_(["pending", "running"]),
        ).update(
            {
                KnowledgeCollectionSyncJobItem.status: "skipped",
                KnowledgeCollectionSyncJobItem.retryable: False,
                KnowledgeCollectionSyncJobItem.safe_reason_code: safe_reason_code(
                    reason_code
                ),
                KnowledgeCollectionSyncJobItem.completed_at: now,
                KnowledgeCollectionSyncJobItem.updated_at: now,
            },
            synchronize_session=False,
        )

    def queue_job(
        self,
        job: WorkerSyncJob,
        *,
        now: datetime,
        next_retry_at: datetime,
        reason_code: str | None,
    ) -> None:
        row = self._required_job(job.job_id)
        row.status = "queued"
        row.lease_owner = None
        row.lease_expires_at = None
        row.next_retry_at = next_retry_at
        row.safe_reason_code = safe_reason_code(reason_code)
        row.updated_at = now
        self._set_collection_state(row, "pending", now=now)

    def item_counts(
        self, job_id: uuid.UUID, *, expected_total: int
    ) -> WorkerItemCounts:
        rows = (
            self.db.query(
                KnowledgeCollectionSyncJobItem.status,
                func.count(KnowledgeCollectionSyncJobItem.id),
            )
            .filter(KnowledgeCollectionSyncJobItem.job_id == job_id)
            .group_by(KnowledgeCollectionSyncJobItem.status)
            .all()
        )
        counts = {status: int(count) for status, count in rows}
        reason = (
            self.db.query(KnowledgeCollectionSyncJobItem.safe_reason_code)
            .filter(
                KnowledgeCollectionSyncJobItem.job_id == job_id,
                KnowledgeCollectionSyncJobItem.status.in_(["failed", "skipped"]),
                KnowledgeCollectionSyncJobItem.safe_reason_code.is_not(None),
            )
            .order_by(KnowledgeCollectionSyncJobItem.position.asc())
            .limit(1)
            .scalar()
        )
        row_total = sum(counts.values())
        missing = max(expected_total - row_total, 0)
        excess = max(row_total - expected_total, 0)
        return WorkerItemCounts(
            pending=counts.get("pending", 0),
            running=counts.get("running", 0),
            succeeded=counts.get("succeeded", 0),
            failed=counts.get("failed", 0),
            skipped=counts.get("skipped", 0),
            missing=missing,
            excess=excess,
            reason_code=(
                "sync.targets_changed" if missing else safe_reason_code(reason)
            ),
        )

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
    ) -> None:
        if status not in TERMINAL_JOB_STATUSES - {"cancelled"}:
            raise ValueError("unsupported terminal sync status")
        row = self._required_job(job.job_id)
        row.status = status
        row.retryable = False
        row.safe_reason_code = safe_reason_code(reason_code)
        row.lease_owner = None
        row.lease_expires_at = None
        row.next_retry_at = None
        row.completed_at = now
        row.updated_at = now
        supplied_counts = (completed_count, failed_count, skipped_count)
        if any(value is not None for value in supplied_counts):
            if any(value is None for value in supplied_counts):
                raise ValueError("terminal sync counts must be supplied together")
            completed = int(completed_count or 0)
            failed = int(failed_count or 0)
            skipped = int(skipped_count or 0)
            if min(completed, failed, skipped) < 0 or (
                completed + failed + skipped != row.total_count
            ):
                raise ValueError("terminal sync counts must match snapshot total")
            row.completed_count = completed
            row.failed_count = failed
            row.skipped_count = skipped
        self._set_collection_state(
            row, collection_sync_state_for_job(status), now=now
        )

    def cancel_job(
        self,
        job: WorkerSyncJob,
        *,
        now: datetime,
        reason_code: str,
    ) -> None:
        row = self._required_job(job.job_id)
        row.status = "cancelled"
        row.retryable = False
        row.safe_reason_code = safe_reason_code(reason_code)
        row.lease_owner = None
        row.lease_expires_at = None
        row.next_retry_at = None
        row.completed_at = now
        row.updated_at = now
        self._set_collection_state(row, row.previous_sync_state, now=now)

    def recover_due(self, *, now: datetime, limit: int) -> list[uuid.UUID]:
        rows = (
            self.db.query(KnowledgeCollectionSyncJob)
            .filter(
                (
                    (KnowledgeCollectionSyncJob.status == "queued")
                    & (KnowledgeCollectionSyncJob.next_retry_at <= now)
                )
                | (
                    (KnowledgeCollectionSyncJob.status == "running")
                    & (KnowledgeCollectionSyncJob.lease_expires_at <= now)
                )
            )
            .order_by(
                KnowledgeCollectionSyncJob.next_retry_at.asc().nullsfirst(),
                KnowledgeCollectionSyncJob.requested_at.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        for row in rows:
            if row.status == "running":
                self.db.query(KnowledgeCollectionSyncJobItem).filter(
                    KnowledgeCollectionSyncJobItem.job_id == row.id,
                    KnowledgeCollectionSyncJobItem.organization_id == row.organization_id,
                    KnowledgeCollectionSyncJobItem.status == "running",
                ).update(
                    {
                        KnowledgeCollectionSyncJobItem.status: "pending",
                        KnowledgeCollectionSyncJobItem.started_at: None,
                        KnowledgeCollectionSyncJobItem.updated_at: now,
                    },
                    synchronize_session=False,
                )
                row.safe_reason_code = "sync.worker_interrupted"
            row.status = "queued"
            row.lease_owner = None
            row.lease_expires_at = None
            row.next_retry_at = now
            row.updated_at = now
            self._set_collection_state(row, "pending", now=now)
        return [row.id for row in rows]

    def delete_expired_terminal(self, *, before: datetime, limit: int) -> int:
        ids = [
            row[0]
            for row in (
                self.db.query(KnowledgeCollectionSyncJob.id)
                .filter(
                    KnowledgeCollectionSyncJob.status.in_(TERMINAL_JOB_STATUSES),
                    KnowledgeCollectionSyncJob.completed_at < before,
                )
                .order_by(KnowledgeCollectionSyncJob.completed_at.asc())
                .with_for_update(skip_locked=True)
                .limit(limit)
                .all()
            )
        ]
        if not ids:
            return 0
        return int(
            self.db.query(KnowledgeCollectionSyncJob)
            .filter(KnowledgeCollectionSyncJob.id.in_(ids))
            .delete(synchronize_session=False)
        )

    def _required_job(self, job_id: uuid.UUID) -> KnowledgeCollectionSyncJob:
        row = self.db.get(KnowledgeCollectionSyncJob, job_id)
        if row is None:
            raise RuntimeError("sync job disappeared while locked")
        return row

    def _required_item(self, item_id: uuid.UUID) -> KnowledgeCollectionSyncJobItem:
        row = self.db.get(KnowledgeCollectionSyncJobItem, item_id)
        if row is None:
            raise RuntimeError("sync item disappeared while locked")
        return row

    def _set_collection_state(
        self,
        job: KnowledgeCollectionSyncJob,
        state: str,
        *,
        now: datetime,
    ) -> None:
        collection = (
            self.db.query(KnowledgeCollection)
            .filter(
                KnowledgeCollection.id == job.collection_id,
                KnowledgeCollection.organization_id == job.organization_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if collection is not None:
            if collection.sync_state == "source_deleted" and state != "source_deleted":
                return
            collection.sync_state = state
            collection.updated_at = now

    @staticmethod
    def _job(row: KnowledgeCollectionSyncJob | None) -> WorkerSyncJob | None:
        if row is None:
            return None
        return WorkerSyncJob(
            job_id=row.id,
            organization_id=row.organization_id,
            collection_id=row.collection_id,
            requested_by=row.requested_by,
            target_snapshot_revision=row.target_snapshot_revision,
            total_count=row.total_count,
            status=row.status,
            previous_sync_state=row.previous_sync_state,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            lease_owner=row.lease_owner,
            lease_expires_at=row.lease_expires_at,
            next_retry_at=row.next_retry_at,
            execution_deadline_at=row.execution_deadline_at,
            started_at=row.started_at,
        )

    @staticmethod
    def _item(row: KnowledgeCollectionSyncJobItem | None) -> WorkerSyncItem | None:
        if row is None:
            return None
        return WorkerSyncItem(
            item_id=row.id,
            job_id=row.job_id,
            organization_id=row.organization_id,
            collection_id=row.collection_id,
            knowledge_base_id=row.knowledge_base_id,
            document_id=row.document_id,
            position=row.position,
            target_revision=row.target_revision,
            status=row.status,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
        )
