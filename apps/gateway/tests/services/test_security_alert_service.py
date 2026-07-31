from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.gateway.services import security_alert_service as module
from apps.gateway.services.security_alert_service import (
    SecurityAlertFilters,
    SecurityAlertService,
)
from apps.shared.schemas.security_alert import (
    SecurityAlertDetail,
    SecurityAlertSafeActor,
)
from apps.shared.db.models.audit_log import ActorType, AuditCategory, AuditStatus


def _request() -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/"})
    request.state.request_id = "request-id"
    return request


def _alert(**overrides):
    now = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
    values = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "subject_actor_id": uuid4(),
        "rule_id": "repeated_permission_denied",
        "rule_version": "v1",
        "severity": "medium",
        "status": "open",
        "policy_reason": None,
        "occurrence_count": 5,
        "first_detected_at": now,
        "last_detected_at": now,
        "lifecycle_version": 1,
        "acknowledged_by": None,
        "acknowledged_at": None,
        "resolution_type": None,
        "resolution_reason": None,
        "resolved_by": None,
        "resolved_at": None,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_period_allows_one_sided_bounds_and_interprets_naive_time_as_kst():
    period = SecurityAlertService.resolve_period(
        _request(),
        datetime(2026, 7, 1, 9, 0),
        None,
    )

    assert period.start_at == datetime(
        2026, 7, 1, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")
    )
    assert period.end_at is None


def test_period_rejects_empty_or_reversed_half_open_range():
    at = datetime(2026, 7, 1, tzinfo=timezone.utc)

    with pytest.raises(HTTPException) as exc:
        SecurityAlertService.resolve_period(_request(), at, at)

    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "period.invalid"


class _ListQuery:
    def __init__(self, alerts):
        self.alerts = alerts
        self.offset_value = None
        self.limit_value = None
        self.order_columns = ()

    def count(self):
        return len(self.alerts)

    def order_by(self, *columns):
        self.order_columns = columns
        return self

    def offset(self, value):
        self.offset_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        start = self.offset_value or 0
        end = start + self.limit_value if self.limit_value is not None else None
        return self.alerts[start:end]


def test_list_returns_filtered_total_and_uses_stable_pagination(monkeypatch):
    organization_id = uuid4()
    alerts = [
        _alert(organization_id=organization_id, last_detected_at=datetime(2026, 7, day, tzinfo=timezone.utc))
        for day in (3, 2, 1)
    ]
    query = _ListQuery(alerts)
    captured = {}

    def filtered_query(db, scoped_organization_id, filters):
        captured["organization_id"] = scoped_organization_id
        captured["filters"] = filters
        return query

    monkeypatch.setattr(module, "_filtered_alert_query", filtered_query)
    monkeypatch.setattr(
        module,
        "_safe_actors_by_id",
        lambda db, org_id, actor_ids: {
            actor_id: SecurityAlertSafeActor(
                id=actor_id, display_name="사용자", state="active"
            )
            for actor_id in actor_ids
        },
    )
    filters = SecurityAlertFilters(severity="medium", status="open")

    result = SecurityAlertService.list_alerts(
        object(),
        organization_id=organization_id,
        filters=filters,
        page=2,
        limit=2,
    )

    assert result.total == 3
    assert [item.id for item in result.items] == [alerts[2].id]
    assert query.offset_value == 2
    assert query.limit_value == 2
    assert len(query.order_columns) == 2
    assert captured == {"organization_id": organization_id, "filters": filters}


def test_actor_projection_hides_email_and_removed_name(monkeypatch):
    active_id = uuid4()
    removed_id = uuid4()
    users = [
        SimpleNamespace(id=active_id, name="활성 사용자", email="active@example.com", deactivated_at=None),
        SimpleNamespace(id=removed_id, name="탈퇴 사용자", email="removed@example.com", deactivated_at=None),
    ]
    memberships = [
        SimpleNamespace(user_id=active_id, membership_state="active"),
        SimpleNamespace(user_id=removed_id, membership_state="removed"),
    ]

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args):
            return self

        def all(self):
            return self.rows

    class Db:
        def query(self, model):
            return Query(users if model is module.User else memberships)

    actors = module._safe_actors_by_id(
        Db(), uuid4(), {active_id, removed_id, uuid4()}
    )

    assert actors[active_id].model_dump() == {
        "id": active_id,
        "display_name": "활성 사용자",
        "state": "active",
    }
    assert actors[removed_id].display_name is None
    assert actors[removed_id].state == "removed"
    assert "email" not in actors[active_id].model_dump()


