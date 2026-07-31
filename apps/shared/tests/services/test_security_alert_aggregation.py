from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.security_alert import SecurityAlert
from apps.shared.services.security_alert_rule_evaluator import (
    build_security_alert_detection_key,
)
from sqlalchemy.exc import IntegrityError

_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _candidate(*, organization_id=None, actor_id=None):
    organization_id = organization_id or uuid4()
    actor_id = actor_id or uuid4()
    rule_id = "repeated_permission_denied"
    rule_version = "v1"
    return SimpleNamespace(
        organization_id=organization_id,
        subject_actor_id=actor_id,
        rule_id=rule_id,
        rule_version=rule_version,
        severity="medium",
        policy_reason=None,
        detection_key=build_security_alert_detection_key(
            organization_id=organization_id,
            actor_id=actor_id,
            rule_id=rule_id,
            rule_version=rule_version,
        ),
    )


def _audit_logs(organization_id, *, count, start_at):
    return [
        SimpleNamespace(
            id=uuid4(),
            occurred_at=start_at + timedelta(seconds=index * 10),
            audit_metadata={"organization_id": str(organization_id)},
        )
        for index in range(count)
    ]


def _aggregate(db, *, candidate, audit_logs, detected_at):
    module = importlib.import_module(
        "apps.shared.services.security_alert_aggregation"
    )
    return module.aggregate_security_alert_detection(
        db,
        candidate=candidate,
        audit_logs=audit_logs,
        detected_at=detected_at,
    )


def test_sal_tc_s002_creates_alert_evidence_and_detected_audit_in_one_uow():
    candidate = _candidate()
    audit_logs = _audit_logs(
        candidate.organization_id,
        count=5,
        start_at=_NOW - timedelta(minutes=1),
    )
    db = _AggregationDb()

    alert = _aggregate(
        db,
        candidate=candidate,
        audit_logs=audit_logs,
        detected_at=_NOW,
    )

    assert isinstance(alert, SecurityAlert)
    assert alert in db.added
    assert alert.status == "open"
    assert alert.detection_key == candidate.detection_key
    assert alert.occurrence_count == 5
    assert alert.episode_count == 1
    assert alert.first_detected_at == _NOW
    assert alert.last_detected_at == _NOW
    assert alert.last_episode_started_at == _NOW
    assert db.evidence_keys == {
        (alert.id, audit_log.id) for audit_log in audit_logs
    }
    detected_audits = [
        row
        for row in db.added
        if isinstance(row, AuditLog) and row.action == "security_alert.detected"
    ]
    assert len(detected_audits) == 1
    assert detected_audits[0].target_id == str(alert.id)
    assert db.commits == 0


def test_aggregation_links_only_candidate_matched_audits():
    candidate = _candidate()
    audit_logs = _audit_logs(
        candidate.organization_id,
        count=6,
        start_at=_NOW - timedelta(minutes=1),
    )
    candidate.matched_audit_ids = tuple(
        audit_log.id for audit_log in audit_logs[:5]
    )
    db = _AggregationDb()

    alert = _aggregate(
        db,
        candidate=candidate,
        audit_logs=audit_logs,
        detected_at=_NOW,
    )

    assert alert.occurrence_count == 5
    assert db.evidence_keys == {
        (alert.id, audit_log.id) for audit_log in audit_logs[:5]
    }


@pytest.mark.parametrize("status", ["open", "acknowledged"])
def test_sal_tc_s003_s004_cooldown_updates_existing_active_alert(status):
    candidate = _candidate()
    alert = _active_alert(candidate, status=status)
    new_audit = _audit_logs(
        candidate.organization_id,
        count=1,
        start_at=_NOW,
    )[0]
    db = _AggregationDb(active_alert=alert)

    result = _aggregate(
        db,
        candidate=candidate,
        audit_logs=[new_audit],
        detected_at=_NOW,
    )

    assert result is alert
    assert alert.status == status
    assert alert.occurrence_count == 6
    assert alert.last_detected_at == _NOW
    assert db.evidence_keys == {(alert.id, new_audit.id)}
    assert not any(
        isinstance(row, AuditLog) and row.action == "security_alert.detected"
        for row in db.added
    )
    assert db.commits == 0


