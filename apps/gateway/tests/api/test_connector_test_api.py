import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import connectors as connector_endpoint
from apps.gateway.application.connectors.errors import (
    ConnectorTestAdmissionUnavailable,
    ConnectorTestBusy,
    ConnectorTestRateLimited,
)
from apps.gateway.application.connectors.models import ConnectorTestResult
from apps.gateway.main import app
from apps.gateway.services.organization_context import (
    resolve_active_organization_id as real_resolve_active_organization_id,
)
from apps.shared.db.models.user import User


class FakeUseCase:
    def __init__(
        self,
        result: ConnectorTestResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or ConnectorTestResult(True, "safe-success")
        self.error = error
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    user = User(
        id=uuid4(),
        email="connector-test@example.com",
        name="Connector Tester",
        social_provider="local",
    )
    organization_id = uuid4()
    fake_db = object()
    use_case = FakeUseCase()

    app.dependency_overrides[connector_endpoint.get_db] = lambda: fake_db
    app.dependency_overrides[connector_endpoint.get_current_user] = lambda: user
    monkeypatch.setattr(
        connector_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, user_id: organization_id,
    )
    monkeypatch.setattr(
        connector_endpoint,
        "get_connector_test_application",
        lambda: SimpleNamespace(use_case=use_case),
    )
    monkeypatch.setattr(
        connector_endpoint,
        "login_network_resolver",
        lambda: SimpleNamespace(resolve=lambda _request: "203.0.113.0/24"),
    )
    try:
        yield TestClient(app, raise_server_exceptions=False), use_case, user, organization_id
    finally:
        app.dependency_overrides = {}


def payload(**overrides) -> dict:
    value = {
        "connection_name": "public-db",
        "type": "postgres",
        "host": "db.example.com",
        "port": 5432,
        "database": "app",
        "username": "app-user",
        "password": "placeholder-secret",
        "ssh": None,
    }
    value.update(overrides)
    return value


def test_endpoint_declares_authentication_and_organization_dependencies() -> None:
    parameters = inspect.signature(connector_endpoint.test_db_connection).parameters

    assert parameters["current_user"].default.dependency is connector_endpoint.get_current_user
    assert parameters["raw_organization_id"].default.alias == "X-Organization-Id"


def test_openapi_keeps_strict_request_body_contract() -> None:
    operation = app.openapi()["paths"]["/api/v1/connectors/test"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert operation["requestBody"]["required"] is True
    assert schema["properties"]["type"]["const"] == "postgres"
    assert schema["properties"]["port"] == {
        "default": 5432,
        "maximum": 65535,
        "minimum": 1,
        "title": "Port",
        "type": "integer",
    }
    assert schema["additionalProperties"] is False


def test_valid_request_builds_command_without_ssh_credentials(client) -> None:
    http, use_case, user, organization_id = client

    response = http.post(
        "/api/v1/connectors/test",
        headers={"X-Organization-Id": str(organization_id)},
        json=payload(port=55432),
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "safe-success",
        "reason_code": None,
    }
    command = use_case.commands[0]
    assert command.actor_id == user.id
    assert command.organization_id == organization_id
    assert command.port == 55432
    assert command.password == "placeholder-secret"
    assert command.network_address == "203.0.113.0/24"


def test_unknown_transport_identity_fails_closed_before_body_read(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, use_case, _, organization_id = client
    monkeypatch.setattr(
        connector_endpoint,
        "login_network_resolver",
        lambda: SimpleNamespace(resolve=lambda _request: "unknown"),
    )

    response = http.post(
        "/api/v1/connectors/test",
        headers={
            "X-Organization-Id": str(organization_id),
            "Content-Type": "application/json",
        },
        content=b"not-json",
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "connector.admission_unavailable"
    assert use_case.commands == []


@pytest.mark.parametrize(
    "invalid_payload",
    [
        payload(type="mysql"),
        payload(port=0),
        payload(port=65536),
        payload(extra="not-allowed"),
        payload(password=""),
        payload(connection_name=" \t "),
    ],
)
def test_strict_schema_rejects_before_use_case(client, invalid_payload: dict) -> None:
    http, use_case, _, organization_id = client

    response = http.post(
        "/api/v1/connectors/test",
        headers={"X-Organization-Id": str(organization_id)},
        json=invalid_payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation.failed"
    assert use_case.commands == []
    assert "placeholder-secret" not in response.text


@pytest.mark.parametrize("connection_name", ["", " \t ", "x" * 101])
def test_create_rejects_invalid_connection_name_before_handler(
    client,
    monkeypatch: pytest.MonkeyPatch,
    connection_name: str,
) -> None:
    http, _, _, _ = client
    monkeypatch.setattr(
        connector_endpoint,
        "_build_workflow_connector",
        lambda *_args: pytest.fail("create handler must not build a connector"),
    )

    response = http.post(
        "/api/v1/connectors",
        json=payload(connection_name=connection_name),
    )

    assert response.status_code == 422
    assert "placeholder-secret" not in response.text


@pytest.mark.parametrize(
    ("content", "headers", "query", "status_code", "code"),
    [
        (b"[]", {"Content-Type": "application/json"}, "", 400, "connector.test_payload_invalid"),
        (b"{}", {"Content-Type": "text/plain"}, "", 415, "connector.test_media_type_not_supported"),
        (b"{}", {"Content-Type": "application/json", "Content-Encoding": "gzip"}, "", 415, "connector.test_media_type_not_supported"),
        (b"{}", {"Content-Type": "application/json"}, "?token=value", 400, "connector.test_payload_invalid"),
        (b"x" * (32 * 1024 + 1), {"Content-Type": "application/json"}, "", 413, "connector.test_payload_too_large"),
    ],
    ids=["root", "media-type", "encoding", "query", "size"],
)
def test_ingress_failures_stop_before_use_case(
    client,
    content: bytes,
    headers: dict[str, str],
    query: str,
    status_code: int,
    code: str,
) -> None:
    http, use_case, _, organization_id = client
    headers["X-Organization-Id"] = str(organization_id)

    response = http.post(
        f"/api/v1/connectors/test{query}",
        headers=headers,
        content=content,
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert use_case.commands == []


@pytest.mark.parametrize(
    ("error", "status_code", "code", "retry_after"),
    [
        (ConnectorTestRateLimited(14), 429, "connector.test_rate_limited", "14"),
        (ConnectorTestBusy(3), 429, "connector.test_busy", "3"),
        (ConnectorTestAdmissionUnavailable(), 503, "connector.admission_unavailable", None),
    ],
)
def test_admission_errors_use_safe_http_contract(
    client,
    error: Exception,
    status_code: int,
    code: str,
    retry_after: str | None,
) -> None:
    http, use_case, _, organization_id = client
    use_case.error = error

    response = http.post(
        "/api/v1/connectors/test",
        headers={"X-Organization-Id": str(organization_id)},
        json=payload(),
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert response.headers.get("Retry-After") == retry_after
    assert "placeholder-secret" not in response.text


def test_retry_after_is_exposed_to_allowed_browser_origin(client) -> None:
    http, use_case, _, organization_id = client
    use_case.error = ConnectorTestBusy(3)

    response = http.post(
        "/api/v1/connectors/test",
        headers={
            "X-Organization-Id": str(organization_id),
            "Origin": "http://localhost:3000",
        },
        json=payload(),
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3"
    exposed_headers = response.headers["Access-Control-Expose-Headers"]
    assert "retry-after" in exposed_headers.lower()


def test_organization_scope_is_checked_before_body_is_read(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, use_case, _, organization_id = client

    def reject_scope(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="resource.not_found")

    monkeypatch.setattr(
        connector_endpoint,
        "resolve_active_organization_id",
        reject_scope,
    )
    response = http.post(
        "/api/v1/connectors/test",
        headers={
            "X-Organization-Id": str(organization_id),
            "Content-Type": "application/json",
        },
        content=b"not-json",
    )

    assert response.status_code == 404
    assert use_case.commands == []


@pytest.mark.parametrize(
    ("organization_header", "status_code", "code"),
    [
        (None, 400, "organization.required"),
        ("not-a-uuid", 422, "validation.failed"),
    ],
)
def test_organization_header_is_validated_before_body(
    client,
    monkeypatch: pytest.MonkeyPatch,
    organization_header: str | None,
    status_code: int,
    code: str,
) -> None:
    http, use_case, _, _ = client
    monkeypatch.setattr(
        connector_endpoint,
        "resolve_active_organization_id",
        real_resolve_active_organization_id,
    )
    headers = {"Content-Type": "application/json"}
    if organization_header is not None:
        headers["X-Organization-Id"] = organization_header

    response = http.post(
        "/api/v1/connectors/test",
        headers=headers,
        content=b"not-json",
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert use_case.commands == []
