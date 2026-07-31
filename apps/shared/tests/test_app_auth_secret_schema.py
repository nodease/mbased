from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.schemas.app import (
    AppAuthSecretRotateRequest,
    AppAuthSecretRotateResponse,
    AppCreateRequest,
    AppResponse,
)
from apps.shared.schemas.deployment import DeploymentCreate, DeploymentResponse
from pydantic import ValidationError


def test_app_and_deployment_requests_reject_client_secret_fields():
    with pytest.raises(ValidationError):
        AppCreateRequest.model_validate(
            {
                "name": "App",
                "icon": {
                    "type": "emoji",
                    "content": "A",
                    "background_color": "#ffffff",
                },
                "auth_secret": "client-controlled",
            }
        )

    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(
            {
                "app_id": str(uuid4()),
                "type": "api",
                "auth_secret": "client-controlled",
            }
        )


def test_rotation_request_is_strict_and_forbids_unknown_fields():
    assert (
        AppAuthSecretRotateRequest.model_validate(
            {"expected_version": 0, "revoke_previous_immediately": False}
        ).expected_version
        == 0
    )

    for payload in (
        {"expected_version": True},
        {"expected_version": 0, "unknown": "value"},
        {"expected_version": -1},
    ):
        with pytest.raises(ValidationError):
            AppAuthSecretRotateRequest.model_validate(payload)


def test_rotation_response_repr_hides_one_time_secret():
    response = AppAuthSecretRotateResponse(
        secret="one-time-secret",
        version=1,
        rotated_at=datetime.now(timezone.utc),
        previous_grace_active=False,
    )

    assert "one-time-secret" not in repr(response)
    assert response.model_dump()["secret"] == "one-time-secret"


def test_general_resource_responses_never_project_legacy_secret_attributes():
    now = datetime.now(timezone.utc)
    app_id = uuid4()
    user_id = uuid4()
    app_response = AppResponse.model_validate(
        SimpleNamespace(
            id=app_id,
            name="App",
            description=None,
            icon={
                "type": "emoji",
                "content": "A",
                "background_color": "#ffffff",
            },
            workflow_id=None,
            url_slug="app-slug",
            is_market=False,
            forked_from=None,
            active_deployment_id=None,
            active_deployment_type=None,
            active_deployment_is_active=None,
            owner_name=None,
            budget_status=None,
            created_at=now,
            updated_at=now,
            auth_secret="must-not-be-projected",
        )
    )
    deployment_response = DeploymentResponse.model_validate(
        SimpleNamespace(
            id=uuid4(),
            app_id=app_id,
            version=1,
            type=DeploymentType.API,
            url_slug="app-slug",
            description=None,
            config={},
            parameter_optimization=None,
            is_active=True,
            browser_access_policy=None,
            created_by=user_id,
            created_at=now,
            graph_snapshot={},
            input_schema=None,
            output_schema=None,
            auth_secret="must-not-be-projected",
        )
    )

    assert "auth_secret" not in app_response.model_dump()
    assert "auth_secret" not in deployment_response.model_dump()
