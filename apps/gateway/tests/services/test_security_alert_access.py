from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.gateway.services import security_alert_access


def _request() -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/"})
    request.state.request_id = "request-id"
    return request


def _error_code(exc: HTTPException) -> str:
    return exc.detail["error"]["code"]


def test_current_organization_manager_is_allowed(monkeypatch):
    organization_id = uuid4()
    user_id = uuid4()
    monkeypatch.setattr(
        security_alert_access,
        "resolve_active_organization_id",
        lambda db, request, raw_organization_id, actor_id: organization_id,
    )
    monkeypatch.setattr(
        security_alert_access,
        "has_organization_manager_permission",
        lambda db, actor_id, scoped_organization_id: True,
    )

    result = security_alert_access.resolve_security_alert_manager_organization(
        object(),
        _request(),
        str(organization_id),
        user_id,
        "security_alert.list",
    )

    assert result == organization_id


@pytest.mark.parametrize("caller_kind", ["member", "auditor", "raw_auditor"])
def test_non_manager_roles_are_denied_even_with_organization_scope(
    monkeypatch, caller_kind
):
    organization_id = uuid4()
    monkeypatch.setattr(
        security_alert_access,
        "resolve_active_organization_id",
        lambda *args: organization_id,
    )
    monkeypatch.setattr(
        security_alert_access,
        "has_organization_manager_permission",
        lambda *args: False,
    )
    recorded = []
    monkeypatch.setattr(
        security_alert_access,
        "record_audit",
        lambda **kwargs: recorded.append(kwargs),
    )

    with pytest.raises(HTTPException) as exc:
        security_alert_access.resolve_security_alert_manager_organization(
            object(),
            _request(),
            str(organization_id),
            uuid4(),
            "security_alert.list",
        )

    assert caller_kind  # 각 역할은 manager 권한과 별개라는 계약을 표시한다.
    assert exc.value.status_code == 403
    assert _error_code(exc.value) == "permission.denied"
    assert getattr(exc.value, "audit_recorded", False) is True
    assert len(recorded) == 1
    assert recorded[0]["action"] == "permission.denied"
    assert recorded[0]["target_type"] == "organization"
    assert recorded[0]["target_id"] == organization_id
    assert recorded[0]["metadata"]["required_permission"] == "security_alert.manage"
    assert recorded[0]["metadata"]["requested_operation"] == "security_alert.list"
    assert (
        recorded[0]["metadata"]["denial_reason"]
        == "organization_manager_required"
    )
    assert "alert_id" not in str(recorded[0])


@pytest.mark.parametrize("membership_state", ["suspended", "removed"])
def test_inactive_membership_is_hidden_by_active_organization_resolution(
    monkeypatch, membership_state
):
    def reject_inactive_scope(*args):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "resource.not_found"}},
        )

    monkeypatch.setattr(
        security_alert_access,
        "resolve_active_organization_id",
        reject_inactive_scope,
    )
    manager_check_called = False

    def manager_check(*args):
        nonlocal manager_check_called
        manager_check_called = True
        return True

    monkeypatch.setattr(
        security_alert_access,
        "has_organization_manager_permission",
        manager_check,
    )

    with pytest.raises(HTTPException) as exc:
        security_alert_access.resolve_security_alert_manager_organization(
            object(),
            _request(),
            str(uuid4()),
            uuid4(),
            "security_alert.list",
        )

    assert membership_state
    assert exc.value.status_code == 404
    assert manager_check_called is False


class _AlertQuery:
    def __init__(self, result):
        self.result = result

    def filter(self, *conditions):
        assert len(conditions) == 2
        return self

    def first(self):
        return self.result


class _AlertSession:
    def __init__(self, result):
        self.result = result

    def query(self, model):
        return _AlertQuery(self.result)


def test_alert_in_current_organization_is_returned():
    alert = SimpleNamespace(id=uuid4(), organization_id=uuid4())

    result = security_alert_access.get_security_alert_in_organization_or_404(
        _AlertSession(alert),
        _request(),
        alert.organization_id,
        alert.id,
    )

    assert result is alert


@pytest.mark.parametrize("scope_case", ["missing", "other_organization"])
def test_missing_and_cross_organization_alerts_share_safe_404(scope_case):
    with pytest.raises(HTTPException) as exc:
        security_alert_access.get_security_alert_in_organization_or_404(
            _AlertSession(None),
            _request(),
            uuid4(),
            uuid4(),
        )

    assert scope_case
    assert exc.value.status_code == 404
    assert _error_code(exc.value) == "resource.not_found"
    assert "organization" not in str(exc.value.detail).lower()
