import unittest
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import admin as admin_endpoint
from apps.gateway.main import app
from apps.gateway.utils.api_errors import raise_api_error
from apps.shared.services.security_alert_lifecycle import (
    SecurityAlertStaleStateError,
)


class TestSecurityAlertAdminRoutesRegistered(unittest.TestCase):
    """MBA-213 관리자 API의 공개 경로 계약을 먼저 고정한다."""

    def setUp(self):
        self.client = TestClient(app)
        self.alert_id = uuid4()

    def tearDown(self):
        app.dependency_overrides = {}

    def assert_route_requires_authentication(self, method: str, path: str, **kwargs):
        response = self.client.request(method, path, **kwargs)

        # Route가 등록되어 있으면 미인증 요청은 404/405가 아니라 401로 끝난다.
        self.assertEqual(response.status_code, 401, response.text)

    def test_list_route_is_registered(self):
        self.assert_route_requires_authentication(
            "GET", "/api/v1/admin/security-alerts"
        )

    def test_summary_route_is_registered_before_alert_id_route(self):
        self.assert_route_requires_authentication(
            "GET", "/api/v1/admin/security-alerts/summary"
        )

    def test_detail_route_is_registered(self):
        self.assert_route_requires_authentication(
            "GET", f"/api/v1/admin/security-alerts/{self.alert_id}"
        )

    def test_audit_logs_route_is_registered(self):
        self.assert_route_requires_authentication(
            "GET", f"/api/v1/admin/security-alerts/{self.alert_id}/audit-logs"
        )

    def test_acknowledge_route_is_registered(self):
        self.assert_route_requires_authentication(
            "POST",
            f"/api/v1/admin/security-alerts/{self.alert_id}/acknowledge",
            json={"expected_version": 1},
        )

    def test_resolve_route_is_registered(self):
        self.assert_route_requires_authentication(
            "POST",
            f"/api/v1/admin/security-alerts/{self.alert_id}/resolve",
            json={
                "expected_version": 1,
                "resolution_type": "mitigated",
                "reason": "필요한 대응을 완료했습니다.",
            },
        )

    def test_reopen_route_is_registered(self):
        self.assert_route_requires_authentication(
            "POST",
            f"/api/v1/admin/security-alerts/{self.alert_id}/reopen",
            json={"expected_version": 1},
        )


@pytest.fixture
def authorized_client(monkeypatch):
    organization_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[admin_endpoint.get_db] = lambda: object()
    app.dependency_overrides[admin_endpoint.get_current_user] = lambda: SimpleNamespace(
        id=user_id
    )
    monkeypatch.setattr(
        admin_endpoint,
        "resolve_security_alert_manager_organization",
        lambda *args: organization_id,
    )
    try:
        yield TestClient(app), organization_id
    finally:
        app.dependency_overrides = {}


def _assert_safe_error(response, status_code, code):
    assert response.status_code == status_code
    body = response.json()
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert "request_id" in body["error"]
    assert "traceback" not in str(body).lower()


def test_missing_and_invalid_organization_header_use_safe_errors(monkeypatch):
    user_id = uuid4()
    app.dependency_overrides[admin_endpoint.get_db] = lambda: object()
    app.dependency_overrides[admin_endpoint.get_current_user] = lambda: SimpleNamespace(
        id=user_id
    )
    client = TestClient(app)
    try:
        missing = client.get("/api/v1/admin/security-alerts")
        invalid = client.get(
            "/api/v1/admin/security-alerts",
            headers={"X-Organization-Id": "not-a-uuid"},
        )
    finally:
        app.dependency_overrides = {}

    _assert_safe_error(missing, 400, "organization.required")
    _assert_safe_error(invalid, 422, "validation.failed")


def test_invalid_period_uses_safe_400(authorized_client):
    client, organization_id = authorized_client

    response = client.get(
        "/api/v1/admin/security-alerts",
        headers={"X-Organization-Id": str(organization_id)},
        params={"startAt": "2026-07-13T00:00:00Z", "endAt": "2026-07-13T00:00:00Z"},
    )

    _assert_safe_error(response, 400, "period.invalid")


def test_cross_organization_detail_uses_safe_404(authorized_client, monkeypatch):
    client, organization_id = authorized_client

    def hidden(*args, **kwargs):
        request = kwargs["request"]
        raise_api_error(
            request, 404, "resource.not_found", "Security alert not found."
        )

    monkeypatch.setattr(
        admin_endpoint.SecurityAlertService,
        "get_detail",
        staticmethod(hidden),
    )

    response = client.get(
        f"/api/v1/admin/security-alerts/{uuid4()}",
        headers={"X-Organization-Id": str(organization_id)},
    )

    _assert_safe_error(response, 404, "resource.not_found")
    assert "organization" not in response.json()["error"]["message"].lower()


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (SecurityAlertStaleStateError("stale_state"), 409, "stale_state"),
        (RuntimeError("database secret detail"), 500, "audit.persistence_failed"),
    ],
)
def test_mutation_failures_use_safe_error_contract(
    authorized_client, monkeypatch, failure, status_code, code
):
    client, organization_id = authorized_client

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(
        admin_endpoint.SecurityAlertService,
        "acknowledge",
        staticmethod(fail),
    )

    response = client.post(
        f"/api/v1/admin/security-alerts/{uuid4()}/acknowledge",
        headers={"X-Organization-Id": str(organization_id)},
        json={"expected_version": 1},
    )

    _assert_safe_error(response, status_code, code)
    assert "database secret detail" not in str(response.json())


def test_unknown_mutation_field_is_safe_422_before_service(
    authorized_client, monkeypatch
):
    client, organization_id = authorized_client
    called = False

    def must_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        admin_endpoint.SecurityAlertService,
        "acknowledge",
        staticmethod(must_not_run),
    )

    response = client.post(
        f"/api/v1/admin/security-alerts/{uuid4()}/acknowledge",
        headers={"X-Organization-Id": str(organization_id)},
        json={"expected_version": 1, "raw_secret": "must-not-echo"},
    )

    _assert_safe_error(response, 422, "validation.failed")
    assert called is False
    assert "must-not-echo" not in str(response.json())
