from __future__ import annotations

import logging
from collections import Counter
from typing import ClassVar

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter as PrometheusCounter
except Exception:
    PrometheusCounter = None

try:
    MAIL_PROCESSING_TRANSITIONS = (
        PrometheusCounter(
            "mail_processing_transitions_total",
            "Mail processing state transition count.",
            ["event", "outcome"],
        )
        if PrometheusCounter
        else None
    )
except ValueError:
    MAIL_PROCESSING_TRANSITIONS = None


class MailProcessingObservability:
    """Record bounded Mail state metrics without message or tenant identifiers."""

    _EVENTS = frozenset({"registration", "draft_admission", "draft", "ack"})
    _OUTCOMES = frozenset(
        {
            "resolved",
            "acquired",
            "duplicate",
            "in_progress",
            "retry_not_due",
            "exhausted",
            "succeeded",
            "failed_before_effect",
            "outcome_unknown",
            "pending",
        }
    )
    _local_counters: ClassVar[Counter[tuple[str, str]]] = Counter()

    @classmethod
    def record(cls, event: str, outcome: str) -> None:
        safe_event = event if event in cls._EVENTS else "unknown"
        safe_outcome = outcome if outcome in cls._OUTCOMES else "unknown"
        cls._local_counters[(safe_event, safe_outcome)] += 1
        if MAIL_PROCESSING_TRANSITIONS is not None:
            MAIL_PROCESSING_TRANSITIONS.labels(
                event=safe_event,
                outcome=safe_outcome,
            ).inc()
        logger.info(
            "mail.processing_transition",
            extra={
                "event": "mail.processing_transition",
                "transition_event": safe_event,
                "outcome": safe_outcome,
            },
        )

    @classmethod
    def get_local_counter(cls, event: str, outcome: str) -> int:
        return cls._local_counters[(event, outcome)]

    @classmethod
    def reset_local_counters(cls) -> None:
        cls._local_counters.clear()
