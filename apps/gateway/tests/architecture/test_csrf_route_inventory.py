from collections import Counter
from pathlib import Path

import pytest
from fastapi import FastAPI

from apps.gateway.application.csrf.models import (
    CsrfContentKind,
    CsrfRoutePolicyKind,
)
from apps.gateway.composition.csrf import (
    CsrfRouteInventoryError,
    build_csrf_route_policy_registry,
)
from apps.gateway.main import app


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_every_gateway_unsafe_route_has_exactly_one_csrf_policy():
    registry = build_csrf_route_policy_registry(app)

    assert len(registry.policies) == 138
    assert Counter(policy.policy_kind for policy in registry.policies) == {
        CsrfRoutePolicyKind.COOKIE_AUTHENTICATED: 131,
        CsrfRoutePolicyKind.PRE_AUTH_SESSION: 3,
        CsrfRoutePolicyKind.PUBLIC_ANONYMOUS: 2,
        CsrfRoutePolicyKind.SERVER_CREDENTIAL: 2,
    }


def test_multipart_cookie_routes_are_explicit_and_bounded():
    registry = build_csrf_route_policy_registry(app)

    assert {
        (policy.method, policy.path_template)
        for policy in registry.policies
        if policy.content_kind is CsrfContentKind.MULTIPART
    } == {
        ("POST", "/api/v1/rag/upload"),
        ("POST", "/api/v1/workflows/{workflow_id}/stream"),
    }


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        (
            "POST",
            "/api/v1/auth/login",
            CsrfRoutePolicyKind.PRE_AUTH_SESSION,
        ),
        (
            "POST",
            "/api/v1/teams",
            CsrfRoutePolicyKind.COOKIE_AUTHENTICATED,
        ),
        (
            "PUT",
            "/api/v1/permissions/workflows/{workflow_id}/teams/{team_id}",
            CsrfRoutePolicyKind.COOKIE_AUTHENTICATED,
        ),
        (
            "POST",
            "/api/v1/run-public/{url_slug}/chat",
            CsrfRoutePolicyKind.PUBLIC_ANONYMOUS,
        ),
        (
            "POST",
            "/api/v1/hooks/{url_slug}",
            CsrfRoutePolicyKind.SERVER_CREDENTIAL,
        ),
    ],
)
def test_high_risk_and_exception_routes_keep_their_declared_policy(
    method: str,
    path: str,
    expected: CsrfRoutePolicyKind,
):
    registry = build_csrf_route_policy_registry(app)

    policy = registry.by_key(method, path)

    assert policy.policy_kind is expected


def test_new_unsafe_route_without_auth_dependency_or_exception_fails_inventory():
    unclassified_app = FastAPI()

    @unclassified_app.post("/api/v1/new-public-mutation")
    def new_public_mutation():
        return {"ok": True}

    with pytest.raises(CsrfRouteInventoryError, match="unclassified unsafe route"):
        build_csrf_route_policy_registry(unclassified_app)


def test_auth_api_spec_lists_csrf_endpoint_only_in_endpoint_inventory():
    api_spec = (
        REPOSITORY_ROOT / "docs" / "features" / "auth" / "api_spec.md"
    ).read_text(encoding="utf-8")
    endpoint_row = (
        "| GET | `/auth/csrf` | Cookie-authenticated/pre-auth mutation용 "
        "10분 signed CSRF token과 host-only HttpOnly cookie를 발급한다. "
        "| Safe bootstrap; resource permission 없음 |"
    )

    assert api_spec.count(endpoint_row) == 1
