from __future__ import annotations

import logging
import threading
import uuid
from datetime import timedelta
from types import TracebackType
from typing import Callable

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from apps.gateway.adapters.db.knowledge_document_ingestion_repository import (
    SqlAlchemyDocumentIngestionRepository,
)
from apps.gateway.application.knowledge_document_ingestion.worker import (
    DocumentIngestionCancelled,
    DocumentIngestionLeaseLost,
    DocumentIngestionPermanentFailure,
    DocumentIngestionRetryableFailure,
    WorkerDocumentIngestionJob,
)
from apps.gateway.services.ingestion.service import (
    DurableIngestionDocumentMissing,
    DurableIngestionLeaseLost,
    DurableIngestionLockBusy,
    DurableIngestionNoContent,
    DurableIngestionSourceFailure,
    IngestionOrchestrator,
)
from apps.shared.db.models.knowledge import Document, KnowledgeBase
from apps.shared.domain.knowledge_document_ingestion import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_LEASE_SECONDS,
    RAW_PARSER_EGRESS_UNAVAILABLE_REASON,
)
from apps.shared.services.knowledge_ingestion_finalizer import (
    KnowledgeIngestionFinalizationError,
)
from apps.shared.services.egress_guard import EgressGuardError


logger = logging.getLogger(__name__)
_TRANSIENT_EGRESS_REASON_CODES = frozenset(
    {
        "egress.connection_failed",
        "egress.dns_resolution_failed",
        "egress.timeout",
    }
)


