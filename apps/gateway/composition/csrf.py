from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from apps.gateway.adapters.csrf.observability import CsrfObservability
from apps.gateway.application.csrf.models import (
    CsrfContentKind,
    CsrfRoutePolicy,
    CsrfRoutePolicyKind,
    CsrfRoutePolicyRegistry,
)
from apps.gateway.application.csrf.token import CsrfTokenService, CsrfValidationReason
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.core.http_security import resolve_session_signing_secret
from apps.shared.audit import record_audit
from apps.shared.audit.actions import AuditAction


_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_PRE_AUTH_ROUTES = frozenset(
    {
        ("POST", "/api/v1/auth/signup"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/logout"),
    }
)
_PUBLIC_ANONYMOUS_ROUTES = frozenset(
    {
        ("POST", "/api/v1/run-public/{url_slug}"),
        ("POST", "/api/v1/run-public/{url_slug}/chat"),
    }
)
_SERVER_CREDENTIAL_ROUTES = frozenset(
    {
        ("POST", "/api/v1/run/{url_slug}"),
        ("POST", "/api/v1/hooks/{url_slug}"),
    }
)
_LEGACY_COOKIE_AUTHENTICATED_ROUTES = frozenset(
    {
        ("POST", "/api/v1/teams"),
        ("PATCH", "/api/v1/teams/{team_id}"),
        ("POST", "/api/v1/teams/{team_id}/members"),
        ("DELETE", "/api/v1/teams/{team_id}/members/{user_id}"),
        ("DELETE", "/api/v1/teams/{team_id}"),
        ("POST", "/api/v1/permissions/bulk-grants"),
        (
            "PUT",
            "/api/v1/permissions/workflows/{workflow_id}/teams/{team_id}",
        ),
        (
            "PUT",
            "/api/v1/permissions/knowledge-bases/{knowledge_base_id}/teams/{team_id}",
        ),
        (
            "PUT",
            "/api/v1/permissions/llm-credentials/{credential_id}/teams/{team_id}",
        ),
        (
            "PUT",
            "/api/v1/permissions/workflows/{workflow_id}/users/{user_id}",
        ),
        (
            "PUT",
            "/api/v1/permissions/knowledge-bases/{knowledge_base_id}/users/{user_id}",
        ),
        (
            "PUT",
            "/api/v1/permissions/llm-credentials/{credential_id}/users/{user_id}",
        ),
        (
            "DELETE",
            "/api/v1/permissions/workflows/{workflow_id}/teams/{team_id}",
        ),
        (
            "DELETE",
            "/api/v1/permissions/knowledge-bases/{knowledge_base_id}/teams/{team_id}",
        ),
        (
            "DELETE",
            "/api/v1/permissions/llm-credentials/{credential_id}/teams/{team_id}",
        ),
        (
            "DELETE",
            "/api/v1/permissions/workflows/{workflow_id}/users/{user_id}",
        ),
        (
            "DELETE",
            "/api/v1/permissions/knowledge-bases/{knowledge_base_id}/users/{user_id}",
        ),
        (
            "DELETE",
            "/api/v1/permissions/llm-credentials/{credential_id}/users/{user_id}",
        ),
    }
)
_MULTIPART_COOKIE_ROUTES = frozenset(
    {
        ("POST", "/api/v1/rag/upload"),
        ("POST", "/api/v1/workflows/{workflow_id}/stream"),
    }
)
_OAUTH_STATE_ROUTES = frozenset(
    {
        ("GET", "/api/v1/auth/google/login"),
        ("GET", "/api/v1/auth/google/callback"),
    }
)


class CsrfRouteInventoryError(RuntimeError):
    pass


def _effective_routes(app: Any):
    for route in app.routes:
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        if callable(effective_route_contexts):
            yield from effective_route_contexts()
            continue
        if (
            getattr(route, "methods", None)
            and getattr(route, "path", None)
            and getattr(route, "path_regex", None)
        ):
            yield route


def _dependency_calls(dependant: Any):
    for child in getattr(dependant, "dependencies", ()):
        yield getattr(child, "call", None)
        yield from _dependency_calls(child)


def _has_cookie_auth_dependency(route: Any) -> bool:
    return any(
        dependency is get_current_user
        for dependency in _dependency_calls(getattr(route, "dependant", None))
    )


def _classify_route(
    route: Any,
    key: tuple[str, str],
) -> CsrfRoutePolicyKind | None:
    if key in _PRE_AUTH_ROUTES:
        return CsrfRoutePolicyKind.PRE_AUTH_SESSION
    if key in _PUBLIC_ANONYMOUS_ROUTES:
        return CsrfRoutePolicyKind.PUBLIC_ANONYMOUS
    if key in _SERVER_CREDENTIAL_ROUTES:
        return CsrfRoutePolicyKind.SERVER_CREDENTIAL
    if key in _LEGACY_COOKIE_AUTHENTICATED_ROUTES or _has_cookie_auth_dependency(route):
        return CsrfRoutePolicyKind.COOKIE_AUTHENTICATED
    return None


def _content_kind(
    route: Any,
    key: tuple[str, str],
    policy_kind: CsrfRoutePolicyKind,
) -> CsrfContentKind:
    if policy_kind in {
        CsrfRoutePolicyKind.PUBLIC_ANONYMOUS,
        CsrfRoutePolicyKind.SERVER_CREDENTIAL,
    }:
        return CsrfContentKind.UNRESTRICTED
    if key in _MULTIPART_COOKIE_ROUTES:
        return CsrfContentKind.MULTIPART
    if getattr(route, "body_field", None) is None:
        return CsrfContentKind.BODY_OPTIONAL
    media_type = getattr(route.body_field.field_info, "media_type", None)
    if media_type not in {None, "application/json"}:
        raise CsrfRouteInventoryError(
            f"unsupported protected route media type: {key!r}"
        )
    return CsrfContentKind.JSON


def build_csrf_route_policy_registry(app: Any) -> CsrfRoutePolicyRegistry:
    policies: list[CsrfRoutePolicy] = []
    actual_unsafe_keys: set[tuple[str, str]] = set()
    unclassified: list[tuple[str, str]] = []
    oauth_keys: set[tuple[str, str]] = set()

    for route in _effective_routes(app):
        methods = {method.upper() for method in route.methods}
        for method in methods:
            key = (method, route.path)
            if key in _OAUTH_STATE_ROUTES:
                oauth_keys.add(key)
            if method not in _UNSAFE_METHODS:
                continue
            if key in actual_unsafe_keys:
                raise CsrfRouteInventoryError(f"duplicate unsafe route: {key!r}")
            actual_unsafe_keys.add(key)
            policy_kind = _classify_route(route, key)
            if policy_kind is None:
                unclassified.append(key)
                continue
            policies.append(
                CsrfRoutePolicy(
                    method=method,
                    path_template=route.path,
                    path_pattern=route.path_regex,
                    policy_kind=policy_kind,
                    content_kind=_content_kind(route, key, policy_kind),
                )
            )

    if unclassified:
        raise CsrfRouteInventoryError(
            f"unclassified unsafe route: {sorted(unclassified)!r}"
        )

    configured_routes = (
        _PRE_AUTH_ROUTES
        | _PUBLIC_ANONYMOUS_ROUTES
        | _SERVER_CREDENTIAL_ROUTES
        | _LEGACY_COOKIE_AUTHENTICATED_ROUTES
        | _MULTIPART_COOKIE_ROUTES
    )
    stale_routes = configured_routes - actual_unsafe_keys
    if stale_routes:
        raise CsrfRouteInventoryError(
            f"configured CSRF route does not exist: {sorted(stale_routes)!r}"
        )
    if oauth_keys != _OAUTH_STATE_ROUTES:
        raise CsrfRouteInventoryError(
            "OAuth state route inventory does not match the configured callbacks"
        )
    return CsrfRoutePolicyRegistry(tuple(policies))


@lru_cache(maxsize=1)
def csrf_token_service() -> CsrfTokenService:
    secret = resolve_session_signing_secret(
        os.getenv("SECRET_KEY"),
        node_env=os.getenv("NODE_ENV"),
    )
    return CsrfTokenService.from_root_secret(secret)


def csrf_enforcement_enabled() -> bool:
    node_env = (os.getenv("NODE_ENV") or "").strip().lower()
    configured_mode = (os.getenv("CSRF_ENFORCEMENT_MODE") or "").strip().lower()
    if not configured_mode or configured_mode == "enforce":
        return True
    if configured_mode == "disabled" and node_env == "test":
        return False
    raise RuntimeError("CSRF enforcement mode is invalid")


def record_csrf_auth_required(
    policy: CsrfRoutePolicy,
    method: str,
    request_id: str,
) -> None:
    record_audit(
        action=AuditAction.AUTH_PERMISSION_DENIED,
        category="action",
        actor_type="system",
        status="failure",
        metadata={
            "reason": "auth.required",
            "policy": policy.policy_kind.value,
            "method": method,
            "request_id": request_id,
        },
    )


def record_csrf_bootstrap_denial(
    reason: CsrfValidationReason,
    *,
    request_id: str | None,
) -> None:
    CsrfObservability.record(
        reason=reason.value,
        policy=CsrfRoutePolicyKind.PRE_AUTH_SESSION.value,
        method="GET",
    )
    record_audit(
        action=AuditAction.AUTH_PERMISSION_DENIED,
        category="action",
        actor_type="system",
        status="failure",
        metadata={
            "reason": f"auth.csrf.{reason.value}",
            "policy": CsrfRoutePolicyKind.PRE_AUTH_SESSION.value,
            "method": "GET",
            "request_id": request_id,
        },
    )


def record_csrf_denial(
    reason: CsrfValidationReason,
    policy: CsrfRoutePolicy,
    method: str,
    request_id: str,
) -> None:
    CsrfObservability.record(
        reason=reason.value,
        policy=policy.policy_kind.value,
        method=method,
    )
    record_audit(
        action=AuditAction.AUTH_PERMISSION_DENIED,
        category="action",
        actor_type="system",
        status="failure",
        metadata={
            "reason": f"auth.csrf.{reason.value}",
            "policy": policy.policy_kind.value,
            "method": method,
            "request_id": request_id,
        },
    )
