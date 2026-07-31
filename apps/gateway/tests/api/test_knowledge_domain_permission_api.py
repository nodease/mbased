import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from apps.gateway.api.v1.endpoints import knowledge as knowledge_endpoint
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.gateway.application.knowledge_administration.domain_permissions import (
    DomainPermissionMutationResult,
    DomainPermissionProjection,
    OrganizationManagerRequired,
)


def _request_context(monkeypatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    monkeypatch.setattr(
        knowledge_endpoint,
        "resolve_active_organization_id",
        lambda db, request, raw, current_user_id: organization_id,
    )
    app.dependency_overrides[knowledge_endpoint.get_db] = lambda: object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    return organization_id, user_id


def test_domain_capabilities_are_server_derived(monkeypatch):
    organization_id, _ = _request_context(monkeypatch)

    class FakeUseCase:
        def capabilities(self, actor_id, requested_organization_id):
            assert requested_organization_id == organization_id
            return {"catalog_manage", "permission_delegate"}, False

    monkeypatch.setattr(
        knowledge_endpoint,
        "build_knowledge_domain_permission_use_case",
        lambda db: FakeUseCase(),
    )
    try:
        response = TestClient(app).get(
            "/api/v1/knowledge/domain-capabilities",
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    body = response.json()
    assert body["actions"] == ["catalog_manage", "permission_delegate"]
    assert body["can_create_collection"] is True
    assert body["can_manage_domain_permissions"] is False
    assert body["can_change_public_visibility"] is False


def test_domain_permission_list_returns_safe_projection(monkeypatch):
    organization_id, _ = _request_context(monkeypatch)
    permission_id = uuid.uuid4()
    team_id = uuid.uuid4()

    class FakeUseCase:
        def list_permissions(self, actor_id, requested_organization_id):
            return [
                DomainPermissionProjection(
                    permission_id=permission_id,
                    subject_type="team",
                    subject_id=team_id,
                    subject_safe_label="Knowledge Team",
                    permission_action="catalog_manage",
                    assigned_at=datetime.now(timezone.utc),
                    expires_at=None,
                    is_expired=False,
                )
            ]

    monkeypatch.setattr(
        knowledge_endpoint,
        "build_knowledge_domain_permission_use_case",
        lambda db: FakeUseCase(),
    )
    try:
        response = TestClient(app).get(
            "/api/v1/knowledge/domain-permissions",
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200
    assert response.json()["permissions"][0]["permission_id"] == str(permission_id)
    assert "raw" not in str(response.json()).lower()


def test_team_domain_permission_put_uses_active_organization(monkeypatch):
    organization_id, user_id = _request_context(monkeypatch)
    team_id = uuid.uuid4()
    captured = {}

    class FakeUseCase:
        def grant(self, command):
            captured["command"] = command
            return DomainPermissionMutationResult("created", uuid.uuid4())

    monkeypatch.setattr(
        knowledge_endpoint,
        "build_knowledge_domain_permission_use_case",
        lambda db: FakeUseCase(),
    )
    try:
        response = TestClient(app).put(
            f"/api/v1/knowledge/domain-permissions/teams/{team_id}/catalog_manage",
            json={},
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 204
    command = captured["command"]
    assert command.actor_id == user_id
    assert command.organization_id == organization_id
    assert command.subject_id == team_id


def test_domain_permission_manager_denial_uses_safe_error(monkeypatch):
    organization_id, _ = _request_context(monkeypatch)

    class FakeUseCase:
        def list_permissions(self, actor_id, requested_organization_id):
            raise OrganizationManagerRequired()

    monkeypatch.setattr(
        knowledge_endpoint,
        "build_knowledge_domain_permission_use_case",
        lambda db: FakeUseCase(),
    )
    try:
        response = TestClient(app).get(
            "/api/v1/knowledge/domain-permissions",
            headers={"X-Organization-Id": str(organization_id)},
        )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission.denied"