def test_sal_tc_s005_resolved_alert_uses_only_fresh_events_for_recurrence():
    candidate = _candidate()
    resolved_at = _NOW - timedelta(minutes=2)
    resolved_alert = SimpleNamespace(
        **vars(_active_alert(candidate, status="resolved")),
        resolved_at=resolved_at,
    )
    old_events = _audit_logs(
        candidate.organization_id,
        count=3,
        start_at=resolved_at - timedelta(minutes=1),
    )
    fresh_events = _audit_logs(
        candidate.organization_id,
        count=2,
        start_at=resolved_at + timedelta(seconds=1),
    )
    original_count = resolved_alert.occurrence_count
    original_last_detected_at = resolved_alert.last_detected_at
    db = _AggregationDb(latest_resolved_alert=resolved_alert)

    result = _aggregate(
        db,
        candidate=candidate,
        audit_logs=[*old_events, *fresh_events],
        detected_at=_NOW,
    )

    assert result is None
    assert resolved_alert.occurrence_count == original_count
    assert resolved_alert.last_detected_at == original_last_detected_at
    assert db.evidence_keys == set()
    assert db.added == []


def test_resolved_multi_resource_alert_requires_fresh_distinct_targets():
    candidate = _candidate()
    candidate.rule_id = "multi_resource_permission_probe"
    candidate.severity = "high"
    candidate.detection_key = build_security_alert_detection_key(
        organization_id=candidate.organization_id,
        actor_id=candidate.subject_actor_id,
        rule_id=candidate.rule_id,
        rule_version=candidate.rule_version,
    )
    resolved_at = _NOW - timedelta(minutes=2)
    resolved_alert = SimpleNamespace(
        **vars(_active_alert(candidate, status="resolved")),
        resolved_at=resolved_at,
    )
    old_events = _audit_logs(
        candidate.organization_id,
        count=4,
        start_at=resolved_at - timedelta(minutes=1),
    )
    for index, audit_log in enumerate(old_events):
        audit_log.target_type = "workflow"
        audit_log.target_id = f"old-{index}"
    fresh_events = _audit_logs(
        candidate.organization_id,
        count=5,
        start_at=resolved_at + timedelta(seconds=1),
    )
    for audit_log in fresh_events:
        audit_log.target_type = "workflow"
        audit_log.target_id = "fresh-repeated-target"
    candidate.matched_audit_ids = tuple(
        audit_log.id for audit_log in [*old_events, *fresh_events]
    )
    db = _AggregationDb(latest_resolved_alert=resolved_alert)

    result = _aggregate(
        db,
        candidate=candidate,
        audit_logs=[*old_events, *fresh_events],
        detected_at=_NOW,
    )

    assert result is None
    assert db.added == []
    assert db.evidence_keys == set()


def test_sal_tc_u011_cooldown_expires_at_exactly_thirty_minutes():
    module = importlib.import_module(
        "apps.shared.services.security_alert_aggregation"
    )
    last_detected_at = _NOW

    assert module.is_security_alert_cooldown_active(
        last_detected_at=last_detected_at,
        event_at=_NOW + timedelta(minutes=30) - timedelta(microseconds=1),
    ) is True
    assert module.is_security_alert_cooldown_active(
        last_detected_at=last_detected_at,
        event_at=_NOW + timedelta(minutes=30),
    ) is False
    assert module.is_security_alert_cooldown_active(
        last_detected_at=last_detected_at,
        event_at=_NOW + timedelta(minutes=30, microseconds=1),
    ) is False
    assert module.is_security_alert_cooldown_active(
        last_detected_at=last_detected_at,
        event_at=_NOW - timedelta(hours=1),
    ) is True


def test_aggregation_threshold_comes_from_rule_registry(monkeypatch):
    module = importlib.import_module(
        "apps.shared.services.security_alert_aggregation"
    )
    registry = importlib.import_module(
        "apps.shared.services.security_alert_rule_registry"
    )
    original = registry.get_security_alert_rule("repeated_permission_denied")
    stricter = registry.SecurityAlertRule(
        **{**vars(original), "threshold": 6}
    )
    monkeypatch.setattr(
        module,
        "get_security_alert_rule",
        lambda rule_id: stricter if rule_id == stricter.rule_id else None,
    )

    assert module._meets_rule_threshold(
        "repeated_permission_denied",
        _audit_logs(uuid4(), count=5, start_at=_NOW),
    ) is False


