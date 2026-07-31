from __future__ import annotations

from sqlalchemy.orm import Session

from apps.gateway.adapters.cache.knowledge_document_ingestion_progress import (
    RedisDocumentIngestionProgressProjection,
)
from apps.gateway.adapters.db.knowledge_document_ingestion_repository import (
    SqlAlchemyDocumentIngestionRepository,
    SqlAlchemyWorkerDocumentIngestionAuthorization,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.adapters.queue.knowledge_document_ingestion_publisher import (
    CeleryKnowledgeDocumentIngestionPublisher,
)
from apps.gateway.application.knowledge_document_ingestion.worker import (
    ExecuteDocumentIngestionJob,
    RecoverDocumentIngestionJobs,
)
from apps.gateway.services.ingestion.job_runner import (
    KnowledgeDocumentIngestionJobRunner,
)
from apps.shared.celery_app import celery_app
from apps.shared.db.session import SessionLocal


def build_execute_document_ingestion_job(
    db: Session,
) -> ExecuteDocumentIngestionJob:
    return ExecuteDocumentIngestionJob(
        repository=SqlAlchemyDocumentIngestionRepository(db),
        authorization=SqlAlchemyWorkerDocumentIngestionAuthorization(db),
        runner=KnowledgeDocumentIngestionJobRunner(SessionLocal),
        unit_of_work=SqlAlchemyUnitOfWork(db),
        progress=RedisDocumentIngestionProgressProjection(),
    )


def build_recover_document_ingestion_jobs(
    db: Session,
) -> RecoverDocumentIngestionJobs:
    return RecoverDocumentIngestionJobs(
        repository=SqlAlchemyDocumentIngestionRepository(db),
        publisher=CeleryKnowledgeDocumentIngestionPublisher(celery_app),
        unit_of_work=SqlAlchemyUnitOfWork(db),
        progress=RedisDocumentIngestionProgressProjection(),
    )
