from apps.gateway.adapters.authentication import audit as audit_adapter
from apps.gateway.adapters.authentication.audit import LoginAuditRecorder
from apps.shared.audit.actions import AuditAction


def test_success_audit_projects_only_safe_actor_snapshot(monkeypatch):
    events = []
    monkeypatch.setattr(audit_adapter, "record_audit", lambda **event: events.append(event))

    LoginAuditRecorder().record_success(
        request_id="request-1",
        policy_version="v1",
        actor_id="opaque-user-id",
        actor_name="Member",
    )

    assert events == [
        {
            "action": AuditAction.USER_LOGIN,
            "category": "action",
            "actor_id": "opaque-user-id",
            "actor_type": "user",
            "metadata": {
                "request_id": "request-1",
                "reason_code": "auth.login.succeeded",
                "limiter_policy_version": "v1",
                "actor": {"id": "opaque-user-id", "name": "Member"},
            },
        }
    ]


def test_failure_audit_filters_dimensions_and_omits_raw_identity(monkeypatch):
    events = []
    monkeypatch.setattr(audit_adapter, "record_audit", lambda **event: events.append(event))

    LoginAuditRecorder().record_failure(
        request_id=None,
        policy_version="v1",
        reason_code="auth.login.rate_limited",
        limited_dimensions=("network", "unsafe", "account"),
    )

    event = events[0]
    assert event["action"] == AuditAction.USER_LOGIN_FAILED
    assert event["actor_type"] == "system"
    assert event["status"] == "failure"
    assert event["metadata"] == {
        "reason_code": "auth.login.rate_limited",
        "limiter_policy_version": "v1",
        "limited_dimensions": ["account", "network"],
    }


def test_failure_audit_replaces_unapproved_reason_and_drops_unsafe_request_id(
    monkeypatch,
):
    events = []
    monkeypatch.setattr(
        audit_adapter,
        "record_audit",
        lambda **event: events.append(event),
    )

    LoginAuditRecorder().record_failure(
        request_id="unsafe\nrequest-id",
        policy_version="v1",
        reason_code="raw backend exception",
    )

    assert events[0]["metadata"] == {
        "reason_code": "auth.login.internal_error",
        "limiter_policy_version": "v1",
    }
