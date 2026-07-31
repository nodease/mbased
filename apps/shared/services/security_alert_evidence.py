from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.security_alert import SecurityAlert, SecurityAlertAuditEvent
from sqlalchemy.dialects.postgresql import insert

_ACTIVE_STATUSES = frozenset({"open", "acknowledged"})


def link_security_alert_evidence(
    db: Any,
    *,
    alert: Any,
    detected_at: datetime,
    audit_log_id: UUID | None = None,
    audit_log: Any | None = None,
) -> bool:
    if getattr(alert, "status", "open") not in _ACTIVE_STATUSES:
        return False

    insert_once = getattr(db, "add_evidence_once", None)
    get = getattr(db, "get", None)
    uses_database_session = callable(get) and not callable(insert_once)

    audit_log_id = _validated_audit_log_id(
        db,
        alert=alert,
        audit_log_id=audit_log_id,
        audit_log=audit_log,
        load_from_database=uses_database_session,
    )
    if audit_log_id is None:
        return False

    alert = _lock_active_alert(
        db,
        alert=alert,
        lock_in_database=uses_database_session,
    )
    if alert is None:
        return False

    if not _insert_evidence_once(
        db,
        alert_id=alert.id,
        audit_log_id=audit_log_id,
        linked_at=detected_at,
    ):
        return False

    _record_occurrence(alert, detected_at=detected_at)
    return True


def _validated_audit_log_id(
    db: Any,
    *,
    alert: Any,
    audit_log_id: UUID | None,
    audit_log: Any | None,
    load_from_database: bool,
) -> UUID | None:
    if audit_log is None and audit_log_id is None:
        raise ValueError("audit_log or audit_log_id is required")

    if audit_log is None and load_from_database:
        audit_log = db.get(AuditLog, audit_log_id)

    if audit_log is not None:
        if not _has_matching_organization(alert, audit_log):
            return None
        return audit_log.id

    return audit_log_id


def _lock_active_alert(
    db: Any,
    *,
    alert: Any,
    lock_in_database: bool,
) -> Any | None:
    if not lock_in_database:
        return alert

    locked_alert = db.get(
        SecurityAlert,
        alert.id,
        populate_existing=True,
        with_for_update=True,
    )
    if locked_alert is None or locked_alert.status not in _ACTIVE_STATUSES:
        return None
    return locked_alert


def _record_occurrence(alert: Any, *, detected_at: datetime) -> None:
    alert.occurrence_count += 1
    alert.last_detected_at = (
        detected_at
        if alert.last_detected_at is None
        else max(alert.last_detected_at, detected_at)
    )


def _has_matching_organization(alert: Any, audit_log: Any) -> bool:
    audit_metadata = audit_log.audit_metadata or {}
    return str(audit_metadata.get("organization_id")) == str(alert.organization_id)


def _insert_evidence_once(
    db: Any,
    *,
    alert_id: UUID,
    audit_log_id: UUID,
    linked_at: datetime,
) -> bool:
    insert_once = getattr(db, "add_evidence_once", None)
    if callable(insert_once):
        return insert_once(alert_id=alert_id, audit_log_id=audit_log_id)

    statement = (
        insert(SecurityAlertAuditEvent)
        .values(
            security_alert_id=alert_id,
            audit_log_id=audit_log_id,
            linked_at=linked_at,
        )
        .on_conflict_do_nothing(
            constraint="uq_security_alert_audit_events_alert_audit"
        )
        .returning(SecurityAlertAuditEvent.id)
    )
    return db.execute(statement).scalar_one_or_none() is not None
