from __future__ import annotations

import logging
from collections import Counter
from typing import ClassVar

from apps.gateway.application.csrf.token import CsrfValidationReason

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter as PrometheusCounter
except Exception:
    PrometheusCounter = None

try:
    CSRF_DENIALS = (
        PrometheusCounter(
            "auth_csrf_denials_total",
            "Rejected cookie-authenticated browser requests.",
            ["reason", "policy", "method"],
        )
        if PrometheusCounter
        else None
    )
except ValueError:
    CSRF_DENIALS = None


class CsrfObservability:
    _REASONS = frozenset(reason.value for reason in CsrfValidationReason)
    _POLICIES = frozenset({"cookie_authenticated", "pre_auth_session"})
    _METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
    _local_counters: ClassVar[Counter[tuple[str, str, str]]] = Counter()

    @classmethod
    def record(cls, *, reason: str, policy: str, method: str) -> None:
        safe_reason = reason if reason in cls._REASONS else "unknown"
        safe_policy = policy if policy in cls._POLICIES else "unknown"
        safe_method = method if method in cls._METHODS else "unknown"
        labels = (safe_reason, safe_policy, safe_method)
        cls._local_counters[labels] += 1
        if CSRF_DENIALS is not None:
            CSRF_DENIALS.labels(
                reason=safe_reason,
                policy=safe_policy,
                method=safe_method,
            ).inc()
        logger.info(
            "auth.csrf_denied",
            extra={
                "event": "auth.csrf_denied",
                "reason": safe_reason,
                "policy": safe_policy,
                "method": safe_method,
            },
        )
