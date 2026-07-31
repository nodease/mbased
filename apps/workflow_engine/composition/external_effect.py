from __future__ import annotations

from datetime import timedelta
from typing import Callable

from celery import current_task
from sqlalchemy.orm import Session

from apps.shared.celery_app import celery_app
from apps.shared.db.session import SessionLocal
from apps.workflow_engine.adapters.db.external_effect_repository import (
    SQLAlchemyEffectAttemptRepository,
)
from apps.workflow_engine.application.external_effect import ExternalEffectExecutor
from apps.workflow_engine.domain.external_effect import provider_contract_registry


def external_effect_claim_ttl_seconds() -> int:
    hard_limit = celery_app.conf.task_time_limit
    if not isinstance(hard_limit, (int, float)) or hard_limit <= 0:
        raise RuntimeError("workflow task hard time limit is invalid")
    return int(hard_limit) + 30


def build_external_effect_executor(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    task_deadline: Callable[[], float | None] | None = None,
) -> ExternalEffectExecutor:
    contracts = provider_contract_registry()
    repository = SQLAlchemyEffectAttemptRepository(
        session_factory,
        contracts=contracts,
    )
    return ExternalEffectExecutor(
        repository=repository,
        claim_ttl=timedelta(seconds=external_effect_claim_ttl_seconds()),
        task_deadline=task_deadline,
        retry_available=external_effect_retry_available,
    )


def external_effect_retry_available() -> bool:
    try:
        request = current_task.request
        retries = int(request.retries)
        max_retries = current_task.max_retries
        return max_retries is None or retries < int(max_retries)
    except (AttributeError, TypeError, ValueError):
        return False
