"""Workflow Engine composition provider for deployment runtime policy."""

from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
    DeploymentRuntimePolicy,
)


def get_deployment_runtime_policy() -> DeploymentRuntimePolicy:
    """Return the process-local immutable runtime policy."""
    return DEFAULT_DEPLOYMENT_RUNTIME_POLICY


__all__ = ["get_deployment_runtime_policy"]
