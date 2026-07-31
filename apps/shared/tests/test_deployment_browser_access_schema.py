from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.schemas.deployment import (
    DeploymentBrowserAccessPolicy,
    DeploymentBrowserAccessProjection,
    DeploymentBrowserAccessRevisionCreate,
    DeploymentCreate,
    DeploymentPreflightResponse,
    DeploymentResponse,
)
from pydantic import ValidationError


def _policy() -> dict:
    return {
        "contract_version": "deployment_browser_access.v1",
        "embedding": {
            "enabled": True,
            "parent_origins": ["https://portal.example.com"],
        },
    }


def test_browser_access_policy_rejects_unknown_fields_and_non_strict_values() -> None:
    with pytest.raises(ValidationError):
        DeploymentBrowserAccessPolicy.model_validate(
            {
                **_policy(),
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        DeploymentBrowserAccessPolicy.model_validate(
            {
                **_policy(),
                "embedding": {
                    "enabled": 1,
                    "parent_origins": ["https://portal.example.com"],
                },
            }
        )
    with pytest.raises(ValidationError):
        DeploymentBrowserAccessPolicy.model_validate(
            {
                **_policy(),
                "embedding": {
                    "enabled": True,
                    "parent_origins": [123],
                },
            }
        )


def test_browser_access_policy_enforces_contract_literal() -> None:
    with pytest.raises(ValidationError):
        DeploymentBrowserAccessPolicy.model_validate(
            {
                **_policy(),
                "contract_version": "deployment_browser_access.v2",
            }
        )


def test_parent_origin_defaults_are_isolated_between_instances() -> None:
    first = DeploymentBrowserAccessPolicy.model_validate(
        {
            "contract_version": "deployment_browser_access.v1",
            "embedding": {"enabled": False},
        }
    )
    second = DeploymentBrowserAccessPolicy.model_validate(
        {
            "contract_version": "deployment_browser_access.v1",
            "embedding": {"enabled": False},
        }
    )

    first.embedding.parent_origins.append("https://portal.example.com")

    assert second.embedding.parent_origins == []


def test_deployment_create_and_preflight_share_browser_policy_shape() -> None:
    create = DeploymentCreate(
        app_id=uuid4(),
        type=DeploymentType.CHATBOT,
        browser_access_policy=_policy(),
    )

    assert create.browser_access_policy is not None
    assert create.browser_access_policy.embedding.enabled is True
    assert create.browser_access_policy.embedding.parent_origins == [
        "https://portal.example.com"
    ]


def test_deployment_create_keeps_omitted_policy_distinct_for_application_default() -> None:
    create = DeploymentCreate(app_id=uuid4(), type=DeploymentType.CHATBOT)

    assert create.browser_access_policy is None


def test_revision_defaults_inactive_and_requires_policy() -> None:
    request = DeploymentBrowserAccessRevisionCreate(
        browser_access_policy=_policy()
    )

    assert request.is_active is False
    with pytest.raises(ValidationError):
        DeploymentBrowserAccessRevisionCreate.model_validate({})
    with pytest.raises(ValidationError):
        DeploymentBrowserAccessRevisionCreate.model_validate(
            {"browser_access_policy": _policy(), "is_active": 1}
        )


def test_preflight_response_serializes_optional_normalized_policy() -> None:
    response = DeploymentPreflightResponse(
        status="passed",
        audience="anonymous_public",
        normalized_browser_access_policy=_policy(),
    )

    assert response.model_dump()["normalized_browser_access_policy"] == _policy()
    assert (
        DeploymentPreflightResponse(
            status="passed",
            audience="anonymous_public",
        ).normalized_browser_access_policy
        is None
    )


def test_public_projection_contains_only_safe_browser_fields() -> None:
    projection = DeploymentBrowserAccessProjection.model_validate(
        {
            "contract_version": "deployment_browser_access.v1",
            "deployment_version": 3,
            "embedding": {
                "enabled": True,
                "frame_ancestors": ["https://portal.example.com"],
            },
        }
    )

    assert projection.model_dump() == {
        "contract_version": "deployment_browser_access.v1",
        "deployment_version": 3,
        "embedding": {
            "enabled": True,
            "frame_ancestors": ["https://portal.example.com"],
        },
    }
    with pytest.raises(ValidationError):
        DeploymentBrowserAccessProjection.model_validate(
            {
                **projection.model_dump(),
                "app_id": str(uuid4()),
            }
        )


@pytest.mark.parametrize(
    "malformed_policy",
    [
        {**_policy(), "contract_version": "deployment_browser_access.v2"},
        {**_policy(), "unexpected": True},
        {"embedding": "invalid"},
    ],
)
def test_deployment_response_normalizes_malformed_persisted_policy(
    malformed_policy,
) -> None:
    persisted_row = SimpleNamespace(
        id=uuid4(),
        app_id=uuid4(),
        version=1,
        type=DeploymentType.CHATBOT,
        graph_snapshot={"nodes": [], "edges": []},
        created_by=uuid4(),
        created_at=datetime.now(timezone.utc),
        browser_access_policy=malformed_policy,
    )
    response = DeploymentResponse.model_validate(persisted_row)

    assert response.browser_access_policy is not None
    assert response.browser_access_policy.model_dump() == {
        "contract_version": "deployment_browser_access.v1",
        "embedding": {
            "enabled": False,
            "parent_origins": [],
        },
    }
