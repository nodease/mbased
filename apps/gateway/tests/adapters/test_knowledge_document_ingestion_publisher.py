import uuid
from unittest.mock import Mock

import pytest

from apps.gateway.adapters.queue.knowledge_document_ingestion_publisher import (
    CeleryKnowledgeDocumentIngestionPublisher,
    KnowledgeDocumentIngestionPublishError,
)


def test_publisher_uses_job_only_payload_and_deterministic_task_id() -> None:
    celery_app = Mock()
    job_id = uuid.uuid4()

    CeleryKnowledgeDocumentIngestionPublisher(celery_app).publish(job_id)

    celery_app.send_task.assert_called_once_with(
        "knowledge.document_ingestion.execute",
        args=[str(job_id)],
        queue="knowledge",
        task_id=f"knowledge-document-ingestion-{job_id}",
        argsrepr="[knowledge ingestion job id]",
        kwargsrepr="{}",
    )


def test_publisher_redacts_broker_failure() -> None:
    celery_app = Mock()
    celery_app.send_task.side_effect = RuntimeError("sensitive broker detail")

    with pytest.raises(KnowledgeDocumentIngestionPublishError) as exc_info:
        CeleryKnowledgeDocumentIngestionPublisher(celery_app).publish(uuid.uuid4())

    assert "sensitive broker detail" not in str(exc_info.value)
