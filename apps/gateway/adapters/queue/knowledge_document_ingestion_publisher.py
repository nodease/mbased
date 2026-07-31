from __future__ import annotations

import logging
import uuid
from typing import Any


logger = logging.getLogger(__name__)


class KnowledgeDocumentIngestionPublishError(RuntimeError):
    pass


class CeleryKnowledgeDocumentIngestionPublisher:
    def __init__(self, celery_app: Any) -> None:
        self.celery_app = celery_app

    def publish(self, job_id: uuid.UUID) -> None:
        try:
            self.celery_app.send_task(
                "knowledge.document_ingestion.execute",
                args=[str(job_id)],
                queue="knowledge",
                task_id=f"knowledge-document-ingestion-{job_id}",
                argsrepr="[knowledge ingestion job id]",
                kwargsrepr="{}",
            )
        except Exception as exc:
            logger.warning(
                "Knowledge document ingestion publish deferred: error_type=%s",
                type(exc).__name__,
            )
            raise KnowledgeDocumentIngestionPublishError() from None