@pytest.mark.parametrize("status", ["open", "acknowledged"])
def test_expired_cooldown_does_not_update_existing_active_alert(status):
    candidate = _candidate()
    alert = _active_alert(candidate, status=status)
    alert.last_detected_at = _NOW - timedelta(minutes=30)
    original_count = alert.occurrence_count
    original_episode_count = alert.episode_count
    original_episode_started_at = alert.last_episode_started_at
    new_audit = _audit_logs(
        candidate.organization_id,
        count=1,
        start_at=_NOW,
    )[0]
    db = _AggregationDb(active_alert=alert)

    result = _aggregate(
        db,
        candidate=candidate,
        audit_logs=[new_audit],
        detected_at=_NOW,
    )

    assert result is None
    assert alert.occurrence_count == original_count
    assert alert.episode_count == original_episode_count
    assert alert.last_detected_at == _NOW - timedelta(minutes=30)
    assert alert.last_episode_started_at == original_episode_started_at
    assert db.evidence_keys == set()


@pytest.mark.parametrize("status", ["open", "acknowledged"])
def test_expired_cooldown_threshold_starts_new_episode(status):
    candidate = _candidate()
    alert = _active_alert(candidate, status=status)
    previous_episode_started_at = _NOW - timedelta(minutes=31)
    alert.last_detected_at = previous_episode_started_at
    alert.last_episode_started_at = previous_episode_started_at
    audit_logs = _audit_logs(
        candidate.organization_id,
        count=5,
        start_at=_NOW - timedelta(minutes=1),
    )
    candidate.matched_audit_ids = tuple(audit.id for audit in audit_logs)
    detected_at = audit_logs[-1].occurred_at
    original_count = alert.occurrence_count
    db = _AggregationDb(active_alert=alert)

    result = _aggregate(
        db,
        candidate=candidate,
        audit_logs=audit_logs,
        detected_at=detected_at,
    )

    assert result is alert
    assert alert.status == status
    assert alert.occurrence_count == original_count + 5
    assert alert.episode_count == 2
    assert alert.last_detected_at == detected_at
    assert alert.last_episode_started_at == detected_at
    assert db.evidence_keys == {
        (alert.id, audit_log.id) for audit_log in audit_logs
    }


def test_late_older_audit_updates_active_alert_without_moving_last_detected_back():
    candidate = _candidate()
    alert = _active_alert(candidate, status="open")
    alert.last_detected_at = _NOW
    late_audit = _audit_logs(
        candidate.organization_id,
        count=1,
        start_at=_NOW - timedelta(minutes=1),
    )[0]
    original_count = alert.occurrence_count
    db = _AggregationDb(active_alert=alert)

    result = _aggregate(
        db,
        candidate=candidate,
        audit_logs=[late_audit],
        detected_at=late_audit.occurred_at,
    )

    assert result is alert
    assert alert.occurrence_count == original_count + 1
    assert alert.last_detected_at == _NOW
    assert db.evidence_keys == {(alert.id, late_audit.id)}


def test_late_older_threshold_does_not_start_a_new_episode():
    candidate = _candidate()
    alert = _active_alert(candidate, status="open")
    alert.last_detected_at = _NOW
    original_count = alert.occurrence_count
    original_episode_count = alert.episode_count
    original_episode_started_at = alert.last_episode_started_at
    late_audits = _audit_logs(
        candidate.organization_id,
        count=5,
        start_at=_NOW - timedelta(hours=1),
    )
    candidate.matched_audit_ids = tuple(audit.id for audit in late_audits)
    detected_at = late_audits[-1].occurred_at
    db = _AggregationDb(active_alert=alert)

    result = _aggregate(
        db,
        candidate=candidate,
        audit_logs=late_audits,
        detected_at=detected_at,
    )

    assert result is alert
    assert alert.occurrence_count == original_count + 5
    assert alert.last_detected_at == _NOW
    assert alert.episode_count == original_episode_count
    assert alert.last_episode_started_at == original_episode_started_at


