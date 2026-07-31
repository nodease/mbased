from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.gateway.api.v1.endpoints import deployment as deployment_endpoint
from apps.gateway.application.deployment.browser_access_errors import (
    BrowserAccessConversationContractError,
    BrowserAccessPolicyError,
    BrowserAccessResourceHidden,
)
from apps.gateway.application.deployment.browser_access_models import (
    BrowserAccessRevision,
    PublicBrowserAccessProjection,
)
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.schemas.deployment import DeploymentBrowserAccessRevisionCreate


def _policy() -> dict:
    return {
        "contract_version": "deployment_browser_access.v1",
        "embedding": {
            "enabled": True,
            "parent_origins": ["https://portal.example.com"],
        },
    }


class _RevisionUseCase:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.command = None

    def execute(self, command):
        self.command = command
        if self.error is not None:
            raise self.error
        return self.result


class _PublicUseCase:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.arguments = None

    def execute(self, url_slug, *, environment):
        self.arguments = (url_slug, environment)
        if self.error is not None:
            raise self.error
        return self.result


def _revision(actor_id: uuid.UUID, *, active: bool = False) -> BrowserAccessRevision:
    return BrowserAccessRevision(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        version=3,
        deployment_type="chatbot",
        graph_snapshot={"nodes": [], "edges": []},
        config={},
        input_schema=None,
        output_schema=None,
        description="revision",
        created_by=actor_id,
        created_at=datetime.now(timezone.utc),
        is_active=active,
        browser_access_policy=_policy(),
        url_slug="public-chatbot",
    )


def test_revision_endpoint_authorizes_deploy_and_maps_application_result(
    monkeypatch,
) -> None:
    actor = SimpleNamespace(id=uuid.uuid4())
    workflow_id = uuid.uuid4()
    source_id = uuid.uuid4()
    result = _revision(actor.id)
    use_case = _RevisionUseCase(result=result)
    checked = []
    app = SimpleNamespace(id=result.app_id, workflow_id=workflow_id)
    source = SimpleNamespace(id=source_id, app_id=app.id)

    monkeypatch.setattr(
        deployment_endpoint,
        "_deployment_app_and_workflow_id",
        lambda db, deployment_id: (source, app, workflow_id),
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "ensure_workflow_permission",
        lambda db, user, checked_workflow_id, action: checked.append(
            (user.id, checked_workflow_id, action)
        ),
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "build_browser_access_revision_use_case",
        lambda db, **kwargs: use_case,
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "deployment_browser_access_environment",
        lambda: "production",
    )

    response = deployment_endpoint.create_browser_access_revision(
        source_id,
        DeploymentBrowserAccessRevisionCreate(browser_access_policy=_policy()),
        db=object(),
        current_user=actor,
    )

    assert checked == [(actor.id, workflow_id, "deploy")]
    assert use_case.command.source_deployment_id == source_id
    assert use_case.command.actor_id == actor.id
    assert use_case.command.browser_access_policy == _policy()
    assert use_case.command.is_active is False
    assert use_case.command.environment == "production"
    assert response.id == result.id
    assert response.type == DeploymentType.CHATBOT
    assert response.browser_access_policy is not None
    assert response.browser_access_policy.model_dump() == _policy()


