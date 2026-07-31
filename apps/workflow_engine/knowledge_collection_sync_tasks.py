from __future__ import annotations

import logging
import uuid

from apps.shared.celery_app import celery_app
from apps.shared.db.session import SessionLocal
from apps.shared.services.workflow_task_publisher import RedactedWorkflowTask
from apps.workflow_engine.composition.knowledge_collection_sync import (
    build_execute_knowledge_collection_sync,
    build_recover_knowledge_collection_sync_jobs,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workflow.knowledge_collection_sync.execute",
    bind=True,
    max_retries=0,
    base=RedactedWorkflowTask,
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_knowledge_collection_sync(self, job_id: str):
    try:
        parsed_job_id = uuid.UUID(job_id)
    except (AttributeError, TypeError, ValueError):
        return {"status": "missing", "reason_code": None}

    session = SessionLocal()
    try:
        owner = str(self.request.id or uuid.uuid4())[:64]
        result = build_execute_knowledge_collection_sync(session).execute(
            parsed_job_id,
            owner=owner,
        )
        return {"status": result.status, "reason_code": result.reason_code}
    except Exception as exc:
        session.rollback()
        logger.error(
            "Knowledge Collection sync task failed: error_type=%s",
            type(exc).__name__,
        )
        return {"status": "failed", "reason_code": "sync.internal_error"}
    finally:
        session.close()


@celery_app.task(
    name="workflow.knowledge_collection_sync.recover",
    bind=True,
    max_retries=0,
    base=RedactedWorkflowTask,
    acks_late=True,
    reject_on_worker_lost=True,
)
def recover_knowledge_collection_sync_jobs(self):
    session = SessionLocal()
    try:
        return build_recover_knowledge_collection_sync_jobs(session).execute()
    except Exception as exc:
        session.rollback()
        logger.error(
            "Knowledge Collection sync recovery failed: error_type=%s",
            type(exc).__name__,
        )
        return {"recovered": 0, "published": 0, "deleted": 0}
    finally:
        session.close()
