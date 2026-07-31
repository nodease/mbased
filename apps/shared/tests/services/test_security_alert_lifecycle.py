from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest


def test_acknowledge_updates_alert_and_adds_canonical_audit_to_same_unit_of_work():
    from apps.shared.services.security_alert_lifecycle import (
        acknowledge_security_alert,
    )

    organization_id = uuid4()
    manager_id = uuid4()
    alert = SimpleNamespace(
        id=uuid4(),
        organization_id=organization_id,
        rule_id="repeated_permission_denied",
        rule_version="v1",
        severity="medium",
        status="open",
        lifecycle_version=1,
        acknowledged_by=None,
        acknowledged_at=None,
    )
    acknowledged_at = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    db = _LifecycleDb()

    result = acknowledge_security_alert(
        db,
        alert=alert,
        manager_id=manager_id,
        acknowledged_at=acknowledged_at,
        expected_version=1,
    )

    assert result is alert
    assert alert.status == "acknowledged"
    assert alert.lifecycle_version == 2
    assert alert.acknowledged_by == manager_id
    assert alert.acknowledged_at == acknowledged_at
    assert db.commits == 0
    assert len(db.added) == 1

    audit = db.added[0]
    assert audit.action == "security_alert.acknowledged"
    assert audit.actor_id == manager_id
    assert audit.target_type == "security_alert"
    assert audit.target_id == str(alert.id)
    assert audit.audit_metadata == {
        "organization_id": str(organization_id),
        "rule_id": "repeated_permission_denied",
        "rule_version": "v1",
        "severity": "medium",
    }


def test_acknowledge_rolls_back_when_canonical_audit_add_fails():
    from apps.shared.services.security_alert_lifecycle import (
        acknowledge_security_alert,
    )

    alert = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        rule_id="repeated_permission_denied",
        rule_version="v1",
        severity="medium",
        status="open",
        lifecycle_version=4,
        acknowledged_by=None,
        acknowledged_at=None,
    )
    db = _FailingLifecycleDb(alert)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        acknowledge_security_alert(
            db,
            alert=alert,
            manager_id=uuid4(),
            acknowledged_at=datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc),
            expected_version=4,
        )

    assert db.commits == 0
    assert db.rollbacks == 1
    assert alert.status == "open"
    assert alert.lifecycle_version == 4
    assert alert.acknowledged_by is None
    assert alert.acknowledged_at is None


def test_same_expected_version_allows_only_one_acknowledge_and_one_audit():
    from apps.shared.services.security_alert_lifecycle import (
        acknowledge_security_alert,
    )

    first_manager_id = uuid4()
    second_manager_id = uuid4()
    alert = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        rule_id="repeated_permission_denied",
        rule_version="v1",
        severity="medium",
        status="open",
        lifecycle_version=1,
        acknowledged_by=None,
        acknowledged_at=None,
    )
    acknowledged_at = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    db = _LifecycleDb()

    acknowledge_security_alert(
        db,
        alert=alert,
        manager_id=first_manager_id,
        acknowledged_at=acknowledged_at,
        expected_version=1,
    )

    with pytest.raises(ValueError, match="stale_state"):
        acknowledge_security_alert(
            db,
            alert=alert,
            manager_id=second_manager_id,
            acknowledged_at=acknowledged_at,
            expected_version=1,
        )

    assert alert.status == "acknowledged"
    assert alert.lifecycle_version == 2
    assert alert.acknowledged_by == first_manager_id
    assert len(db.added) == 1