def test_revision_endpoint_maps_strict_conversation_contract_error(
    monkeypatch,
) -> None:
    actor = SimpleNamespace(id=uuid.uuid4())
    source_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app = SimpleNamespace(id=uuid.uuid4(), workflow_id=workflow_id)
    source = SimpleNamespace(id=source_id, app_id=app.id)
    use_case = _RevisionUseCase(
        error=BrowserAccessConversationContractError(
            "conversation.consumer_mapping_required"
        )
    )

    monkeypatch.setattr(
        deployment_endpoint,
        "_deployment_app_and_workflow_id",
        lambda db, deployment_id: (source, app, workflow_id),
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "ensure_workflow_permission",
        lambda *args: None,
    )
    monkeypatch.setattr(
        deployment_endpoint.DeploymentService,
        "migrate_legacy_node_secrets",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "build_browser_access_revision_use_case",
        lambda db, **kwargs: use_case,
    )
    monkeypatch.setattr(
        "apps.gateway.services.scheduler_service.get_scheduler_service",
        lambda: object(),
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint.create_browser_access_revision(
            source_id,
            DeploymentBrowserAccessRevisionCreate(
                browser_access_policy=_policy(),
                is_active=True,
            ),
            db=object(),
            current_user=actor,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == {
        "code": "conversation.consumer_mapping_required",
        "message": "The public conversation consumer mapping is invalid.",
    }


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (
            BrowserAccessPolicyError(
                "deployment.browser_access.invalid_origin",
                origin_index=0,
            ),
            422,
            "deployment.browser_access.invalid_origin",
        ),
        (BrowserAccessResourceHidden(), 404, None),
    ],
)
def test_revision_endpoint_maps_safe_application_errors(
    monkeypatch,
    error,
    status_code,
    code,
) -> None:
    actor = SimpleNamespace(id=uuid.uuid4())
    source_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    app = SimpleNamespace(id=uuid.uuid4(), workflow_id=workflow_id)
    source = SimpleNamespace(id=source_id, app_id=app.id)
    use_case = _RevisionUseCase(error=error)

    monkeypatch.setattr(
        deployment_endpoint,
        "_deployment_app_and_workflow_id",
        lambda db, deployment_id: (source, app, workflow_id),
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "ensure_workflow_permission",
        lambda *args: None,
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "build_browser_access_revision_use_case",
        lambda db, **kwargs: use_case,
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint.create_browser_access_revision(
            source_id,
            DeploymentBrowserAccessRevisionCreate(browser_access_policy=_policy()),
            db=object(),
            current_user=actor,
        )

    assert exc_info.value.status_code == status_code
    if code is not None:
        assert exc_info.value.detail["code"] == code
        assert exc_info.value.detail["field"] == (
            "browser_access_policy.embedding.parent_origins[0]"
        )
        assert "portal.example.com" not in str(exc_info.value.detail)


def test_public_projection_endpoint_sets_no_store_and_returns_safe_schema(
    monkeypatch,
) -> None:
    use_case = _PublicUseCase(
        result=PublicBrowserAccessProjection(
            contract_version="deployment_browser_access.v1",
            deployment_version=5,
            enabled=True,
            frame_ancestors=("https://portal.example.com",),
        )
    )
    response = SimpleNamespace(headers={})
    monkeypatch.setattr(
        deployment_endpoint,
        "build_public_browser_access_use_case",
        lambda db: use_case,
    )
    monkeypatch.setattr(
        deployment_endpoint,
        "deployment_browser_access_environment",
        lambda: "production",
    )

    result = deployment_endpoint.get_public_browser_access_policy(
        "public-chatbot",
        response,
        db=object(),
    )

    assert use_case.arguments == ("public-chatbot", "production")
    assert response.headers == {"Cache-Control": "no-store"}
    assert result.model_dump() == {
        "contract_version": "deployment_browser_access.v1",
        "deployment_version": 5,
        "embedding": {
            "enabled": True,
            "frame_ancestors": ["https://portal.example.com"],
        },
    }
    assert "Access-Control-Allow-Origin" not in response.headers


def test_public_projection_endpoint_hides_missing_target(monkeypatch) -> None:
    use_case = _PublicUseCase(error=BrowserAccessResourceHidden())
    response = SimpleNamespace(headers={})
    monkeypatch.setattr(
        deployment_endpoint,
        "build_public_browser_access_use_case",
        lambda db: use_case,
    )

    with pytest.raises(HTTPException) as exc_info:
        deployment_endpoint.get_public_browser_access_policy(
            "missing",
            response,
            db=object(),
        )

    assert exc_info.value.status_code == 404
    assert response.headers == {"Cache-Control": "no-store"}
