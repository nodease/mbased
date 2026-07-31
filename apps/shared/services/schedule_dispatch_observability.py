from __future__ import annotations

import logging

from apps.shared.domain.schedule_dispatch import (
    OUTCOME_RESOLUTIONS,
    SCHEDULE_DISPATCH_MODES,
    SCHEDULE_DISPATCH_REASONS,
    SCHEDULE_DISPATCH_STATUSES,
)

_EVENT_NAMES = frozenset(
    {
        "schedule_claim_created_total",
        "schedule_claim_conflict_total",
        "schedule_enqueue_attempt_total",
        "schedule_enqueue_failure_total",
        "schedule_duplicate_delivery_suppressed_total",
        "schedule_claim_dead_letter_total",
        "schedule_claim_pending_age_seconds",
        "schedule_claim_running_age_seconds",
        "schedule_claim_workflow_run_missing_total",
        "schedule_claim_outcome_reviewed_total",
    }
)
_STATUSES = SCHEDULE_DISPATCH_STATUSES | {"duplicate"}
_REASONS = (
    SCHEDULE_DISPATCH_REASONS
    | SCHEDULE_DISPATCH_STATUSES
    | OUTCOME_RESOLUTIONS
    | {"workflow_run_missing"}
)


def emit_schedule_dispatch_signal(
    logger: logging.Logger,
    event: str,
    *,
    value: int | float = 1,
    status: str | None = None,
    reason: str | None = None,
    mode: str | None = None,
) -> bool:
    if (
        event not in _EVENT_NAMES
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or (status is not None and status not in _STATUSES)
        or (reason is not None and reason not in _REASONS)
        or (mode is not None and mode not in SCHEDULE_DISPATCH_MODES)
    ):
        return False
    try:
        logger.info(
            "schedule_dispatch_signal event=%s value=%s status=%s reason=%s mode=%s",
            event,
            value,
            status or "none",
            reason or "none",
            mode or "none",
        )
    except Exception:
        # Observability is best-effort; the durable claim state remains canonical.
        return False
    return True