def test_occurrence_link_and_acknowledge_do_not_lose_each_others_updates():
    from apps.shared.services.security_alert_evidence import (
        link_security_alert_evidence,
    )
    from apps.shared.services.security_alert_lifecycle import (
        acknowledge_security_alert,
    )

    manager_id = uuid4()
    audit_log_id = uuid4()
    alert = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        rule_id="repeated_permission_denied",
        rule_version="v1",
        severity="medium",
        status="open",
        lifecycle_version=1,
        occurrence_count=4,
        last_detected_at=datetime(2026, 7, 11, 11, 0, tzinfo=timezone.utc),
        acknowledged_by=None,
        acknowledged_at=None,
    )
    detected_at = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    db = _ConcurrentLifecycleDb()
    ready = Barrier(2)

    def link_occurrence():
        ready.wait(timeout=1)
        return link_security_alert_evidence(
            db,
            alert=alert,
            audit_log_id=audit_log_id,
            detected_at=detected_at,
        )

    def acknowledge():
        ready.wait(timeout=1)
        return acknowledge_security_alert(
            db,
            alert=alert,
            manager_id=manager_id,
            acknowledged_at=detected_at,
            expected_version=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        occurrence_future = executor.submit(link_occurrence)
        acknowledge_future = executor.submit(acknowledge)
        assert occurrence_future.result(timeout=1) is True
        assert acknowledge_future.result(timeout=1) is alert

    assert alert.occurrence_count == 5
    assert alert.last_detected_at == detected_at
    assert alert.status == "acknowledged"
    assert alert.lifecycle_version == 2
    assert alert.acknowledged_by == manager_id
    assert db.evidence_keys == {(alert.id, audit_log_id)}
    assert [audit.action for audit in db.added] == [
        "security_alert.acknowledged"
    ]


def test_resolve_normalizes_and_redacts_reason_before_persisting_it():
    from apps.shared.services.security_alert_lifecycle import resolve_security_alert

    manager_id = uuid4()
    resolved_at = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    alert = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        rule_id="repeated_policy_block",
        rule_version="v1",
        severity="high",
        status="open",
        lifecycle_version=2,
        resolution_type=None,
        resolution_reason=None,
        resolved_by=None,
        resolved_at=None,
    )
    sanitizer = _ReasonSanitizer()
    db = _LifecycleDb()

    result = resolve_security_alert(
        db,
        alert=alert,
        manager_id=manager_id,
        resolved_at=resolved_at,
        expected_version=2,
        resolution_type="mitigated",
        reason="  대응\r\n완료 user@example.com  ",
        reason_sanitizer=sanitizer,
    )

    assert result is alert
    assert sanitizer.inputs == ["대응\n완료 user@example.com"]
    assert alert.status == "resolved"
    assert alert.lifecycle_version == 3
    assert alert.resolution_type == "mitigated"
    assert alert.resolution_reason == "대응\n완료 [REDACTED]"
    assert alert.resolved_by == manager_id
    assert alert.resolved_at == resolved_at
    assert len(db.added) == 1

    audit = db.added[0]
    assert audit.action == "security_alert.resolved"
    assert audit.audit_metadata["resolution_type"] == "mitigated"
    assert audit.audit_metadata["resolution_reason"] == "대응\n완료 [REDACTED]"


def test_resolve_keeps_alert_unchanged_when_reason_sanitizer_fails():
    from apps.shared.services.security_alert_lifecycle import resolve_security_alert

    alert = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        rule_id="repeated_policy_block",
        rule_version="v1",
        severity="high",
        status="open",
        lifecycle_version=2,
        resolution_type=None,
        resolution_reason=None,
        resolved_by=None,
        resolved_at=None,
    )
    db = _LifecycleDb()

    with pytest.raises(RuntimeError, match="redaction failed"):
        resolve_security_alert(
            db,
            alert=alert,
            manager_id=uuid4(),
            resolved_at=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
            expected_version=2,
            resolution_type="mitigated",
            reason="secret-value",
            reason_sanitizer=_FailingReasonSanitizer(),
        )

    assert alert.status == "open"
    assert alert.lifecycle_version == 2
    assert alert.resolution_type is None
    assert alert.resolution_reason is None
    assert alert.resolved_by is None
    assert alert.resolved_at is None
    assert db.added == []


