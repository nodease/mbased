from __future__ import annotations

from apps.shared.audit import record_audit
from apps.shared.audit.actions import AuditAction


class LoginAuditRecorder:
    _DIMENSIONS = frozenset({"account", "network", "account_network"})
    _REASON_CODES = frozenset(
        {
            "auth.login.succeeded",
            "auth.login.invalid_credentials",
            "auth.login.inactive",
            "auth.login.rate_limited",
            "auth.login.limiter_unavailable",
            "auth.login.internal_error",
        }
    )

    def record_success(
        self,
        *,
        request_id: str | None,
        policy_version: str,
        actor_id: str,
        actor_name: str,
    ) -> None:
        metadata = {
            "reason_code": "auth.login.succeeded",
            "limiter_policy_version": policy_version,
            "actor": {"id": actor_id, "name": actor_name},
        }
        safe_request_id = self._safe_request_id(request_id)
        if safe_request_id:
            metadata["request_id"] = safe_request_id
        record_audit(
            action=AuditAction.USER_LOGIN,
            category="action",
            actor_id=actor_id,
            actor_type="user",
            metadata=metadata,
        )

    def record_failure(
        self,
        *,
        request_id: str | None,
        policy_version: str,
        reason_code: str,
        limited_dimensions: tuple[str, ...] = (),
    ) -> None:
        safe_reason_code = (
            reason_code
            if reason_code in self._REASON_CODES
            else "auth.login.internal_error"
        )
        metadata: dict[str, object] = {
            "reason_code": safe_reason_code,
            "limiter_policy_version": policy_version,
        }
        safe_request_id = self._safe_request_id(request_id)
        if safe_request_id:
            metadata["request_id"] = safe_request_id
        safe_dimensions = sorted(set(limited_dimensions) & self._DIMENSIONS)
        if safe_dimensions:
            metadata["limited_dimensions"] = safe_dimensions
        record_audit(
            action=AuditAction.USER_LOGIN_FAILED,
            category="action",
            actor_type="system",
            status="failure",
            metadata=metadata,
        )

    @staticmethod
    def _safe_request_id(request_id: str | None) -> str | None:
        if (
            isinstance(request_id, str)
            and 1 <= len(request_id) <= 128
            and request_id.isprintable()
        ):
            return request_id
        return None
