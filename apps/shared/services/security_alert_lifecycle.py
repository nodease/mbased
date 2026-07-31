from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator
from uuid import UUID

from apps.shared.db.models.audit_log import (
    ActorType,
    AuditCategory,
    AuditLog,
    AuditStatus,
)
from apps.shared.db.models.security_alert import SecurityAlert
from apps.shared.schemas.member_access import normalize_management_reason
from sqlalchemy import update

_RESOLUTION_TYPES = frozenset({"mitigated", "false_positive", "accepted_risk"})


class SecurityAlertStaleStateError(ValueError):
    pass


def acknowledge_security_alert(
    db: Any,
    *,
    alert: Any,
    manager_id: UUID,
    acknowledged_at: datetime,
    expected_version: int,
) -> Any:
    _require_state(alert, allowed_statuses=("open",), expected_version=expected_version)

    with _rollback_on_error(db):
        _claim_transition_in_database(
            db,
            alert=alert,
            allowed_statuses=("open",),
            expected_version=expected_version,
            values={
                "status": "acknowledged",
                "acknowledged_by": manager_id,
                "acknowledged_at": acknowledged_at,
            },
        )
        alert.status = "acknowledged"
        alert.lifecycle_version += 1
        alert.acknowledged_by = manager_id
        alert.acknowledged_at = acknowledged_at

        db.add(
            _build_lifecycle_audit(
                alert=alert,
                manager_id=manager_id,
                occurred_at=acknowledged_at,
                action="security_alert.acknowledged",
            )
        )
    return alert


def resolve_security_alert(
    db: Any,
    *,
    alert: Any,
    manager_id: UUID,
    resolved_at: datetime,
    expected_version: int,
    resolution_type: str,
    reason: str,
    reason_sanitizer: Any,
) -> Any:
    _require_state(
        alert,
        allowed_statuses=("open", "acknowledged"),
        expected_version=expected_version,
    )
    if resolution_type not in _RESOLUTION_TYPES:
        raise ValueError("resolution_type")

    normalized_reason = normalize_management_reason(reason)
    if normalized_reason is None:
        raise ValueError("resolution_reason")
    sanitized_reason = normalize_management_reason(
        reason_sanitizer.sanitize(normalized_reason)
    )
    if sanitized_reason is None:
        raise ValueError("resolution_reason")

    with _rollback_on_error(db):
        _claim_transition_in_database(
            db,
            alert=alert,
            allowed_statuses=("open", "acknowledged"),
            expected_version=expected_version,
            values={
                "status": "resolved",
                "resolution_type": resolution_type,
                "resolution_reason": sanitized_reason,
                "resolved_by": manager_id,
                "resolved_at": resolved_at,
            },
        )
        alert.status = "resolved"
        alert.lifecycle_version += 1
        alert.resolution_type = resolution_type
        alert.resolution_reason = sanitized_reason
        alert.resolved_by = manager_id
        alert.resolved_at = resolved_at

        audit = _build_lifecycle_audit(
            alert=alert,
            manager_id=manager_id,
            occurred_at=resolved_at,
            action="security_alert.resolved",
        )
        audit.audit_metadata.update(
            {
                "resolution_type": resolution_type,
                "resolution_reason": sanitized_reason,
            }
        )
        db.add(audit)
    return alert


def reopen_security_alert(
    db: Any,
    *,
    alert: Any,
    manager_id: UUID,
    reopened_at: datetime,
    expected_version: int,
) -> Any:
    _require_state(
        alert,
        allowed_statuses=("acknowledged",),
        expected_version=expected_version,
    )

    with _rollback_on_error(db):
        _claim_transition_in_database(
            db,
            alert=alert,
            allowed_statuses=("acknowledged",),
            expected_version=expected_version,
            values={
                "status": "open",
                "acknowledged_by": None,
                "acknowledged_at": None,
            },
        )
        alert.status = "open"
        alert.lifecycle_version += 1
        alert.acknowledged_by = None
        alert.acknowledged_at = None

        db.add(
            _build_lifecycle_audit(
                alert=alert,
                manager_id=manager_id,
                occurred_at=reopened_at,
                action="security_alert.reopened",
            )
        )
    return alert


def _claim_transition_in_database(
    db: Any,
    *,
    alert: Any,
    allowed_statuses: tuple[str, ...],
    expected_version: int,
    values: dict[str, Any],
) -> None:
    execute = getattr(db, "execute", None)
    if not callable(execute):
        return

    statement = (
        update(SecurityAlert)
        .where(
            SecurityAlert.id == alert.id,
            SecurityAlert.status.in_(allowed_statuses),
            SecurityAlert.lifecycle_version == expected_version,
        )
        .values(
            **values,
            lifecycle_version=SecurityAlert.lifecycle_version + 1,
        )
        .returning(SecurityAlert.id)
        .execution_options(synchronize_session=False)
    )
    if execute(statement).scalar_one_or_none() is None:
        raise SecurityAlertStaleStateError("stale_state")


def _require_state(
    alert: Any,
    *,
    allowed_statuses: tuple[str, ...],
    expected_version: int,
) -> None:
    if (
        alert.status not in allowed_statuses
        or alert.lifecycle_version != expected_version
    ):
        raise SecurityAlertStaleStateError("stale_state")


@contextmanager
def _rollback_on_error(db: Any) -> Iterator[None]:
    try:
        yield
    except Exception:
        db.rollback()
        raise


def _build_lifecycle_audit(
    *,
    alert: Any,
    manager_id: UUID,
    occurred_at: datetime,
    action: str,
) -> AuditLog:
    return AuditLog(
        occurred_at=occurred_at,
        actor_id=manager_id,
        actor_type=ActorType.USER,
        category=AuditCategory.ACTION,
        action=action,
        target_type="security_alert",
        target_id=str(alert.id),
        status=AuditStatus.SUCCESS,
        audit_metadata={
            "organization_id": str(alert.organization_id),
            "rule_id": alert.rule_id,
            "rule_version": alert.rule_version,
            "severity": alert.severity,
        },
    )