def test_detail_keeps_lifecycle_time_when_handler_user_was_deleted(monkeypatch):
    resolved_at = datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)
    alert = _alert(
        status="resolved",
        lifecycle_version=2,
        resolution_type="mitigated",
        resolution_reason="대응 완료",
        resolved_by=None,
        resolved_at=resolved_at,
    )
    monkeypatch.setattr(
        module, "get_security_alert_in_organization_or_404", lambda *args: alert
    )
    monkeypatch.setattr(
        module,
        "_safe_actors_by_id",
        lambda db, org_id, actor_ids: {
            alert.subject_actor_id: SecurityAlertSafeActor(
                id=alert.subject_actor_id, display_name=None, state="deleted"
            )
        },
    )
    monkeypatch.setattr(module, "_evidence_count", lambda db, alert_id: 3)

    detail = SecurityAlertService.get_detail(
        object(),
        request=_request(),
        organization_id=alert.organization_id,
        alert_id=alert.id,
    )

    assert detail.evidence_count == 3
    assert detail.resolution is not None
    assert detail.resolution.at == resolved_at
    assert detail.resolution.by.state == "deleted"
    assert detail.resolution.by.id is None


def test_summary_counts_only_open_alerts_and_limits_recent_items(monkeypatch):
    organization_id = uuid4()
    alerts = [
        _alert(
            organization_id=organization_id,
            severity="high" if index < 2 else "medium",
            last_detected_at=datetime(2026, 7, 13, index, tzinfo=timezone.utc),
        )
        for index in range(6)
    ]

    class HighQuery:
        def count(self):
            return 2

    class OpenQuery:
        def __init__(self):
            self.limit_value = None

        def filter(self, *args):
            return HighQuery()

        def count(self):
            return 6

        def order_by(self, *args):
            return self

        def limit(self, value):
            self.limit_value = value
            return self

        def all(self):
            return alerts[: self.limit_value]

    open_query = OpenQuery()

    class Db:
        def query(self, model):
            return self

        def filter(self, *args):
            # 최초 두 조건은 organization + status=open이다.
            assert len(args) == 2
            return open_query

    monkeypatch.setattr(
        module,
        "_safe_actors_by_id",
        lambda db, org_id, actor_ids: {
            actor_id: SecurityAlertSafeActor(
                id=actor_id, display_name="사용자", state="active"
            )
            for actor_id in actor_ids
        },
    )

    summary = SecurityAlertService.get_summary(
        Db(), organization_id=organization_id
    )

    assert summary.open_count == 6
    assert summary.high_open_count == 2
    assert len(summary.recent_items) == 5
    assert open_query.limit_value == 5


def _audit(organization_id, **overrides):
    values = {
        "id": uuid4(),
        "occurred_at": datetime(2026, 7, 13, tzinfo=timezone.utc),
        "actor_id": uuid4(),
        "actor_type": ActorType.USER,
        "category": AuditCategory.ACTION,
        "action": "permission.denied",
        "target_type": "workflow",
        "target_id": str(uuid4()),
        "status": AuditStatus.FAILURE,
        "request_id": "raw-column-must-not-be-used",
        "audit_metadata": {
            "organization_id": str(organization_id),
            "request_id": "safe-request-id",
            "required_permission": "security_alert.manage",
            "requested_operation": "security_alert.list",
            "denial_reason": "organization_manager_required",
            "email": "secret@example.com",
            "token": "not-a-real-token",
        },
        "before": {"password": "before-secret"},
        "after": {"password": "after-secret"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _EvidenceQuery:
    def __init__(self, audits):
        self.audits = audits
        self.join_called = False
        self.offset_value = 0
        self.limit_value = None

    def join(self, *args):
        self.join_called = True
        return self

    def filter(self, *args):
        return self

    def count(self):
        return len(self.audits)

    def order_by(self, *args):
        assert len(args) == 2
        return self

    def offset(self, value):
        self.offset_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        end = self.offset_value + self.limit_value
        return self.audits[self.offset_value:end]


def test_evidence_returns_only_linked_safe_projection(monkeypatch):
    organization_id = uuid4()
    alert = _alert(organization_id=organization_id)
    audit = _audit(organization_id)
    query = _EvidenceQuery([audit])

    class Db:
        def query(self, model):
            assert model is module.AuditLog
            return query

    monkeypatch.setattr(
        module,
        "get_security_alert_in_organization_or_404",
        lambda db, request, scoped_organization_id, alert_id: alert,
    )

    result = SecurityAlertService.list_evidence(
        Db(),
        request=_request(),
        organization_id=organization_id,
        alert_id=alert.id,
        page=1,
        limit=20,
    )

    assert result.total == 1
    assert query.join_called is True
    serialized = result.items[0].model_dump(mode="json")
    assert serialized["target_type"] == "workflow"
    assert serialized["target_id"] == audit.target_id
    assert serialized["request_id"] == "safe-request-id"
    assert serialized["required_permission"] == "security_alert.manage"
    assert serialized["requested_operation"] == "security_alert.list"
    assert serialized["denial_reason"] == "organization_manager_required"
    assert "audit_metadata" not in serialized
    assert "before" not in serialized
    assert "after" not in serialized
    assert "email" not in str(serialized)
    assert "token" not in str(serialized)


@pytest.mark.parametrize(
    "audit_overrides",
    [
        {"action": "policy.block"},
        {"target_type": None},
        {"target_id": None},
        {"audit_metadata": {"organization_id": str(uuid4())}},
        {"audit_metadata": None},
    ],
)
def test_evidence_hides_target_without_safe_projection_contract(audit_overrides):
    organization_id = uuid4()
    item = module._safe_audit_item(
        _audit(organization_id, **audit_overrides), organization_id
    )

    assert item.target_type is None
    assert item.target_id is None


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _LifecycleSession:
    def __init__(self, alert, *, fail_commit=False):
        self.alert = alert
        self.fail_commit = fail_commit
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement):
        return _ScalarResult(self.alert.id)

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self):
        self.rollbacks += 1


