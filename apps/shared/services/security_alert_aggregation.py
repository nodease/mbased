from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Sequence
from uuid import uuid4

from apps.shared.db.models.audit_log import (
    ActorType,
    AuditCategory,
    AuditLog,
    AuditStatus,
)
from apps.shared.db.models.security_alert import SecurityAlert
from apps.shared.services.security_alert_evidence import (
    link_security_alert_evidence,
)
from apps.shared.services.security_alert_rule_evaluator import (
    build_security_alert_detection_key,
)
from apps.shared.services.security_alert_rule_registry import (
    get_security_alert_rule,
)
from sqlalchemy.exc import IntegrityError

_SECURITY_ALERT_COOLDOWN = timedelta(minutes=30)


def is_security_alert_cooldown_active(
    *,
    last_detected_at: datetime,
    event_at: datetime,
) -> bool:
    # Delayed/reconciled events may be older than the alert's latest event.
    # They can add evidence, but must never open a new episode in the past.
    return event_at < last_detected_at + _SECURITY_ALERT_COOLDOWN


def aggregate_security_alert_detection(
    db: Any,
    *,
    candidate: Any,
    audit_logs: Sequence[Any],
    detected_at: datetime,
) -> SecurityAlert | None:
    detection_key = _candidate_detection_key(candidate)
    matched_audits = _select_candidate_audits(candidate, audit_logs)

    try:
        active_alert = _find_active_alert(db, detection_key=detection_key)
        if active_alert is not None:
            updated_alert = _update_active_alert_during_cooldown(
                db,
                alert=active_alert,
                audit_logs=matched_audits,
                detected_at=detected_at,
            )
            if updated_alert is not None:
                return updated_alert
            return _start_new_episode_after_cooldown(
                db,
                alert=active_alert,
                rule_id=candidate.rule_id,
                audit_logs=matched_audits,
                detected_at=detected_at,
            )

        latest_resolved = _find_latest_resolved_alert(
            db,
            detection_key=detection_key,
        )
        fresh_audits = _unique_audits_after_resolution(
            matched_audits,
            latest_resolved,
        )
        if not _meets_rule_threshold(candidate.rule_id, fresh_audits):
            return None

        return _create_alert_with_savepoint(
            db,
            candidate=candidate,
            detection_key=detection_key,
            audit_logs=fresh_audits,
            detected_at=detected_at,
        )
    except IntegrityError:
        winner = _recover_active_alert_after_conflict(
            db,
            detection_key=detection_key,
            audit_logs=matched_audits,
        )
        if winner is None:
            raise
        return winner
    except Exception:
        db.rollback()
        raise


def _update_active_alert_during_cooldown(
    db: Any,
    *,
    alert: Any,
    audit_logs: Sequence[Any],
    detected_at: datetime,
) -> Any | None:
    if not is_security_alert_cooldown_active(
        last_detected_at=alert.last_detected_at,
        event_at=detected_at,
    ):
        return None
    _link_evidence(db, alert=alert, audit_logs=audit_logs)
    return alert


def _start_new_episode_after_cooldown(
    db: Any,
    *,
    alert: Any,
    rule_id: str,
    audit_logs: Sequence[Any],
    detected_at: datetime,
) -> Any | None:
    if not _meets_rule_threshold(rule_id, audit_logs):
        return None
    if _link_evidence(db, alert=alert, audit_logs=audit_logs) == 0:
        return None
    alert.episode_count += 1
    alert.last_episode_started_at = detected_at
    return alert


def _select_candidate_audits(
    candidate: Any,
    audit_logs: Sequence[Any],
) -> list[Any]:
    matched_audit_ids = getattr(candidate, "matched_audit_ids", None)
    if matched_audit_ids is None:
        return list(audit_logs)
    matched_ids = set(matched_audit_ids)
    return [audit_log for audit_log in audit_logs if audit_log.id in matched_ids]


def _candidate_detection_key(candidate: Any) -> str:
    existing_key = getattr(candidate, "detection_key", None)
    if existing_key:
        return existing_key
    return build_security_alert_detection_key(
        organization_id=candidate.organization_id,
        actor_id=candidate.subject_actor_id,
        rule_id=candidate.rule_id,
        rule_version=candidate.rule_version,
        policy_reason=candidate.policy_reason,
    )


def _flush(db: Any) -> None:
    flush = getattr(db, "flush", None)
    if callable(flush):
        flush()


