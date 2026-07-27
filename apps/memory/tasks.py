"""Celery execution adapters for Memory-owned maintenance work."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apps.memory.adapters.persistence.readiness import (
    REQUIRED_MEMORY_SCHEMA,
    check_memory_schema_readiness,
)
from apps.memory.adapters.persistence.repository import (
    SqlAlchemyConversationMemoryRepository,
    SqlAlchemyMemoryUnitOfWork,
)
from apps.memory.adapters.queue.conversation_turn_publisher import (
    CeleryConversationTurnPublisher,
)
from apps.memory.application.dispatch import (
    FinalizeTerminalTurnDispatchCommand,
    FinalizeTerminalTurnDispatchUseCase,
    ListDueTurnDispatchJobsUseCase,
    RecoverExpiredTurnDispatchCommand,
    RecoverExpiredTurnDispatchUseCase,
)
from apps.memory.domain.conversation import DispatchStatus, MemoryTurnDispatchJob
from apps.memory.domain.errors import DispatchStateConflictError
from apps.memory.application.retention import PurgeExpiredPublicSecretReplaysUseCase
from apps.shared.celery_app import celery_app
from apps.shared.db.session import SessionLocal

logger = logging.getLogger(__name__)
_RETENTION_BATCH_LIMIT = 500
_RETENTION_MAX_BATCHES_PER_RUN = 20
_REPLAY_SCHEMA = {
    "conversation_idempotency_records": REQUIRED_MEMORY_SCHEMA[
        "conversation_idempotency_records"
    ],
    "conversation_secret_replays": REQUIRED_MEMORY_SCHEMA[
        "conversation_secret_replays"
    ],
}
_DISPATCH_RECONCILIATION_BATCH_LIMIT = 100
_DISPATCH_RETRY_BASE_SECONDS = 5
_DISPATCH_SCHEMA = {
    "memory_turn_dispatch_jobs": REQUIRED_MEMORY_SCHEMA["memory_turn_dispatch_jobs"]
}


def _build_retention_use_case(session):
    return PurgeExpiredPublicSecretReplaysUseCase(
        repository=SqlAlchemyConversationMemoryRepository(session),
        uow=SqlAlchemyMemoryUnitOfWork(session),
    )


def _drain_expired_public_replays(
    use_case,
    *,
    now: datetime,
    batch_limit: int,
    max_batches: int,
) -> dict[str, int | bool]:
    if not 1 <= max_batches <= 100:
        raise ValueError("retention drain batch budget is invalid")
    deleted_count = 0
    for batch_count in range(1, max_batches + 1):
        batch = use_case.execute(now=now, limit=batch_limit)
        deleted_count += batch.deleted_count
        has_more = batch.has_more(limit=batch_limit)
        if not has_more:
            return {
                "deleted_count": deleted_count,
                "batch_count": batch_count,
                "has_more": False,
            }
    return {
        "deleted_count": deleted_count,
        "batch_count": max_batches,
        "has_more": True,
    }


def _dispatch_retry_at(
    job: MemoryTurnDispatchJob,
    *,
    now: datetime,
) -> datetime | None:
    if job.attempt_count >= job.max_attempts:
        return None
    exponent = max(0, min(6, job.attempt_count - 1))
    return now + timedelta(
        seconds=min(300, _DISPATCH_RETRY_BASE_SECONDS * (2**exponent))
    )


def _reconcile_due_dispatch_jobs(
    jobs: tuple[MemoryTurnDispatchJob, ...],
    *,
    publisher,
    session_factory,
    now: datetime,
) -> dict[str, int]:
    counts = {
        "published": 0,
        "recovered": 0,
        "terminal": 0,
        "conflict": 0,
        "failed": 0,
    }
    for job in jobs:
        try:
            if job.status is DispatchStatus.TERMINAL:
                session = session_factory()
                try:
                    repository = SqlAlchemyConversationMemoryRepository(session)
                    FinalizeTerminalTurnDispatchUseCase(
                        repository=repository,
                        uow=SqlAlchemyMemoryUnitOfWork(session),
                    ).execute(
                        FinalizeTerminalTurnDispatchCommand(
                            organization_id=job.organization_id,
                            session_id=job.session_id,
                            turn_id=job.turn_id,
                            dispatch_id=job.id,
                            now=now,
                        )
                    )
                    counts["terminal"] += 1
                finally:
                    session.close()
                continue
            if job.status is DispatchStatus.CLAIMED:
                session = session_factory()
                try:
                    repository = SqlAlchemyConversationMemoryRepository(session)
                    uow = SqlAlchemyMemoryUnitOfWork(session)
                    recovered = RecoverExpiredTurnDispatchUseCase(
                        repository=repository,
                        uow=uow,
                    ).execute(
                        RecoverExpiredTurnDispatchCommand(
                            organization_id=job.organization_id,
                            dispatch_id=job.id,
                            now=now,
                            retry_at=_dispatch_retry_at(job, now=now),
                            safe_reason_code="memory.dispatch_claim_expired",
                        )
                    )
                    if recovered.status is DispatchStatus.TERMINAL:
                        FinalizeTerminalTurnDispatchUseCase(
                            repository=repository,
                            uow=uow,
                        ).execute(
                            FinalizeTerminalTurnDispatchCommand(
                                organization_id=job.organization_id,
                                session_id=job.session_id,
                                turn_id=job.turn_id,
                                dispatch_id=job.id,
                                now=now,
                            )
                        )
                        counts["terminal"] += 1
                    else:
                        counts["recovered"] += 1
                finally:
                    session.close()
                continue
            publisher.publish(
                organization_id=job.organization_id,
                session_id=job.session_id,
                dispatch_id=job.id,
                turn_id=job.turn_id,
                memory_contract_version=job.memory_contract_version,
                storage_generation=job.storage_generation,
                minimum_worker_capability=job.minimum_worker_capability,
            )
            counts["published"] += 1
        except DispatchStateConflictError:
            counts["conflict"] += 1
        except Exception as error:
            counts["failed"] += 1
            logger.warning(
                "Conversation dispatch reconciliation item failed: error_type=%s",
                type(error).__name__,
            )
    return counts


def _list_due_dispatch_jobs(*, now: datetime) -> tuple[MemoryTurnDispatchJob, ...]:
    session = SessionLocal()
    try:
        readiness = check_memory_schema_readiness(
            session,
            required_schema=_DISPATCH_SCHEMA,
        )
        if not readiness.ready:
            return ()
        return ListDueTurnDispatchJobsUseCase(
            repository=SqlAlchemyConversationMemoryRepository(session),
            uow=SqlAlchemyMemoryUnitOfWork(session),
        ).execute(now=now, limit=_DISPATCH_RECONCILIATION_BATCH_LIMIT)
    finally:
        session.close()


@celery_app.task(
    name="memory.secret_replay_retention_purge",
    bind=True,
    max_retries=3,
    ignore_result=True,
)
def purge_expired_public_secret_replays(self):
    session = SessionLocal()
    try:
        readiness = check_memory_schema_readiness(
            session,
            required_schema=_REPLAY_SCHEMA,
        )
        if not readiness.ready:
            return {
                "status": "not_ready",
                "deleted_count": 0,
                "batch_count": 0,
                "has_more": False,
            }
        drain_result = _drain_expired_public_replays(
            _build_retention_use_case(session),
            now=datetime.now(timezone.utc),
            batch_limit=_RETENTION_BATCH_LIMIT,
            max_batches=_RETENTION_MAX_BATCHES_PER_RUN,
        )
        return {"status": "success", **drain_result}
    except Exception as error:
        session.rollback()
        logger.error(
            "Public secret replay retention failed: error_type=%s",
            type(error).__name__,
        )
        raise self.retry(exc=error, countdown=2**self.request.retries)
    finally:
        session.close()


@celery_app.task(
    name="memory.turn_dispatch.reconcile",
    bind=True,
    max_retries=3,
    ignore_result=True,
)
def reconcile_conversation_turn_dispatches(self):
    try:
        now = datetime.now(timezone.utc)
        jobs = _list_due_dispatch_jobs(now=now)
        counts = _reconcile_due_dispatch_jobs(
            jobs,
            publisher=CeleryConversationTurnPublisher(
                celery_app=celery_app,
                session_factory=SessionLocal,
            ),
            session_factory=SessionLocal,
            now=now,
        )
        return {
            "status": "partial" if counts["failed"] else "success",
            "scanned": len(jobs),
            **counts,
        }
    except Exception as error:
        logger.error(
            "Conversation dispatch reconciliation failed: error_type=%s",
            type(error).__name__,
        )
        raise self.retry(exc=error, countdown=2**self.request.retries)


__all__ = [
    "purge_expired_public_secret_replays",
    "reconcile_conversation_turn_dispatches",
]
