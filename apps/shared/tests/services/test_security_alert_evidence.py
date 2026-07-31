from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Lock
from types import SimpleNamespace
from uuid import uuid4


def test_same_audit_evidence_increments_occurrence_only_once():
    from apps.shared.services.security_alert_evidence import (
        link_security_alert_evidence,
    )

    alert = SimpleNamespace(id=uuid4(), occurrence_count=0, last_detected_at=None)
    audit_log_id = uuid4()
    detected_at = datetime(2026, 7, 11, tzinfo=timezone.utc)
    db = _EvidenceDb()

    first_linked = link_security_alert_evidence(
        db,
        alert=alert,
        audit_log_id=audit_log_id,
        detected_at=detected_at,
    )
    duplicate_linked = link_security_alert_evidence(
        db,
        alert=alert,
        audit_log_id=audit_log_id,
        detected_at=detected_at,
    )

    assert first_linked is True
    assert duplicate_linked is False
    assert db.evidence_keys == {(alert.id, audit_log_id)}
    assert alert.occurrence_count == 1
    assert alert.last_detected_at == detected_at


def test_cross_organization_audit_is_not_linked_as_evidence():
    from apps.shared.services.security_alert_evidence import (
        link_security_alert_evidence,
    )

    alert_organization_id = uuid4()
    audit_organization_id = uuid4()
    alert = SimpleNamespace(
        id=uuid4(),
        organization_id=alert_organization_id,
        occurrence_count=3,
        last_detected_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    audit_log = SimpleNamespace(
        id=uuid4(),
        audit_metadata={"organization_id": str(audit_organization_id)},
    )
    original_audit_metadata = dict(audit_log.audit_metadata)
    db = _EvidenceDb()

    linked = link_security_alert_evidence(
        db,
        alert=alert,
        audit_log=audit_log,
        detected_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert linked is False
    assert db.evidence_keys == set()
    assert alert.occurrence_count == 3
    assert alert.last_detected_at == datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert audit_log.audit_metadata == original_audit_metadata


def test_resolved_alert_does_not_accept_new_evidence():
    from apps.shared.services.security_alert_evidence import (
        link_security_alert_evidence,
    )

    original_detected_at = datetime(2026, 7, 10, tzinfo=timezone.utc)
    alert = SimpleNamespace(
        id=uuid4(),
        status="resolved",
        occurrence_count=3,
        last_detected_at=original_detected_at,
    )
    db = _EvidenceDb()

    linked = link_security_alert_evidence(
        db,
        alert=alert,
        audit_log_id=uuid4(),
        detected_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert linked is False
    assert db.evidence_keys == set()
    assert alert.occurrence_count == 3
    assert alert.last_detected_at == original_detected_at


def test_concurrent_same_audit_link_is_counted_once_without_deadlock():
    from apps.shared.services.security_alert_evidence import (
        link_security_alert_evidence,
    )

    alert = SimpleNamespace(id=uuid4(), occurrence_count=0, last_detected_at=None)
    audit_log_id = uuid4()
    detected_at = datetime(2026, 7, 11, tzinfo=timezone.utc)
    db = _ConcurrentEvidenceDb()

    def link_once():
        return link_security_alert_evidence(
            db,
            alert=alert,
            audit_log_id=audit_log_id,
            detected_at=detected_at,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: link_once(), range(2)))

    assert sorted(results) == [False, True]
    assert db.evidence_keys == {(alert.id, audit_log_id)}
    assert alert.occurrence_count == 1
    assert alert.last_detected_at == detected_at


class _EvidenceDb:
    def __init__(self):
        self.evidence_keys = set()

    def add_evidence_once(self, *, alert_id, audit_log_id):
        key = (alert_id, audit_log_id)
        if key in self.evidence_keys:
            return False
        self.evidence_keys.add(key)
        return True


class _ConcurrentEvidenceDb(_EvidenceDb):
    def __init__(self):
        super().__init__()
        self._ready = Barrier(2)
        self._lock = Lock()

    def add_evidence_once(self, *, alert_id, audit_log_id):
        self._ready.wait(timeout=1)
        with self._lock:
            return super().add_evidence_once(
                alert_id=alert_id,
                audit_log_id=audit_log_id,
            )
