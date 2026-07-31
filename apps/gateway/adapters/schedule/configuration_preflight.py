from __future__ import annotations

import uuid
from collections.abc import Mapping

from apps.gateway.application.deployment.errors import DeploymentPreflightBlocked
from apps.gateway.application.deployment.models import NodeCatalogSnapshot
from apps.gateway.application.deployment.ports import DeploymentPreflightRepository
from apps.gateway.application.deployment.preflight import DeploymentPreflightUseCase
from apps.shared.services.workflow_configuration_preflight import (
    workflow_configuration_issues,
)


class ScheduleConfigurationPreflightAdapter:
    def __init__(
        self,
        repository: DeploymentPreflightRepository,
        *,
        node_catalog_by_type: Mapping[str, NodeCatalogSnapshot],
    ) -> None:
        self.repository = repository
        self.node_catalog_by_type = dict(node_catalog_by_type)

    def is_ready(
        self,
        *,
        graph_snapshot: dict | None,
        organization_id: uuid.UUID,
    ) -> bool:
        if workflow_configuration_issues(graph_snapshot):
            return False
        use_case = DeploymentPreflightUseCase(
            self.repository,
            organization_id=organization_id,
            principal_id=None,
            node_catalog_by_type=self.node_catalog_by_type,
        )
        try:
            use_case.enforce_schedule_dispatch(
                graph_snapshot=graph_snapshot,
            )
        except DeploymentPreflightBlocked:
            return False
        return True