class KnowledgeDocumentIngestionJobRunner:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.session_factory = session_factory
        self.heartbeat_seconds = heartbeat_seconds
        self.lease_seconds = lease_seconds

    def run(self, job: WorkerDocumentIngestionJob) -> uuid.UUID | None:
        if (
            job.document_id is None
            or job.knowledge_base_id is None
            or job.requested_by_user_id is None
        ):
            raise DocumentIngestionCancelled("ingestion.document_missing")
        if not job.owner_token or not job.fencing_token:
            raise DocumentIngestionLeaseLost()

        with _LeaseHeartbeat(
            self.session_factory,
            job=job,
            heartbeat_seconds=self.heartbeat_seconds,
            lease_seconds=self.lease_seconds,
        ) as heartbeat:
            session = self.session_factory()
            try:
                document = session.get(Document, job.document_id)
                knowledge_base = session.get(KnowledgeBase, job.knowledge_base_id)
                if (
                    document is None
                    or knowledge_base is None
                    or document.knowledge_base_id != knowledge_base.id
                    or knowledge_base.organization_id != job.organization_id
                ):
                    raise DocumentIngestionCancelled("ingestion.document_missing")
                if (
                    knowledge_base.lifecycle_state != "active"
                    or knowledge_base.sync_state == "source_deleted"
                ):
                    raise DocumentIngestionCancelled("ingestion.lifecycle_blocked")

                repository = SqlAlchemyDocumentIngestionRepository(session)

                def lease_is_current() -> bool:
                    if heartbeat.lease_lost:
                        return False
                    lease_session: Session | None = None
                    current = False
                    try:
                        lease_session = self.session_factory()
                        current = SqlAlchemyDocumentIngestionRepository(
                            lease_session
                        ).is_owned_worker_job_current(
                            job.job_id,
                            owner_token=job.owner_token or "",
                            fencing_token=job.fencing_token or "",
                        )
                    except Exception as exc:
                        logger.warning(
                            "Knowledge ingestion lease check failed: error_type=%s",
                            type(exc).__name__,
                        )
                    finally:
                        if lease_session is not None:
                            try:
                                lease_session.close()
                            except Exception as exc:
                                logger.warning(
                                    "Knowledge ingestion lease session close failed: "
                                    "error_type=%s",
                                    type(exc).__name__,
                                )
                    if not current:
                        heartbeat.lease_lost = True
                    return current

                def finalize_job(
                    result_document_version_id: uuid.UUID | None,
                    completed_at,
                ) -> bool:
                    if heartbeat.lease_lost:
                        return False
                    return repository.mark_succeeded(
                        job.job_id,
                        owner_token=job.owner_token or "",
                        fencing_token=job.fencing_token or "",
                        result_document_version_id=result_document_version_id,
                        now=completed_at,
                    )

                result = IngestionOrchestrator(
                    session,
                    user_id=job.requested_by_user_id,
                    organization_id=job.organization_id,
                    chunk_size=document.chunk_size,
                    chunk_overlap=document.chunk_overlap,
                    ai_model=knowledge_base.embedding_model,
                ).process_document_for_job(
                    job.document_id,
                    session=session,
                    fencing_token=job.fencing_token,
                    finalize_job=finalize_job,
                    lease_is_current=lease_is_current,
                )
                return result
            except DurableIngestionDocumentMissing as exc:
                raise DocumentIngestionCancelled("ingestion.document_missing") from exc
            except DurableIngestionNoContent as exc:
                raise DocumentIngestionPermanentFailure("ingestion.no_content") from exc
            except DurableIngestionLockBusy as exc:
                raise DocumentIngestionRetryableFailure(
                    "ingestion.worker_interrupted"
                ) from exc
            except DurableIngestionLeaseLost as exc:
                raise DocumentIngestionLeaseLost() from exc
            except DurableIngestionSourceFailure as exc:
                if exc.reason_code == "source.temporarily_unavailable":
                    raise DocumentIngestionRetryableFailure(
                        "ingestion.source_temporarily_unavailable"
                    ) from exc
                if exc.reason_code == "resource.hidden":
                    raise DocumentIngestionPermanentFailure(
                        "ingestion.authorization_revoked"
                    ) from exc
                if exc.reason_code == "configuration.invalid":
                    raise DocumentIngestionPermanentFailure(
                        "ingestion.configuration_invalid"
                    ) from exc
                if exc.reason_code == RAW_PARSER_EGRESS_UNAVAILABLE_REASON:
                    raise DocumentIngestionPermanentFailure(
                        RAW_PARSER_EGRESS_UNAVAILABLE_REASON
                    ) from exc
                raise DocumentIngestionPermanentFailure(
                    "ingestion.processing_failed"
                ) from exc
            except KnowledgeIngestionFinalizationError as exc:
                if str(exc) in {"fencing_token_mismatch", "stale_ingestion_worker"}:
                    raise DocumentIngestionLeaseLost() from exc
                raise DocumentIngestionPermanentFailure(
                    "ingestion.processing_failed"
                ) from exc
            except SoftTimeLimitExceeded as exc:
                raise DocumentIngestionRetryableFailure("ingestion.timeout") from exc
            except (OperationalError, TimeoutError, ConnectionError) as exc:
                raise DocumentIngestionRetryableFailure(
                    "ingestion.source_temporarily_unavailable"
                ) from exc
            except EgressGuardError as exc:
                if exc.reason_code in _TRANSIENT_EGRESS_REASON_CODES:
                    raise DocumentIngestionRetryableFailure(
                        "ingestion.source_temporarily_unavailable"
                    ) from exc
                raise DocumentIngestionPermanentFailure(
                    "ingestion.processing_failed"
                ) from exc
            except (
                DocumentIngestionCancelled,
                DocumentIngestionLeaseLost,
                DocumentIngestionPermanentFailure,
                DocumentIngestionRetryableFailure,
            ):
                raise
            except Exception as exc:
                logger.error(
                    "Knowledge document ingestion failed: error_type=%s",
                    type(exc).__name__,
                )
                raise DocumentIngestionPermanentFailure(
                    "ingestion.internal_error"
                ) from exc
            finally:
                session.close()


class _LeaseHeartbeat:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        job: WorkerDocumentIngestionJob,
        heartbeat_seconds: int,
        lease_seconds: int,
    ) -> None:
        self.session_factory = session_factory
        self.job = job
        self.heartbeat_seconds = heartbeat_seconds
        self.lease_seconds = lease_seconds
        self.lease_lost = False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="knowledge-ingestion-heartbeat",
            daemon=True,
        )

    def __enter__(self) -> _LeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1, self.heartbeat_seconds))

    def _run(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            session = self.session_factory()
            try:
                repository = SqlAlchemyDocumentIngestionRepository(session)
                now = repository.database_now()
                renewed = repository.heartbeat(
                    self.job.job_id,
                    owner_token=self.job.owner_token or "",
                    fencing_token=self.job.fencing_token or "",
                    now=now,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                )
                if not renewed:
                    session.rollback()
                    self.lease_lost = True
                    return
                session.commit()
            except Exception as exc:
                session.rollback()
                logger.warning(
                    "Knowledge ingestion heartbeat failed: error_type=%s",
                    type(exc).__name__,
                )
            finally:
                session.close()