def _create_alert_in_uow(
    db: Any,
    *,
    candidate: Any,
    detection_key: str,
    audit_logs: Sequence[Any],
    detected_at: datetime,
) -> SecurityAlert:
    alert = _build_alert(
        candidate=candidate,
        detection_key=detection_key,
        detected_at=detected_at,
    )
    db.add(alert)
    _flush(db)
    _link_evidence(db, alert=alert, audit_logs=audit_logs)
    db.add(_build_detected_audit(alert=alert, occurred_at=detected_at))
    return alert


def _create_alert_with_savepoint(
    db: Any,
    *,
    candidate: Any,
    detection_key: str,
    audit_logs: Sequence[Any],
    detected_at: datetime,
) -> SecurityAlert:
    begin_nested = getattr(db, "begin_nested", None)
    savepoint = begin_nested() if callable(begin_nested) else None
    try:
        alert = _create_alert_in_uow(
            db,
            candidate=candidate,
            detection_key=detection_key,
            audit_logs=audit_logs,
            detected_at=detected_at,
        )
    except IntegrityError:
        if savepoint is not None:
            savepoint.rollback()
        raise

    commit = getattr(savepoint, "commit", None) if savepoint is not None else None
    if callable(commit):
        commit()
    return alert


def _recover_active_alert_after_conflict(
    db: Any,
    *,
    detection_key: str,
    audit_logs: Sequence[Any],
) -> Any | None:
    winner = _find_active_alert(db, detection_key=detection_key)
    if winner is not None:
        _link_evidence(db, alert=winner, audit_logs=audit_logs)
    return winner


def _build_alert(
    *,
    candidate: Any,
    detection_key: str,
    detected_at: datetime,
) -> SecurityAlert:
    return SecurityAlert(
        id=uuid4(),
        organization_id=candidate.organization_id,
        subject_actor_id=candidate.subject_actor_id,
        rule_id=candidate.rule_id,
        rule_version=candidate.rule_version,
        severity=candidate.severity,
        status="open",
        policy_reason=candidate.policy_reason,
        detection_key=detection_key,
        occurrence_count=0,
        episode_count=1,
        first_detected_at=detected_at,
        last_detected_at=detected_at,
        last_episode_started_at=detected_at,
        lifecycle_version=1,
    )


def _find_active_alert(db: Any, *, detection_key: str) -> Any | None:
    finder = getattr(db, "find_active_alert", None)
    if callable(finder):
        return finder(detection_key=detection_key)
    return (
        db.query(SecurityAlert)
        .filter(
            SecurityAlert.detection_key == detection_key,
            SecurityAlert.status.in_(("open", "acknowledged")),
        )
        .with_for_update()
        .one_or_none()
    )


def _find_latest_resolved_alert(db: Any, *, detection_key: str) -> Any | None:
    finder = getattr(db, "find_latest_resolved_alert", None)
    if callable(finder):
        return finder(detection_key=detection_key)
    return (
        db.query(SecurityAlert)
        .filter(
            SecurityAlert.detection_key == detection_key,
            SecurityAlert.status == "resolved",
        )
        .order_by(SecurityAlert.resolved_at.desc())
        .first()
    )


def _unique_audits_after_resolution(
    audit_logs: Sequence[Any],
    resolved_alert: Any | None,
) -> list[Any]:
    unique_audits = {audit_log.id: audit_log for audit_log in audit_logs}
    if resolved_alert is None:
        return list(unique_audits.values())
    return [
        audit_log
        for audit_log in unique_audits.values()
        if audit_log.occurred_at > resolved_alert.resolved_at
    ]


def _meets_rule_threshold(rule_id: str, audit_logs: Sequence[Any]) -> bool:
    rule = get_security_alert_rule(rule_id)
    if rule is None:
        return False
    if rule.count_mode == "distinct_targets":
        distinct_targets = {
            (audit_log.target_type, audit_log.target_id)
            for audit_log in audit_logs
        }
        return len(distinct_targets) >= rule.threshold
    return len(audit_logs) >= rule.threshold


def _link_evidence(db: Any, *, alert: Any, audit_logs: Sequence[Any]) -> int:
    linked_count = 0
    for audit_log in audit_logs:
        linked = link_security_alert_evidence(
            db,
            alert=alert,
            audit_log=audit_log,
            detected_at=audit_log.occurred_at,
        )
        if linked:
            linked_count += 1
            _flush(db)
    return linked_count


def _build_detected_audit(*, alert: Any, occurred_at: datetime) -> AuditLog:
    return AuditLog(
        occurred_at=occurred_at,
        actor_id=None,
        actor_type=ActorType.SYSTEM,
        category=AuditCategory.ACTION,
        action="security_alert.detected",
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
