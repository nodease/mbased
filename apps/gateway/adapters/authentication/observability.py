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
    LOGIN_OUTCOMES = (
        PrometheusCounter(
            "auth_login_attempts_total",
            "Password login outcomes after distributed admission.",
            ["outcome", "policy_version", "operation"],
        )
        if PrometheusCounter
        else None
    )
except ValueError:
    LOGIN_OUTCOMES = None


class LoginObservability:
    _OUTCOMES = frozenset(
        {
            "succeeded",
            "invalid_credentials",
            "inactive",
            "rate_limited",
            "limiter_unavailable",
            "reset_failed",
            "audit_failed",
            "internal_error",
        }
    )
    _OPERATIONS = frozenset({"login", "admission", "reset", "audit"})
    _POLICY_VERSIONS = frozenset({"v1"})
    _local_counters: ClassVar[Counter[tuple[str, str, str]]] = Counter()

    @classmethod
    def record(cls, *, outcome: str, policy_version: str, operation: str) -> None:
        safe_outcome = outcome if outcome in cls._OUTCOMES else "unknown"
        safe_policy = (
            policy_version if policy_version in cls._POLICY_VERSIONS else "unknown"
        )
        safe_operation = operation if operation in cls._OPERATIONS else "unknown"
        labels = (safe_outcome, safe_policy, safe_operation)
        cls._local_counters[labels] += 1
        if LOGIN_OUTCOMES is not None:
            LOGIN_OUTCOMES.labels(
                outcome=safe_outcome,
                policy_version=safe_policy,
                operation=safe_operation,
            ).inc()
        logger.info(
            "auth.password_login",
            extra={
                "event": "auth.password_login",
                "outcome": safe_outcome,
                "policy_version": safe_policy,
                "operation": safe_operation,
            },
        )
