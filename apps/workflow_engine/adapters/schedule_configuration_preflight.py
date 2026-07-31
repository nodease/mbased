from __future__ import annotations

import uuid

from apps.shared.services.workflow_configuration_preflight import (
    workflow_configuration_issues,
)


class ScheduleConfigurationPreflightAdapter:
    def is_ready(
        self,
        *,
        graph_snapshot: dict | None,
        organization_id: uuid.UUID,
    ) -> bool:
        del organization_id
        return not workflow_configuration_issues(graph_snapshot)
