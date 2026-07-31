from __future__ import annotations

import uuid

from apps.gateway.composition.knowledge_document_ingestion_worker import (
    build_execute_document_ingestion_job,
    build_recover_document_ingestion_jobs,
)
from apps.shared.celery_app import celery_app
from apps.shared.db.session import SessionLocal


@celery_app.task(
    bind=True,
    name="knowledge.document_ingestion.execute",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,
    soft_time_limit=840,
    time_limit=900,
)
def execute_document_ingestion(self, job_id: str):
    try:
        canonical_job_id = uuid.UUID(str(job_id))
    except (TypeError, ValueError, AttributeError):
        return {"status": "invalid_job_id"}

    db = SessionLocal()
    try:
        result = build_execute_document_ingestion_job(db).execute(
            canonical_job_id,
            owner_token=str(uuid.uuid4()),
        )
        return {"status": result.status, "reason_code": result.reason_code}
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="knowledge.document_ingestion.recover",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,
    soft_time_limit=50,
    time_limit=60,
)
def recover_document_ingestion_jobs(self):
    db = SessionLocal()
    try:
        return build_recover_document_ingestion_jobs(db).execute()
    finally:
        db.close()