def test_resolve_rolls_back_when_canonical_audit_add_fails():
    from apps.shared.services.security_alert_lifecycle import resolve_security_alert

    alert = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        rule_id="repeated_policy_block",
        rule_version="v1",
        severity="high",
        status="acknowledged",
        lifecycle_version=5,
        resolution_type=None,
        resolution_reason=None,
        resolved_by=None,
        resolved_at=None,
    )
    db = _FailingResolveAuditDb(alert)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        resolve_security_alert(
            db,
            alert=alert,
            manager_id=uuid4(),
            resolved_at=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
            expected_version=5,
            resolution_type="false_positive",
            reason="오탐 확인",
            reason_sanitizer=_ReasonSanitizer(),
        )

    assert db.commits == 0
    assert db.rollbacks == 1
    assert alert.status == "acknowledged"
    assert alert.lifecycle_version == 5
    assert alert.resolution_type is None
    assert alert.resolution_reason is None
    assert alert.resolved_by is None
    assert alert.resolved_at is None


def test_reopen_clears_acknowledgment_and_adds_canonical_audit():
    from apps.shared.services.security_alert_lifecycle import reopen_security_alert

    manager_id = uuid4()
    previous_manager_id = uuid4()
    reopened_at = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    alert = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        rule_id="repeated_permission_denied",
        rule_version="v1",
        severity="medium",
        status="acknowledged",
        lifecycle_version=3,
        acknowledged_by=previous_manager_id,
        acknowledged_at=datetime(2026, 7, 12, 11, 0, tzinfo=timezone.utc),
    )
    db = _LifecycleDb()

    result = reopen_security_alert(
        db,
        alert=alert,
        manager_id=manager_id,
        reopened_at=reopened_at,
        expected_version=3,
    )

    assert result is alert
    assert alert.status == "open"
    assert alert.lifecycle_version == 4
    assert alert.acknowledged_by is None
    assert alert.acknowledged_at is None
    assert db.commits == 0
    assert len(db.added) == 1

    audit = db.added[0]
    assert audit.action == "security_alert.reopened"
    assert audit.actor_id == manager_id
    assert audit.occurred_at == reopened_at
    assert audit.target_type == "security_alert"
    assert audit.target_id == str(alert.id)


class _LifecycleDb:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, row):
        self.added.append(row)


class _FailingLifecycleDb(_LifecycleDb):
    def __init__(self, alert):
        super().__init__()
        self.alert = alert
        self.rollbacks = 0

    def add(self, _row):
        raise RuntimeError("audit unavailable")

    def rollback(self):
        self.rollbacks += 1
        self.alert.status = "open"
        self.alert.lifecycle_version = 4
        self.alert.acknowledged_by = None
        self.alert.acknowledged_at = None


class _ConcurrentLifecycleDb(_LifecycleDb):
    def __init__(self):
        super().__init__()
        self.evidence_keys = set()
        self._lock = Lock()

    def add_evidence_once(self, *, alert_id, audit_log_id):
        key = (alert_id, audit_log_id)
        with self._lock:
            if key in self.evidence_keys:
                return False
            self.evidence_keys.add(key)
            return True


class _ReasonSanitizer:
    def __init__(self):
        self.inputs = []

    def sanitize(self, reason):
        self.inputs.append(reason)
        return reason.replace("user@example.com", "[REDACTED]")


class _FailingReasonSanitizer:
    def sanitize(self, _reason):
        raise RuntimeError("redaction failed")


class _FailingResolveAuditDb(_LifecycleDb):
    def __init__(self, alert):
        super().__init__()
        self.alert = alert
        self.rollbacks = 0

    def add(self, _row):
        raise RuntimeError("audit unavailable")

    def rollback(self):
        self.rollbacks += 1
        self.alert.status = "acknowledged"
        self.alert.lifecycle_version = 5
        self.alert.resolution_type = None
        self.alert.resolution_reason = None
        self.alert.resolved_by = None
        self.alert.resolved_at = None
