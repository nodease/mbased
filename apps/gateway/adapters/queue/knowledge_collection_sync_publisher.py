from __future__ import annotations

import uuid
from typing import Any

from apps.shared.services.workflow_task_publisher import send_workflow_task


class CeleryCollectionSyncPublisher:
    def __init__(self, celery_app: Any) -> None:
        self.celery_app = celery_app

    def publish(self, job_id: uuid.UUID) -> None:
        send_workflow_task(
            self.celery_app,
            "workflow.knowledge_collection_sync.execute",
            args=[str(job_id)],
        )
