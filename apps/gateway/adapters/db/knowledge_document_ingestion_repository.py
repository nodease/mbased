from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from apps.gateway.application.knowledge_document_ingestion.worker import (
    RecoveredDocumentIngestionJob,
    WorkerDocumentIngestionJob,
)
from apps.gateway.application.knowledge_document_ingestion.use_cases import (
    AdmissionDocumentSnapshot,
    DocumentIngestionJobSnapshot,
    DocumentIngestionSettings,
    RequestDocumentIngestionCommand,
)
from apps.gateway.services.knowledge_authorization_service import (
    KnowledgeAuthorizationService,
    KnowledgePermissionDenied,
    KnowledgeResourceHidden,
)
from apps.shared.db.models.knowledge import (
    Document,
    KnowledgeBase,
    KnowledgeDocumentIngestionJob,
)
from apps.shared.domain.knowledge_document_ingestion import (
    DEFAULT_DISPATCH_LEASE_SECONDS,
    safe_reason_code,
)
from apps.shared.services.knowledge_ingestion_fencing import (
    ACTIVE_FENCING_TOKEN_HASH_KEY,
)
from apps.shared.services.permissions import has_active_organization_membership


class SqlAlchemyDocumentIngestionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._locked_document: Document | None = None
        self._locked_documents: dict[uuid.UUID, Document] = {}
        self._locked_knowledge_base: KnowledgeBase | None = None
        self._locked_worker_job: KnowledgeDocumentIngestionJob | None = None

    def database_now(self) -> datetime:
        value = self.db.query(func.clock_timestamp()).scalar()
        return value or datetime.now(timezone.utc)

    def lock_document_scope(
        self,
        organization_id: uuid.UUID,
        knowledge_base_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> AdmissionDocumentSnapshot | None:
        knowledge_base = (
            self.db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.organization_id == organization_id,
            )
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if knowledge_base is None:
            return None
        document = (
            self.db.query(Document)
            .filter(
                Document.id == document_id,
                Document.knowledge_base_id == knowledge_base_id,
            )
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if document is None:
            return None
        self._locked_document = document
        self._locked_documents = {document.id: document}
        self._locked_knowledge_base = knowledge_base
        return self._target_snapshot(document, knowledge_base)

    def lock_knowledge_base_documents(
        self,
        organization_id: uuid.UUID,
        knowledge_base_id: uuid.UUID,
    ) -> tuple[AdmissionDocumentSnapshot, ...] | None:
        knowledge_base = (
            self.db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.organization_id == organization_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if knowledge_base is None:
            return None
        documents = (
            self.db.query(Document)
            .filter(Document.knowledge_base_id == knowledge_base_id)
            .order_by(Document.id.asc())
            .with_for_update()
            .all()
        )
        self._locked_knowledge_base = knowledge_base
        self._locked_documents = {document.id: document for document in documents}
        self._locked_document = documents[0] if len(documents) == 1 else None
        return tuple(
            self._target_snapshot(document, knowledge_base) for document in documents
        )

    def find_active_job(
        self, document_id: uuid.UUID
    ) -> DocumentIngestionJobSnapshot | None:
        row = (
            self.db.query(KnowledgeDocumentIngestionJob)
            .filter(
                KnowledgeDocumentIngestionJob.document_id == document_id,
                KnowledgeDocumentIngestionJob.status.in_(
                    ["pending", "running", "retry_scheduled"]
                ),
            )
            .with_for_update()
            .one_or_none()
        )
        return self._job_snapshot(row)

    def next_generation(self, document_id: uuid.UUID) -> int:
        current = (
            self.db.query(func.max(KnowledgeDocumentIngestionJob.generation))
            .filter(KnowledgeDocumentIngestionJob.document_id == document_id)
            .scalar()
        )
        return int(current or 0) + 1

    def latest_job(
        self, document_id: uuid.UUID, *, lock: bool = False
    ) -> DocumentIngestionJobSnapshot | None:
        query = (
            self.db.query(KnowledgeDocumentIngestionJob)
            .filter(KnowledgeDocumentIngestionJob.document_id == document_id)
            .order_by(
                KnowledgeDocumentIngestionJob.requested_at.desc(),
                KnowledgeDocumentIngestionJob.id.desc(),
            )
        )
        if lock:
            query = query.with_for_update()
        return self._job_snapshot(query.first())

    def apply_settings_and_mark_queued(
        self,
        target: AdmissionDocumentSnapshot,
        settings: DocumentIngestionSettings,
        *,
        now: datetime,
    ) -> None:
        document = self._locked_documents.get(target.document_id)
        knowledge_base = self._locked_knowledge_base
        if (
            document is None
            or knowledge_base is None
            or document.id != target.document_id
            or knowledge_base.id != target.knowledge_base_id
        ):
            raise RuntimeError("document scope must be locked before admission")

        if settings.chunk_size is not None:
            document.chunk_size = settings.chunk_size
        if settings.chunk_overlap is not None:
            document.chunk_overlap = settings.chunk_overlap
        if settings.embedding_model is not None:
            knowledge_base.embedding_model = settings.embedding_model
            knowledge_base.updated_at = now

        meta_info = dict(document.meta_info or {})
        meta_info.update(dict(settings.meta_updates))
        for key in settings.meta_remove_keys:
            meta_info.pop(key, None)
        meta_info.pop(ACTIVE_FENCING_TOKEN_HASH_KEY, None)
        meta_info.pop("processing_started_at", None)
        meta_info.pop("processing_recovered_from_timeout", None)
        meta_info["progress"] = 0
        meta_info["processing_enqueued_at"] = now.isoformat()
        meta_info["processing_current_step"] = "Processing queued."
        document.meta_info = meta_info
        document.status = "indexing"
        document.error_message = None
        document.updated_at = now

    def update_locked_knowledge_base_model(
        self,
        knowledge_base_id: uuid.UUID,
        embedding_model: str,
        *,
        now: datetime,
    ) -> None:
        knowledge_base = self._locked_knowledge_base
        if knowledge_base is None or knowledge_base.id != knowledge_base_id:
            raise RuntimeError("knowledge base must be locked before reindex admission")
        knowledge_base.embedding_model = embedding_model
        knowledge_base.updated_at = now

    def create_job(
        self,
        *,
        command: RequestDocumentIngestionCommand,
        generation: int,
        input_revision: str,
        idempotency_key: str,
        now: datetime,
        max_attempts: int,
    ) -> DocumentIngestionJobSnapshot:
        job = KnowledgeDocumentIngestionJob(
            id=uuid.uuid4(),
            organization_id=command.organization_id,
            knowledge_base_id=command.knowledge_base_id,
            document_id=command.document_id,
            requested_by_user_id=command.actor_id,
            operation_kind=command.operation,
            generation=generation,
            input_revision=input_revision,
            idempotency_key=idempotency_key,
            status="pending",
            attempt_count=0,
            max_attempts=max_attempts,
            retryable=True,
            dispatch_lease_expires_at=now
            + timedelta(seconds=DEFAULT_DISPATCH_LEASE_SECONDS),
            next_retry_at=now,
            requested_at=now,
            updated_at=now,
            safe_metadata={},
        )
        self.db.add(job)
        self.db.flush()
        return self._job_snapshot(job)  # type: ignore[return-value]

    def lock_worker_job(
        self, job_id: uuid.UUID
    ) -> WorkerDocumentIngestionJob | None:
        self._locked_worker_job = None
        candidate = (
            self.db.query(
                KnowledgeDocumentIngestionJob.knowledge_base_id,
                KnowledgeDocumentIngestionJob.document_id,
            )
            .filter(KnowledgeDocumentIngestionJob.id == job_id)
            .one_or_none()
        )
        if candidate is None or not self._lock_worker_scope_rows(
            knowledge_base_id=candidate.knowledge_base_id,
            document_id=candidate.document_id,
            skip_locked=False,
        ):
            return None
        row = (
            self.db.query(KnowledgeDocumentIngestionJob)
            .filter(KnowledgeDocumentIngestionJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        self._locked_worker_job = row
        return self._worker_job(row)

    def mark_running(
        self,
        job: WorkerDocumentIngestionJob,
        *,
        owner_token: str,
        fencing_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> WorkerDocumentIngestionJob:
        row = self._locked_worker_job
        if row is None or row.id != job.job_id:
            raise RuntimeError("ingestion job must be locked before claim")
        row.status = "running"
        row.attempt_count += 1
        row.owner_token = owner_token
        row.fencing_token = fencing_token
        row.lease_expires_at = lease_expires_at
        row.heartbeat_at = now
        row.dispatch_lease_expires_at = None
        row.next_retry_at = None
        row.safe_reason_code = None
        row.started_at = row.started_at or now
        row.updated_at = now
        self.db.flush()
        return self._worker_job(row)  # type: ignore[return-value]

    def lock_owned_worker_job(
        self,
        job_id: uuid.UUID,
        *,
        owner_token: str,
        fencing_token: str,
    ) -> WorkerDocumentIngestionJob | None:
        self._locked_worker_job = None
        candidate = (
            self.db.query(
                KnowledgeDocumentIngestionJob.knowledge_base_id,
                KnowledgeDocumentIngestionJob.document_id,
            )
            .filter(
                KnowledgeDocumentIngestionJob.id == job_id,
                KnowledgeDocumentIngestionJob.status == "running",
                KnowledgeDocumentIngestionJob.owner_token == owner_token,
                KnowledgeDocumentIngestionJob.fencing_token == fencing_token,
                KnowledgeDocumentIngestionJob.lease_expires_at
                > func.clock_timestamp(),
            )
            .one_or_none()
        )
        if candidate is None or not self._lock_worker_scope_rows(
            knowledge_base_id=candidate.knowledge_base_id,
            document_id=candidate.document_id,
            skip_locked=False,
        ):
            return None
        row = (
            self.db.query(KnowledgeDocumentIngestionJob)
            .filter(
                KnowledgeDocumentIngestionJob.id == job_id,
                KnowledgeDocumentIngestionJob.status == "running",
                KnowledgeDocumentIngestionJob.owner_token == owner_token,
                KnowledgeDocumentIngestionJob.fencing_token == fencing_token,
                KnowledgeDocumentIngestionJob.lease_expires_at
                > func.clock_timestamp(),
            )
            .with_for_update()
            .one_or_none()
        )
        self._locked_worker_job = row
        return self._worker_job(row)

    def heartbeat(
        self,
        job_id: uuid.UUID,
        *,
        owner_token: str,
        fencing_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        updated = (
            self.db.query(KnowledgeDocumentIngestionJob)
            .filter(
                KnowledgeDocumentIngestionJob.id == job_id,
                KnowledgeDocumentIngestionJob.status == "running",
                KnowledgeDocumentIngestionJob.owner_token == owner_token,
                KnowledgeDocumentIngestionJob.fencing_token == fencing_token,
                KnowledgeDocumentIngestionJob.lease_expires_at
                > func.clock_timestamp(),
            )
            .update(
                {
                    KnowledgeDocumentIngestionJob.heartbeat_at: now,
                    KnowledgeDocumentIngestionJob.lease_expires_at: lease_expires_at,
                    KnowledgeDocumentIngestionJob.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        return int(updated or 0) == 1

    def mark_succeeded(
        self,
        job_id: uuid.UUID,
        *,
        owner_token: str,
        fencing_token: str,
        result_document_version_id: uuid.UUID | None,
        now: datetime,
    ) -> bool:
        updated = (
            self.db.query(KnowledgeDocumentIngestionJob)
            .filter(
                KnowledgeDocumentIngestionJob.id == job_id,
                KnowledgeDocumentIngestionJob.status == "running",
                KnowledgeDocumentIngestionJob.owner_token == owner_token,
                KnowledgeDocumentIngestionJob.fencing_token == fencing_token,
                KnowledgeDocumentIngestionJob.lease_expires_at
                > func.clock_timestamp(),
            )
            .update(
                {
                    KnowledgeDocumentIngestionJob.status: "succeeded",
                    KnowledgeDocumentIngestionJob.result_document_version_id: (
                        result_document_version_id
                    ),
                    KnowledgeDocumentIngestionJob.retryable: False,
                    KnowledgeDocumentIngestionJob.safe_reason_code: None,
                    KnowledgeDocumentIngestionJob.owner_token: None,
                    KnowledgeDocumentIngestionJob.fencing_token: None,
                    KnowledgeDocumentIngestionJob.lease_expires_at: None,
                    KnowledgeDocumentIngestionJob.dispatch_lease_expires_at: None,
                    KnowledgeDocumentIngestionJob.next_retry_at: None,
                    KnowledgeDocumentIngestionJob.completed_at: now,
                    KnowledgeDocumentIngestionJob.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        return int(updated or 0) == 1

    def is_owned_worker_job_current(
        self,
        job_id: uuid.UUID,
        *,
        owner_token: str,
        fencing_token: str,
    ) -> bool:
        current = (
            self.db.query(KnowledgeDocumentIngestionJob.id)
            .filter(
                KnowledgeDocumentIngestionJob.id == job_id,
                KnowledgeDocumentIngestionJob.status == "running",
                KnowledgeDocumentIngestionJob.owner_token == owner_token,
                KnowledgeDocumentIngestionJob.fencing_token == fencing_token,
                KnowledgeDocumentIngestionJob.lease_expires_at
                > func.clock_timestamp(),
            )
            .scalar()
        )
        return current is not None

    def mark_retry_scheduled(
        self,
        job: WorkerDocumentIngestionJob,
        *,
        now: datetime,
        next_retry_at: datetime,
        reason_code: str,
    ) -> None:
        row = self._require_locked_worker_job(job)
        row.status = "retry_scheduled"
        row.retryable = True
        row.safe_reason_code = safe_reason_code(reason_code)
        row.owner_token = None
        row.fencing_token = None
        row.lease_expires_at = None
        row.dispatch_lease_expires_at = None
        row.next_retry_at = next_retry_at
        row.updated_at = now
        self._project_document_state(
            row,
            status="indexing",
            error_message=None,
            step="Processing retry scheduled.",
            now=now,
        )

    def mark_dead_lettered(
        self,
        job: WorkerDocumentIngestionJob,
        *,
        now: datetime,
        reason_code: str,
        retryable: bool,
    ) -> None:
        row = self._require_locked_worker_job(job)
        row.status = "dead_lettered"
        row.retryable = retryable
        row.safe_reason_code = safe_reason_code(reason_code)
        row.owner_token = None
        row.fencing_token = None
        row.lease_expires_at = None
        row.dispatch_lease_expires_at = None
        row.next_retry_at = None
        row.dead_lettered_at = now
        row.completed_at = now
        row.updated_at = now
        self._project_document_state(
            row,
            status="failed",
            error_message="Document processing failed.",
            step="Processing failed.",
            now=now,
        )

    def mark_cancelled(
        self,
        job: WorkerDocumentIngestionJob,
        *,
        now: datetime,
        reason_code: str,
    ) -> None:
        row = self._require_locked_worker_job(job)
        row.status = "cancelled"
        row.retryable = False
        row.safe_reason_code = safe_reason_code(reason_code)
        row.owner_token = None
        row.fencing_token = None
        row.lease_expires_at = None
        row.dispatch_lease_expires_at = None
        row.next_retry_at = None
        row.completed_at = now
        row.updated_at = now
        self._project_document_state(
            row,
            status="failed",
            error_message="Document processing was cancelled.",
            step="Processing cancelled.",
            now=now,
        )

    def recover_due(
        self, *, now: datetime, limit: int
    ) -> list[RecoveredDocumentIngestionJob]:
        if limit <= 0:
            return []
        candidates = (
            self.db.query(
                KnowledgeDocumentIngestionJob.id,
                KnowledgeDocumentIngestionJob.knowledge_base_id,
                KnowledgeDocumentIngestionJob.document_id,
            )
            .filter(self._recovery_due_filter(now))
            .order_by(
                KnowledgeDocumentIngestionJob.requested_at.asc(),
                KnowledgeDocumentIngestionJob.id.asc(),
            )
            .limit(limit * 4)
            .all()
        )
        recoveries: list[RecoveredDocumentIngestionJob] = []
        dispatch_lease_expires_at = now + timedelta(
            seconds=DEFAULT_DISPATCH_LEASE_SECONDS
        )
        for candidate in candidates:
            if len(recoveries) >= limit:
                break

            if not self._lock_worker_scope_rows(
                knowledge_base_id=candidate.knowledge_base_id,
                document_id=candidate.document_id,
                skip_locked=True,
            ):
                continue
            row = (
                self.db.query(KnowledgeDocumentIngestionJob)
                .filter(KnowledgeDocumentIngestionJob.id == candidate.id)
                .with_for_update(skip_locked=True)
                .one_or_none()
            )
            if row is None or not self._is_recovery_due(row, now=now):
                continue

            should_publish = True
            if row.status == "running":
                if row.attempt_count >= row.max_attempts:
                    row.status = "dead_lettered"
                    row.retryable = True
                    row.safe_reason_code = "ingestion.worker_interrupted"
                    row.owner_token = None
                    row.fencing_token = None
                    row.lease_expires_at = None
                    row.dispatch_lease_expires_at = None
                    row.next_retry_at = None
                    row.dead_lettered_at = now
                    row.completed_at = now
                    row.updated_at = now
                    self._project_document_state(
                        row,
                        status="failed",
                        error_message="Document processing failed.",
                        step="Processing failed.",
                        now=now,
                    )
                    should_publish = False
                else:
                    row.status = "retry_scheduled"
                    row.retryable = True
                    row.safe_reason_code = "ingestion.worker_interrupted"
                    row.owner_token = None
                    row.fencing_token = None
                    row.lease_expires_at = None
                    row.dispatch_lease_expires_at = dispatch_lease_expires_at
                    row.next_retry_at = now
                    row.updated_at = now
                    self._project_document_state(
                        row,
                        status="indexing",
                        error_message=None,
                        step="Processing retry scheduled.",
                        now=now,
                    )
            else:
                row.dispatch_lease_expires_at = dispatch_lease_expires_at
                row.updated_at = now

            recoveries.append(
                RecoveredDocumentIngestionJob(
                    job_id=row.id,
                    document_id=row.document_id,
                    should_publish=should_publish,
                )
            )
        return recoveries

    def delete_expired_terminal(self, *, before: datetime, limit: int) -> int:
        ids = [
            row[0]
            for row in (
                self.db.query(KnowledgeDocumentIngestionJob.id)
                .filter(
                    KnowledgeDocumentIngestionJob.status.in_(
                        ["succeeded", "dead_lettered", "cancelled"]
                    ),
                    KnowledgeDocumentIngestionJob.completed_at < before,
                )
                .order_by(KnowledgeDocumentIngestionJob.completed_at.asc())
                .with_for_update(skip_locked=True)
                .limit(limit)
                .all()
            )
        ]
        if not ids:
            return 0
        deleted = (
            self.db.query(KnowledgeDocumentIngestionJob)
            .filter(KnowledgeDocumentIngestionJob.id.in_(ids))
            .delete(synchronize_session=False)
        )
        return int(deleted or 0)

    def _require_locked_worker_job(
        self, job: WorkerDocumentIngestionJob
    ) -> KnowledgeDocumentIngestionJob:
        row = self._locked_worker_job
        if row is None or row.id != job.job_id:
            raise RuntimeError("ingestion job must be locked before transition")
        return row

    def _project_document_state(
        self,
        job: KnowledgeDocumentIngestionJob,
        *,
        status: str,
        error_message: str | None,
        step: str,
        now: datetime,
    ) -> None:
        if job.document_id is None:
            return
        document = self._locked_documents.get(job.document_id)
        if document is None:
            document = self.db.get(Document, job.document_id)
        if document is None:
            return
        meta_info = dict(document.meta_info or {})
        meta_info.pop(ACTIVE_FENCING_TOKEN_HASH_KEY, None)
        meta_info["progress"] = 0
        meta_info["processing_current_step"] = step
        if status == "indexing":
            meta_info["processing_enqueued_at"] = now.isoformat()
        document.meta_info = meta_info
        document.status = status
        document.error_message = error_message
        document.updated_at = now

    def _lock_worker_scope_rows(
        self,
        *,
        knowledge_base_id: uuid.UUID | None,
        document_id: uuid.UUID | None,
        skip_locked: bool,
    ) -> bool:
        if knowledge_base_id is not None:
            knowledge_base = (
                self.db.query(KnowledgeBase)
                .filter(KnowledgeBase.id == knowledge_base_id)
                .with_for_update(skip_locked=skip_locked)
                .one_or_none()
            )
            if knowledge_base is None:
                return False
            self._locked_knowledge_base = knowledge_base

        if document_id is not None:
            document_query = self.db.query(Document).filter(Document.id == document_id)
            if knowledge_base_id is not None:
                document_query = document_query.filter(
                    Document.knowledge_base_id == knowledge_base_id
                )
            document = (
                document_query.with_for_update(skip_locked=skip_locked).one_or_none()
            )
            if document is None:
                return False
            self._locked_document = document
            self._locked_documents[document.id] = document
        return True

    @staticmethod
    def _recovery_due_filter(now: datetime):
        return or_(
            and_(
                KnowledgeDocumentIngestionJob.status.in_(
                    ["pending", "retry_scheduled"]
                ),
                or_(
                    KnowledgeDocumentIngestionJob.next_retry_at.is_(None),
                    KnowledgeDocumentIngestionJob.next_retry_at <= now,
                ),
                or_(
                    KnowledgeDocumentIngestionJob.dispatch_lease_expires_at.is_(None),
                    KnowledgeDocumentIngestionJob.dispatch_lease_expires_at <= now,
                ),
            ),
            and_(
                KnowledgeDocumentIngestionJob.status == "running",
                KnowledgeDocumentIngestionJob.lease_expires_at <= now,
            ),
        )

    @staticmethod
    def _is_recovery_due(
        row: KnowledgeDocumentIngestionJob,
        *,
        now: datetime,
    ) -> bool:
        if row.status == "running":
            return row.lease_expires_at is not None and row.lease_expires_at <= now
        if row.status not in {"pending", "retry_scheduled"}:
            return False
        retry_due = row.next_retry_at is None or row.next_retry_at <= now
        dispatch_due = (
            row.dispatch_lease_expires_at is None
            or row.dispatch_lease_expires_at <= now
        )
        return retry_due and dispatch_due

    @staticmethod
    def _target_snapshot(
        document: Document,
        knowledge_base: KnowledgeBase,
    ) -> AdmissionDocumentSnapshot:
        return AdmissionDocumentSnapshot(
            document_id=document.id,
            knowledge_base_id=knowledge_base.id,
            organization_id=knowledge_base.organization_id,
            source_type=str(
                getattr(document.source_type, "value", document.source_type)
            ),
            document_status=document.status,
            lifecycle_state=knowledge_base.lifecycle_state,
            sync_state=knowledge_base.sync_state,
            chunk_size=document.chunk_size,
            chunk_overlap=document.chunk_overlap,
            meta_info=dict(document.meta_info or {}),
            content_hash=document.content_hash,
            active_document_version_id=knowledge_base.active_document_version_id,
            embedding_model=knowledge_base.embedding_model,
            document_updated_at=document.updated_at,
        )

    @staticmethod
    def _job_snapshot(
        row: KnowledgeDocumentIngestionJob | None,
    ) -> DocumentIngestionJobSnapshot | None:
        if row is None:
            return None
        return DocumentIngestionJobSnapshot(
            job_id=row.id,
            organization_id=row.organization_id,
            knowledge_base_id=row.knowledge_base_id,
            document_id=row.document_id,
            operation=row.operation_kind,
            generation=row.generation,
            input_revision=row.input_revision,
            status=row.status,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            retryable=row.retryable,
            safe_reason_code=row.safe_reason_code,
            requested_at=row.requested_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            result_document_version_id=row.result_document_version_id,
        )

    @staticmethod
    def _worker_job(
        row: KnowledgeDocumentIngestionJob | None,
    ) -> WorkerDocumentIngestionJob | None:
        if row is None:
            return None
        return WorkerDocumentIngestionJob(
            job_id=row.id,
            organization_id=row.organization_id,
            knowledge_base_id=row.knowledge_base_id,
            document_id=row.document_id,
            requested_by_user_id=row.requested_by_user_id,
            operation=row.operation_kind,
            generation=row.generation,
            status=row.status,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            retryable=row.retryable,
            owner_token=row.owner_token,
            fencing_token=row.fencing_token,
            lease_expires_at=row.lease_expires_at,
            next_retry_at=row.next_retry_at,
        )


class SqlAlchemyWorkerDocumentIngestionAuthorization:
    def __init__(self, db: Session) -> None:
        self.db = db

    def is_allowed(self, job: WorkerDocumentIngestionJob) -> bool:
        actor_id = job.requested_by_user_id
        knowledge_base_id = job.knowledge_base_id
        document_id = job.document_id
        if actor_id is None or knowledge_base_id is None or document_id is None:
            return False
        if not has_active_organization_membership(
            self.db, actor_id, job.organization_id
        ):
            return False
        try:
            kb, _ = KnowledgeAuthorizationService(
                self.db,
                user_id=actor_id,
                organization_id=job.organization_id,
            ).load_document(
                knowledge_base_id,
                document_id,
                "write",
                domain_action=("sync_manage" if job.operation == "sync" else None),
            )
        except (KnowledgeResourceHidden, KnowledgePermissionDenied):
            return False
        return kb.lifecycle_state == "active" and kb.sync_state != "source_deleted"
