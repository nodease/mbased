from __future__ import annotations

import uuid

from apps.shared.celery_app import celery_app
from apps.shared.services.workflow_task_publisher import send_workflow_task


class CeleryKnowledgeCollectionSyncPublisher:
    def publish(self, job_id: uuid.UUID) -> None:
        send_workflow_task(
            celery_app,
            "workflow.knowledge_collection_sync.execute",
            args=[str(job_id)],
        )
