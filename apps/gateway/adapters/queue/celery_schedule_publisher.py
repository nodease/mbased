from __future__ import annotations

from typing import Any

from apps.gateway.application.deployment.schedule_models import (
    SchedulePublishRequest,
)
from apps.shared.services.workflow_task_publisher import send_workflow_task


class CeleryScheduleTaskPublisher:
    def __init__(self, celery_app: Any) -> None:
        self.celery_app = celery_app

    def publish(self, request: SchedulePublishRequest) -> None:
        send_workflow_task(
            self.celery_app,
            "workflow.execute_scheduled_deployment",
            args=[str(request.claim_id)],
            task_id=request.task_id,
            retry=False,
            ignore_result=True,
        )
