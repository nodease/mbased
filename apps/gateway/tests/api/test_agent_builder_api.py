import uuid
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import agent_builder as agent_builder_endpoint
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.shared.schemas.agent_builder import (
    AgentBuilderMessageResponse,
    AgentBuilderSessionResponse,
)


def test_agent_builder_session_uses_header_resolved_organization(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    captured = {}

    class FakeService:
        def create_or_restore_session(self, payload):
            captured["payload_has_organization_id"] = hasattr(payload, "organization_id")
            return AgentBuilderSessionResponse(
                session_id=uuid.uuid4(),
                app_id=payload.app_id,
                status="active",
            )

    class FakeComposition:
        def orchestration(self):
            return FakeService()

    def fake_compose(*, db, request, raw_organization_id, current_user):
        captured["user_id"] = current_user.id
        captured["organization_id"] = UUID(raw_organization_id)
        return FakeComposition()

    monkeypatch.setattr(agent_builder_endpoint, "compose_agent_builder", fake_compose)
    app.dependency_overrides[agent_builder_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            "/api/v1/agent-builder/sessions",
            json={
                "app_id": str(uuid.uuid4()),
                "organization_id": str(uuid.uuid4()),
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    assert captured["user_id"] == user_id
    assert captured["organization_id"] == organization_id
    assert captured["payload_has_organization_id"] is False


def test_agent_builder_message_contract_does_not_accept_body_organization(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    captured = {}

    class FakeService:
        def submit_message(self, session_id_arg, payload):
            captured["session_id"] = session_id_arg
            captured["payload_has_organization_id"] = hasattr(payload, "organization_id")
            captured["message"] = payload.message
            return AgentBuilderMessageResponse(
                request_id=uuid.uuid4(),
                status="validation_failed",
                warnings=["테스트"],
            )

    class FakeComposition:
        def orchestration(self, **kwargs):
            return FakeService()

    def fake_compose(*, db, request, raw_organization_id, current_user):
        captured["organization_id"] = UUID(raw_organization_id)
        return FakeComposition()

    monkeypatch.setattr(agent_builder_endpoint, "compose_agent_builder", fake_compose)
    app.dependency_overrides[agent_builder_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            f"/api/v1/agent-builder/sessions/{session_id}/messages",
            json={
                "message": "휴가 정책 기반 workflow를 만들어줘",
                "organization_id": str(uuid.uuid4()),
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    assert captured["organization_id"] == organization_id
    assert captured["session_id"] == session_id
    assert captured["payload_has_organization_id"] is False
    assert captured["message"] == "휴가 정책 기반 workflow를 만들어줘"


def test_agent_builder_model_options_use_active_organization(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    captured = {}
    expected = [
        {
            "provider_name": "openai",
            "options": [],
            "unavailable_reason": "no_authorized_model",
        }
    ]

    class FakeComposition:
        def model_options(self):
            return expected

    def fake_compose(*, db, request, raw_organization_id, current_user):
        captured["args"] = (current_user.id, UUID(raw_organization_id))
        return FakeComposition()

    monkeypatch.setattr(agent_builder_endpoint, "compose_agent_builder", fake_compose)
    app.dependency_overrides[agent_builder_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).get(
            "/api/v1/agent-builder/model-options",
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    assert response.json() == expected
    assert captured["args"] == (user_id, organization_id)


def test_agent_builder_message_passes_explicit_intent_model_selection(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    model_id = uuid.uuid4()
    captured = {}

    class FakeService:
        def submit_message(self, session_id_arg, payload):
            return AgentBuilderMessageResponse(
                request_id=uuid.uuid4(),
                status="validation_failed",
                warnings=["테스트"],
            )

    class FakeComposition:
        def orchestration(self, **kwargs):
            captured["extractor"] = kwargs
            return FakeService()

    monkeypatch.setattr(
        agent_builder_endpoint,
        "compose_agent_builder",
        lambda **kwargs: FakeComposition(),
    )
    app.dependency_overrides[agent_builder_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            f"/api/v1/agent-builder/sessions/{session_id}/messages",
            json={
                "message": "입력과 응답 노드를 만들어줘",
                "intent_model_selection": {
                    "credential_id": str(credential_id),
                    "model_id": str(model_id),
                },
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    assert captured["extractor"]["intent_credential_id"] == credential_id
    assert captured["extractor"]["intent_model_id"] == model_id


def test_agent_builder_message_rejects_raw_graph_payload_without_echo(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    called = {"submit": False}

    def forbidden_compose(**kwargs):
        called["submit"] = True
        raise AssertionError("raw graph payload must not reach composition")

    monkeypatch.setattr(agent_builder_endpoint, "compose_agent_builder", forbidden_compose)
    app.dependency_overrides[agent_builder_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            f"/api/v1/agent-builder/sessions/{session_id}/messages",
            json={
                "message": "create workflow",
                "client_graph_snapshot": {
                    "nodes": [{"data": {"api_key": "secret-token-123"}}]
                },
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 422
    assert called["submit"] is False
    assert "secret-token-123" not in response.text


def test_legacy_agent_builder_apply_route_is_removed_without_echo(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    called = {"apply": False}

    def forbidden_compose(**kwargs):
        called["apply"] = True
        raise AssertionError("removed route must not reach composition")

    monkeypatch.setattr(agent_builder_endpoint, "compose_agent_builder", forbidden_compose)
    app.dependency_overrides[agent_builder_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        response = TestClient(app).post(
            f"/api/v1/agent-builder/drafts/{draft_id}/apply",
            json={
                "action": "apply_and_save",
                "previewGraph": {
                    "nodes": [{"data": {"api_key": "secret-token-123"}}]
                },
            },
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 404
    assert called["apply"] is False
    assert "secret-token-123" not in response.text
