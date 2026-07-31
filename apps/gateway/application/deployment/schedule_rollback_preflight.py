from __future__ import annotations

from apps.gateway.application.deployment.schedule_models import (
    ScheduleRollbackBlockers,
)
from apps.gateway.application.deployment.schedule_ports import (
    ScheduleDispatchRepositoryPort,
)


class ScheduleRollbackPreflightUseCase:
    def evaluate(
        self, *, repository: ScheduleDispatchRepositoryPort
    ) -> ScheduleRollbackBlockers:
        return repository.count_rollback_blockers()
