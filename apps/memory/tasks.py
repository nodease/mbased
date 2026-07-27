"""Celery execution adapters for Memory-owned retention work."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apps.memory.adapters.persistence.readiness import (
    REQUIRED_MEMORY_SCHEMA,
    check_memory_schema_readiness,
)
from apps.memory.adapters.persistence.repository import (
    SqlAlchemyConversationMemoryRepository,
    SqlAlchemyMemoryUnitOfWork,
)
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


__all__ = ["purge_expired_public_secret_replays"]
