"""Bounded recovery tasks for the durable provider usage ledger."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from apps.shared.celery_app import celery_app
from apps.shared.db.session import SessionLocal
from apps.shared.services.provider_usage_ledger import ProviderUsageLedgerService

logger = logging.getLogger(__name__)
_TASK_NAME = "provider_usage.reconcile"
_STALE_PROVIDER_STARTED_AFTER = timedelta(minutes=15)


class ProviderUsageReconciliationTaskRetryError(RuntimeError):
    """Safe Celery retry marker without persistence exception details."""


@celery_app.task(name=_TASK_NAME, bind=True, max_retries=3)
def reconcile_provider_usage(self: Any, limit: int = 100) -> dict[str, int]:
    session = SessionLocal()
    try:
        service = ProviderUsageLedgerService()
        stale_ids = service.reconcile_stale_provider_started(
            session,
            stale_after=_STALE_PROVIDER_STARTED_AFTER,
            limit=limit,
        )
        projection = service.reconcile_pending_projections(
            session,
            limit=limit,
        )
        return {
            "stale_started_count": len(stale_ids),
            "projection_claimed_count": projection.claimed_count,
            "projection_projected_count": projection.projected_count,
            "projection_failed_count": projection.failed_count,
        }
    except Exception as exc:
        session.rollback()
        logger.error(
            "[ProviderUsage] reconciliation failed: error_type=%s",
            type(exc).__name__,
        )
        raise self.retry(
            exc=ProviderUsageReconciliationTaskRetryError(
                "provider usage reconciliation retry requested"
            ),
            countdown=2**self.request.retries,
        ) from None
    finally:
        session.close()


__all__ = ["reconcile_provider_usage"]
