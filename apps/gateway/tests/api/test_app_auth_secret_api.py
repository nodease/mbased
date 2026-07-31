from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.gateway.services.app_auth_secret_service import (
    AppAuthSecretLifecycleUnavailableError,
    AppAuthSecretNotFoundError,
    AppAuthSecretPermissionDeniedError,
    AppAuthSecretVersionConflictError,
)
from apps.shared.db.session import get_db
from apps.shared.schemas.app import (
    AppAuthSecretRotateResponse,
    AppAuthSecretStatusResponse,
)


def _client():
    user = SimpleNamespace(id=uuid4())
    db = MagicMock()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False), user, db


def teardown_function():
    app.dependency_overrides = {}


@patch("apps.gateway.api.v1.endpoints.app.resolve_active_organization_id")
@patch("apps.gateway.api.v1.endpoints.app.AppAuthSecretService.status")
def test_status_returns_only_safe_lifecycle_metadata(status, resolve_organization):
    client, user, db = _client()
    app_id = uuid4()
    organization_id = uuid4()
    resolve_organization.return_value = organization_id
    status.return_value = AppAuthSecretStatusResponse(
        configured=True,
        version=2,
        rotation_enabled=False,
        rotated_at=datetime(2026, 7, 17, 3, 0, tzinfo=timezone.utc),
        previous_grace_active=False,
    )

    response = client.get(
        f"/api/v1/apps/{app_id}/auth-secret/status",
        headers={"X-Organization-Id": str(organization_id)},
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert "secret" not in response.json()
    assert "verifier" not in response.json()
    assert response.headers["cache-control"] == "no-store, no-cache"
    assert response.headers["pragma"] == "no-cache"
    status.assert_called_once_with(
        db,
        app_id=app_id,
        organization_id=organization_id,
        actor_user_id=user.id,
        lifecycle_mutations_enabled=False,
    )


@patch("apps.gateway.api.v1.endpoints.app.resolve_active_organization_id")
@patch("apps.gateway.api.v1.endpoints.app.AppAuthSecretService.rotate")
def test_rotate_returns_secret_once_and_passes_expected_version(
    rotate,
    resolve_organization,
    monkeypatch,
):
    client, user, db = _client()
    app_id = uuid4()
    organization_id = uuid4()
    resolve_organization.return_value = organization_id
    monkeypatch.setattr(
        "apps.gateway.api.v1.endpoints.app.settings.APP_AUTH_SECRET_LIFECYCLE_MODE",
        "active",
    )
    rotate.return_value = AppAuthSecretRotateResponse(
        secret="one-time-secret",
        version=3,
        rotated_at=datetime(2026, 7, 17, 3, 0, tzinfo=timezone.utc),
        previous_grace_active=True,
        previous_valid_until=datetime(2026, 7, 17, 3, 5, tzinfo=timezone.utc),
    )

    response = client.post(
        f"/api/v1/apps/{app_id}/auth-secret/rotate",
        headers={"X-Organization-Id": str(organization_id)},
        json={"expected_version": 2, "revoke_previous_immediately": False},
    )

    assert response.status_code == 200
    assert response.json()["secret"] == "one-time-secret"
    assert response.headers["cache-control"] == "no-store, no-cache"
    assert response.headers["pragma"] == "no-cache"
    rotate.assert_called_once_with(
        db,
        app_id=app_id,
        organization_id=organization_id,
        actor_user_id=user.id,
        expected_version=2,
        revoke_previous_immediately=False,
        lifecycle_mutations_enabled=True,
    )


@patch("apps.gateway.api.v1.endpoints.app.resolve_active_organization_id")
@patch("apps.gateway.api.v1.endpoints.app.AppAuthSecretService.rotate")
def test_rotate_rejects_unknown_request_fields_before_service(
    rotate,
    resolve_organization,
):
    client, _, _ = _client()
    app_id = uuid4()
    organization_id = uuid4()
    resolve_organization.return_value = organization_id

    response = client.post(
        f"/api/v1/apps/{app_id}/auth-secret/rotate",
        headers={"X-Organization-Id": str(organization_id)},
        json={"expected_version": 0, "auth_secret": "client-controlled"},
    )

    assert response.status_code == 422
    rotate.assert_not_called()


@patch("apps.gateway.api.v1.endpoints.app.resolve_active_organization_id")
@patch("apps.gateway.api.v1.endpoints.app.AppAuthSecretService.rotate")
def test_rotate_maps_hidden_permission_and_version_errors(
    rotate,
    resolve_organization,
):
    client, _, _ = _client()
    app_id = uuid4()
    organization_id = uuid4()
    resolve_organization.return_value = organization_id

    cases = (
        (AppAuthSecretNotFoundError("app.not_found"), 404, "app.not_found"),
        (
            AppAuthSecretPermissionDeniedError("permission.denied"),
            403,
            "permission.denied",
        ),
        (
            AppAuthSecretVersionConflictError("version"),
            409,
            "app.auth_secret_version_conflict",
        ),
        (
            AppAuthSecretLifecycleUnavailableError("unavailable"),
            503,
            "app.auth_secret_lifecycle_unavailable",
        ),
    )
    for error, expected_status, expected_code in cases:
        rotate.side_effect = error
        response = client.post(
            f"/api/v1/apps/{app_id}/auth-secret/rotate",
            headers={"X-Organization-Id": str(organization_id)},
            json={"expected_version": 0},
        )
        assert response.status_code == expected_status
        assert response.json()["error"]["code"] == expected_code