def _prepare_lifecycle_service(monkeypatch, alert):
    monkeypatch.setattr(
        module,
        "get_security_alert_in_organization_or_404",
        lambda db, request, organization_id, alert_id: alert,
    )
    monkeypatch.setattr(
        SecurityAlertService,
        "get_detail",
        lambda db, request, organization_id, alert_id: SecurityAlertDetail.model_construct(
            id=alert.id,
            organization_id=alert.organization_id,
            status=alert.status,
            version=alert.lifecycle_version,
        ),
    )
    monkeypatch.setattr(
        module,
        "enqueue_security_alert_notification",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        module,
        "dispatch_security_alert_notification_outbox",
        lambda: None,
    )


def test_acknowledge_commits_transition_and_canonical_audit(monkeypatch):
    manager_id = uuid4()
    at = datetime(2026, 7, 13, 2, 0, tzinfo=timezone.utc)
    alert = _alert()
    db = _LifecycleSession(alert)
    _prepare_lifecycle_service(monkeypatch, alert)

    result = SecurityAlertService.acknowledge(
        db,
        request=_request(),
        organization_id=alert.organization_id,
        alert_id=alert.id,
        manager_id=manager_id,
        expected_version=1,
        now=at,
    )

    assert isinstance(result, SecurityAlertDetail)
    assert result.status == "acknowledged"
    assert result.version == 2
    assert alert.status == "acknowledged"
    assert alert.lifecycle_version == 2
    assert db.commits == 1
    assert db.rollbacks == 0
    assert [audit.action for audit in db.added] == ["security_alert.acknowledged"]


