from __future__ import annotations

from .models import DeploymentPreflightResult


class DeploymentPreflightBlocked(Exception):
    def __init__(self, result: DeploymentPreflightResult) -> None:
        super().__init__("Deployment preflight blocked activation")
        self.result = result