@pytest.mark.parametrize("failure", ["evidence", "detected_audit"])
def test_sal_tc_s006_s007_creation_failure_rolls_back_whole_uow(failure):
    candidate = _candidate()
    audit_logs = _audit_logs(
        candidate.organization_id,
        count=5,
        start_at=_NOW - timedelta(minutes=1),
    )
    db = _AggregationDb(failure=failure)

    with pytest.raises(RuntimeError, match=failure):
        _aggregate(
            db,
            candidate=candidate,
            audit_logs=audit_logs,
            detected_at=_NOW,
        )

    assert db.rollbacks == 1
    assert db.commits == 0
    assert db.added == []
    assert db.evidence_keys == set()


def test_reconciliation_conflict_preserves_earlier_batch_results():
    candidate = _candidate()
    audit_logs = _audit_logs(
        candidate.organization_id,
        count=5,
        start_at=_NOW - timedelta(minutes=1),
    )
    winner = _active_alert(candidate, status="open")
    earlier_batch_result = object()
    db = _AggregationConflictDb(
        winner=winner,
        earlier_batch_result=earlier_batch_result,
    )

    result = _aggregate(
        db,
        candidate=candidate,
        audit_logs=audit_logs,
        detected_at=_NOW,
    )

    assert result is winner
    assert db.outer_rollbacks == 0
    assert db.savepoint_rollbacks == 1
    assert db.earlier_batch_result is earlier_batch_result


def _active_alert(candidate, *, status):
    return SimpleNamespace(
        id=uuid4(),
        organization_id=candidate.organization_id,
        subject_actor_id=candidate.subject_actor_id,
        rule_id=candidate.rule_id,
        rule_version=candidate.rule_version,
        severity=candidate.severity,
        status=status,
        policy_reason=candidate.policy_reason,
        detection_key=candidate.detection_key,
        occurrence_count=5,
        episode_count=1,
        first_detected_at=_NOW - timedelta(minutes=10),
        last_detected_at=_NOW - timedelta(minutes=1),
        last_episode_started_at=_NOW - timedelta(minutes=10),
    )


class _AggregationDb:
    def __init__(
        self,
        *,
        active_alert=None,
        latest_resolved_alert=None,
        failure=None,
    ):
        self.active_alert = active_alert
        self.latest_resolved_alert = latest_resolved_alert
        self.failure = failure
        self.added = []
        self.evidence_keys = set()
        self.commits = 0
        self.rollbacks = 0

    def find_active_alert(self, *, detection_key):
        if (
            self.active_alert is not None
            and self.active_alert.detection_key == detection_key
        ):
            return self.active_alert
        return None

    def find_latest_resolved_alert(self, *, detection_key):
        if (
            self.latest_resolved_alert is not None
            and self.latest_resolved_alert.detection_key == detection_key
        ):
            return self.latest_resolved_alert
        return None

    def add(self, row):
        if (
            self.failure == "detected_audit"
            and isinstance(row, AuditLog)
            and row.action == "security_alert.detected"
        ):
            raise RuntimeError("detected_audit")
        self.added.append(row)

    def add_evidence_once(self, *, alert_id, audit_log_id):
        if self.failure == "evidence":
            raise RuntimeError("evidence")
        key = (alert_id, audit_log_id)
        if key in self.evidence_keys:
            return False
        self.evidence_keys.add(key)
        return True

    def rollback(self):
        self.rollbacks += 1
        self.added.clear()
        self.evidence_keys.clear()


class _AggregationConflictDb(_AggregationDb):
    def __init__(self, *, winner, earlier_batch_result):
        super().__init__()
        self.winner = winner
        self.earlier_batch_result = earlier_batch_result
        self.outer_rollbacks = 0
        self.savepoint_rollbacks = 0
        self.conflict_pending = True

    def begin_nested(self):
        return _AggregationSavepoint(self)

    def flush(self):
        if self.conflict_pending:
            raise IntegrityError("insert", {}, RuntimeError("duplicate"))

    def find_active_alert(self, *, detection_key):
        if self.conflict_pending:
            return None
        return self.winner

    def rollback(self):
        self.outer_rollbacks += 1
        self.earlier_batch_result = None
        self.conflict_pending = False


class _AggregationSavepoint:
    def __init__(self, db):
        self.db = db

    def rollback(self):
        self.db.savepoint_rollbacks += 1
        self.db.conflict_pending = False