def test_lifecycle_notification_is_enqueued_before_commit_and_dispatched_after(
    monkeypatch,
):
    alert = _alert()
    db = _LifecycleSession(alert)
    events = []
    original_commit = db.commit

    def commit():
        original_commit()
        events.append("commit")

    db.commit = commit
    _prepare_lifecycle_service(monkeypatch, alert)
    monkeypatch.setattr(
        module,
        "enqueue_security_alert_notification",
        lambda db, *, scoped_organization_id, idempotency_key: events.append(
            ("enqueue", scoped_organization_id, idempotency_key)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "dispatch_security_alert_notification_outbox",
        lambda: events.append("dispatch"),
        raising=False,
    )

    SecurityAlertService.acknowledge(
        db,
        request=_request(),
        organization_id=alert.organization_id,
        alert_id=alert.id,
        manager_id=uuid4(),
        expected_version=1,
    )

    assert events == [
        (
            "enqueue",
            alert.organization_id,
            f"security-alert:{alert.id}:lifecycle:2",
        ),
        "commit",
        "dispatch",
    ]


def test_lifecycle_notification_uses_response_version_for_outbox_key(monkeypatch):
    alert = _alert(lifecycle_version=2)
    detail = SecurityAlertDetail.model_construct(version=2)
    db = _LifecycleSession(alert)
    events = []
    monkeypatch.setattr(
        SecurityAlertService,
        "get_detail",
        lambda db, request, organization_id, alert_id: detail,
    )
    monkeypatch.setattr(
        module,
        "enqueue_security_alert_notification",
        lambda db, *, scoped_organization_id, idempotency_key: events.append(
            (scoped_organization_id, idempotency_key)
        ),
    )
    monkeypatch.setattr(
        module,
        "dispatch_security_alert_notification_outbox",
        lambda: None,
    )

    result = module._detail_then_commit(
        db,
        request=_request(),
        organization_id=alert.organization_id,
        alert_id=alert.id,
    )

    assert result is detail
    assert events == [
        (
            alert.organization_id,
            f"security-alert:{alert.id}:lifecycle:2",
        )
    ]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_lifecycle_dispatch_failure_does_not_rollback_persisted_outbox(monkeypatch):
    alert = _alert()
    db = _LifecycleSession(alert)
    events = []
    _prepare_lifecycle_service(monkeypatch, alert)

    monkeypatch.setattr(
        module,
        "enqueue_security_alert_notification",
        lambda db, *, scoped_organization_id, idempotency_key: events.append(
            ("enqueue", scoped_organization_id)
        ),
        raising=False,
    )

    def fail_dispatch():
        events.append("dispatch")
        raise RuntimeError("notification unavailable")

    monkeypatch.setattr(
        module,
        "dispatch_security_alert_notification_outbox",
        fail_dispatch,
        raising=False,
    )

    SecurityAlertService.acknowledge(
        db,
        request=_request(),
        organization_id=alert.organization_id,
        alert_id=alert.id,
        manager_id=uuid4(),
        expected_version=1,
    )

    assert events == [("enqueue", alert.organization_id), "dispatch"]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_resolve_uses_sanitizer_and_commits_sanitized_reason(monkeypatch):
    alert = _alert()
    db = _LifecycleSession(alert)
    _prepare_lifecycle_service(monkeypatch, alert)

    class Sanitizer:
        def sanitize(self, reason):
            assert reason == "token=secret"
            return "token=[REDACTED]"

    SecurityAlertService.resolve(
        db,
        request=_request(),
        organization_id=alert.organization_id,
        alert_id=alert.id,
        manager_id=uuid4(),
        expected_version=1,
        resolution_type="mitigated",
        reason="token=secret",
        reason_sanitizer=Sanitizer(),
    )

    assert alert.status == "resolved"
    assert alert.resolution_reason == "token=[REDACTED]"
    assert db.commits == 1
    assert db.added[0].action == "security_alert.resolved"
    assert db.added[0].audit_metadata["resolution_reason"] == "token=[REDACTED]"


def test_reopen_commits_and_clears_current_acknowledgement(monkeypatch):
    manager_id = uuid4()
    alert = _alert(
        status="acknowledged",
        lifecycle_version=2,
        acknowledged_by=manager_id,
        acknowledged_at=datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc),
    )
    db = _LifecycleSession(alert)
    _prepare_lifecycle_service(monkeypatch, alert)

    SecurityAlertService.reopen(
        db,
        request=_request(),
        organization_id=alert.organization_id,
        alert_id=alert.id,
        manager_id=manager_id,
        expected_version=2,
    )

    assert alert.status == "open"
    assert alert.lifecycle_version == 3
    assert alert.acknowledged_by is None
    assert alert.acknowledged_at is None
    assert db.commits == 1
    assert db.added[0].action == "security_alert.reopened"


def test_stale_transition_does_not_commit_or_create_audit(monkeypatch):
    alert = _alert(lifecycle_version=2)
    db = _LifecycleSession(alert)
    _prepare_lifecycle_service(monkeypatch, alert)

    with pytest.raises(ValueError, match="stale_state"):
        SecurityAlertService.acknowledge(
            db,
            request=_request(),
            organization_id=alert.organization_id,
            alert_id=alert.id,
            manager_id=uuid4(),
            expected_version=1,
        )

    assert db.commits == 0
    assert db.added == []


def test_commit_failure_rolls_back_lifecycle_unit_of_work(monkeypatch):
    alert = _alert()
    db = _LifecycleSession(alert, fail_commit=True)
    _prepare_lifecycle_service(monkeypatch, alert)

    with pytest.raises(RuntimeError, match="commit failed"):
        SecurityAlertService.acknowledge(
            db,
            request=_request(),
            organization_id=alert.organization_id,
            alert_id=alert.id,
            manager_id=uuid4(),
            expected_version=1,
        )

    assert db.commits == 1
    assert db.rollbacks == 1


def test_detail_projection_failure_rolls_back_before_commit(monkeypatch):
    alert = _alert()
    db = _LifecycleSession(alert)
    monkeypatch.setattr(
        module,
        "get_security_alert_in_organization_or_404",
        lambda db, request, organization_id, alert_id: alert,
    )

    def fail_detail(*args, **kwargs):
        raise RuntimeError("detail projection failed")

    monkeypatch.setattr(SecurityAlertService, "get_detail", fail_detail)

    with pytest.raises(RuntimeError, match="detail projection failed"):
        SecurityAlertService.acknowledge(
            db,
            request=_request(),
            organization_id=alert.organization_id,
            alert_id=alert.id,
            manager_id=uuid4(),
            expected_version=1,
        )

    assert db.commits == 0
    assert db.rollbacks == 1
