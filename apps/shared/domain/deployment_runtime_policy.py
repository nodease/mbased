"""Pure deployment runtime surface policy.

This module intentionally avoids FastAPI, SQLAlchemy, Celery, and concrete
Gateway/Workflow Engine imports so both runtimes can share one allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, FrozenSet, Iterable, Mapping

SURFACE_PUBLIC_INFO = "public_info"
SURFACE_AUTHENTICATED_RUN_INFO = "authenticated_run_info"
SURFACE_AUTHENTICATED_RUN = "authenticated_run"
SURFACE_API_SECRET_RUN = "api_secret_run"
SURFACE_APP_PUBLIC_RUN = "app_public_run"
SURFACE_WEBHOOK_RUN = "webhook_run"
SURFACE_SCHEDULE_RUN = "schedule_run"
SURFACE_WORKFLOW_NODE_CHILD_RUN = "workflow_node_child_run"
KNOWN_RUNTIME_SURFACES = frozenset(
    {
        SURFACE_PUBLIC_INFO,
        SURFACE_AUTHENTICATED_RUN_INFO,
        SURFACE_AUTHENTICATED_RUN,
        SURFACE_API_SECRET_RUN,
        SURFACE_APP_PUBLIC_RUN,
        SURFACE_WEBHOOK_RUN,
        SURFACE_SCHEDULE_RUN,
        SURFACE_WORKFLOW_NODE_CHILD_RUN,
    }
)
KNOWN_TRIGGER_MODES = frozenset(
    {"api", "api_secret", "app", "webhook", "schedule", "scheduler", "workflow_node"}
)

DEPLOYMENT_API = "api"
DEPLOYMENT_WEBAPP = "webapp"
DEPLOYMENT_WIDGET = "widget"
DEPLOYMENT_CHATBOT = "chatbot"
DEPLOYMENT_INTERNAL_CHATBOT = "internal_chatbot"
DEPLOYMENT_MCP = "mcp"
DEPLOYMENT_WORKFLOW_NODE = "workflow_node"
DEPLOYMENT_SCHEDULE = "schedule"
DEPLOYMENT_WEBHOOK = "webhook"

DIRECT_NON_WORKFLOW_NODE_DEPLOYMENT_TYPES = frozenset(
    {
        DEPLOYMENT_API,
        DEPLOYMENT_WEBAPP,
        DEPLOYMENT_WIDGET,
        DEPLOYMENT_CHATBOT,
        DEPLOYMENT_INTERNAL_CHATBOT,
        DEPLOYMENT_MCP,
        DEPLOYMENT_SCHEDULE,
        DEPLOYMENT_WEBHOOK,
    }
)
PUBLIC_APP_DEPLOYMENT_TYPES = frozenset(
    {DEPLOYMENT_WEBAPP, DEPLOYMENT_WIDGET, DEPLOYMENT_CHATBOT}
)
KNOWN_DEPLOYMENT_TYPES = DIRECT_NON_WORKFLOW_NODE_DEPLOYMENT_TYPES | frozenset(
    {DEPLOYMENT_WORKFLOW_NODE}
)


def _normalized_value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    raw = enum_value if enum_value is not None else value
    normalized = str(raw).strip().lower()
    return normalized or None


@dataclass(frozen=True, slots=True)
class DeploymentRuntimePolicy:
    """Immutable allowlist injected at runtime composition boundaries."""

    allowed_types_by_surface: Mapping[str, FrozenSet[str]]
    surface_by_trigger_mode: Mapping[str, str]

    def __post_init__(self) -> None:
        normalized_allowed: dict[str, FrozenSet[str]] = {}
        for surface, deployment_types in self.allowed_types_by_surface.items():
            normalized_surface = _normalized_value(surface)
            if normalized_surface not in KNOWN_RUNTIME_SURFACES:
                raise ValueError("deployment runtime policy surface is unknown")

            normalized_type_values: set[str] = set()
            for value in deployment_types:
                normalized_type = _normalized_value(value)
                if normalized_type not in KNOWN_DEPLOYMENT_TYPES:
                    raise ValueError("deployment runtime policy type is unknown")
                normalized_type_values.add(normalized_type)
            normalized_allowed[normalized_surface] = frozenset(normalized_type_values)

        normalized_triggers: dict[str, str] = {}
        for trigger_mode, surface in self.surface_by_trigger_mode.items():
            normalized_trigger = _normalized_value(trigger_mode)
            normalized_surface = _normalized_value(surface)
            if normalized_trigger not in KNOWN_TRIGGER_MODES:
                raise ValueError("deployment runtime trigger mode is unknown")
            if normalized_surface not in KNOWN_RUNTIME_SURFACES:
                raise ValueError("deployment runtime trigger surface is unknown")
            if normalized_surface not in normalized_allowed:
                raise ValueError("deployment runtime trigger surface is not configured")
            normalized_triggers[normalized_trigger] = normalized_surface

        object.__setattr__(
            self,
            "allowed_types_by_surface",
            MappingProxyType(normalized_allowed),
        )
        object.__setattr__(
            self,
            "surface_by_trigger_mode",
            MappingProxyType(normalized_triggers),
        )

    @classmethod
    def create(
        cls,
        *,
        allowed_types_by_surface: Mapping[str, Iterable[str]],
        surface_by_trigger_mode: Mapping[str, str],
    ) -> "DeploymentRuntimePolicy":
        return cls(
            allowed_types_by_surface=allowed_types_by_surface,
            surface_by_trigger_mode=surface_by_trigger_mode,
        )

    def with_surface_allowed_types(
        self,
        surface: str,
        deployment_types: Iterable[str],
    ) -> "DeploymentRuntimePolicy":
        allowed_types = dict(self.allowed_types_by_surface)
        allowed_types[surface] = frozenset(deployment_types)
        return self.create(
            allowed_types_by_surface=allowed_types,
            surface_by_trigger_mode=self.surface_by_trigger_mode,
        )


DEFAULT_DEPLOYMENT_RUNTIME_POLICY = DeploymentRuntimePolicy.create(
    allowed_types_by_surface={
        SURFACE_PUBLIC_INFO: PUBLIC_APP_DEPLOYMENT_TYPES,
        SURFACE_AUTHENTICATED_RUN_INFO: DIRECT_NON_WORKFLOW_NODE_DEPLOYMENT_TYPES,
        SURFACE_AUTHENTICATED_RUN: DIRECT_NON_WORKFLOW_NODE_DEPLOYMENT_TYPES,
        SURFACE_API_SECRET_RUN: frozenset({DEPLOYMENT_API}),
        SURFACE_APP_PUBLIC_RUN: frozenset(
            {DEPLOYMENT_WEBAPP, DEPLOYMENT_WIDGET, DEPLOYMENT_CHATBOT}
        ),
        SURFACE_WEBHOOK_RUN: frozenset({DEPLOYMENT_WEBHOOK}),
        SURFACE_SCHEDULE_RUN: frozenset({DEPLOYMENT_SCHEDULE}),
        SURFACE_WORKFLOW_NODE_CHILD_RUN: frozenset({DEPLOYMENT_WORKFLOW_NODE}),
    },
    surface_by_trigger_mode={
        "api": SURFACE_API_SECRET_RUN,
        "api_secret": SURFACE_API_SECRET_RUN,
        "app": SURFACE_APP_PUBLIC_RUN,
        "webhook": SURFACE_WEBHOOK_RUN,
        "schedule": SURFACE_SCHEDULE_RUN,
        "scheduler": SURFACE_SCHEDULE_RUN,
        "workflow_node": SURFACE_WORKFLOW_NODE_CHILD_RUN,
    },
)


@dataclass(frozen=True)
class DeploymentRuntimePolicyResult:
    allowed: bool
    surface: str | None
    deployment_type: str | None
    reason: str | None = None


def deployment_type_value(value: Any) -> str | None:
    """Normalize enum-like or string deployment type values."""
    return _normalized_value(value)


def trigger_mode_to_surface(
    trigger_mode: Any,
    *,
    policy: DeploymentRuntimePolicy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
) -> str | None:
    normalized = deployment_type_value(trigger_mode)
    if normalized not in KNOWN_TRIGGER_MODES:
        return None
    return policy.surface_by_trigger_mode.get(normalized)


def allowed_deployment_types_for_surface(
    surface: Any,
    *,
    policy: DeploymentRuntimePolicy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
) -> FrozenSet[str]:
    normalized = deployment_type_value(surface)
    if normalized is None:
        return frozenset()
    return policy.allowed_types_by_surface.get(normalized, frozenset())


def evaluate_deployment_runtime_surface(
    deployment_type: Any,
    surface: Any,
    *,
    policy: DeploymentRuntimePolicy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
) -> DeploymentRuntimePolicyResult:
    normalized_surface = deployment_type_value(surface)
    normalized_deployment_type = deployment_type_value(deployment_type)
    if normalized_surface not in KNOWN_RUNTIME_SURFACES:
        return DeploymentRuntimePolicyResult(
            allowed=False,
            surface=None,
            deployment_type=normalized_deployment_type,
            reason="unknown_surface",
        )

    if normalized_deployment_type not in KNOWN_DEPLOYMENT_TYPES:
        return DeploymentRuntimePolicyResult(
            allowed=False,
            surface=normalized_surface,
            deployment_type=normalized_deployment_type,
            reason="unknown_deployment_type",
        )

    allowed_types = policy.allowed_types_by_surface.get(normalized_surface)
    if not allowed_types:
        return DeploymentRuntimePolicyResult(
            allowed=False,
            surface=normalized_surface,
            deployment_type=normalized_deployment_type,
            reason="unknown_surface",
        )

    if normalized_deployment_type not in allowed_types:
        return DeploymentRuntimePolicyResult(
            allowed=False,
            surface=normalized_surface,
            deployment_type=normalized_deployment_type,
            reason="deployment_type_not_allowed_for_surface",
        )

    return DeploymentRuntimePolicyResult(
        allowed=True,
        surface=normalized_surface,
        deployment_type=normalized_deployment_type,
    )


def is_deployment_type_allowed_for_surface(
    deployment_type: Any,
    surface: Any,
    *,
    policy: DeploymentRuntimePolicy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
) -> bool:
    return evaluate_deployment_runtime_surface(
        deployment_type,
        surface,
        policy=policy,
    ).allowed


def evaluate_deployment_runtime_trigger(
    deployment_type: Any,
    trigger_mode: Any,
    *,
    policy: DeploymentRuntimePolicy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
) -> DeploymentRuntimePolicyResult:
    surface = trigger_mode_to_surface(trigger_mode, policy=policy)
    if surface is None:
        return DeploymentRuntimePolicyResult(
            allowed=False,
            surface=None,
            deployment_type=deployment_type_value(deployment_type),
            reason="unknown_trigger_mode",
        )
    return evaluate_deployment_runtime_surface(
        deployment_type,
        surface,
        policy=policy,
    )


def is_deployment_type_allowed_for_trigger(
    deployment_type: Any,
    trigger_mode: Any,
    *,
    policy: DeploymentRuntimePolicy = DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
) -> bool:
    return evaluate_deployment_runtime_trigger(
        deployment_type,
        trigger_mode,
        policy=policy,
    ).allowed
