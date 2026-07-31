from __future__ import annotations

from sqlalchemy.orm import Session

from apps.gateway.adapters.cache.knowledge_document_ingestion_progress import (
    RedisDocumentIngestionProgressProjection,
)
from apps.gateway.adapters.db.knowledge_document_ingestion_repository import (
    SqlAlchemyDocumentIngestionRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.adapters.queue.knowledge_document_ingestion_publisher import (
    CeleryKnowledgeDocumentIngestionPublisher,
)
from apps.gateway.application.knowledge_document_ingestion.use_cases import (
    DocumentIngestionUnitOfWorkPort,
    ReadDocumentIngestionStatus,
    RedriveDocumentIngestion,
    RequestKnowledgeBaseReindex,
    RequestDocumentIngestion,
)
from apps.shared.celery_app import celery_app


def build_request_document_ingestion(
    db: Session,
    *,
    unit_of_work: DocumentIngestionUnitOfWorkPort | None = None,
) -> RequestDocumentIngestion:
    return RequestDocumentIngestion(
        repository=SqlAlchemyDocumentIngestionRepository(db),
        publisher=CeleryKnowledgeDocumentIngestionPublisher(celery_app),
        unit_of_work=(
            unit_of_work if unit_of_work is not None else SqlAlchemyUnitOfWork(db)
        ),
        progress=RedisDocumentIngestionProgressProjection(),
    )


def build_request_knowledge_base_reindex(db: Session) -> RequestKnowledgeBaseReindex:
    return RequestKnowledgeBaseReindex(
        repository=SqlAlchemyDocumentIngestionRepository(db),
        publisher=CeleryKnowledgeDocumentIngestionPublisher(celery_app),
        unit_of_work=SqlAlchemyUnitOfWork(db),
        progress=RedisDocumentIngestionProgressProjection(),
    )


def build_read_document_ingestion_status(db: Session) -> ReadDocumentIngestionStatus:
    return ReadDocumentIngestionStatus(
        repository=SqlAlchemyDocumentIngestionRepository(db),
        unit_of_work=SqlAlchemyUnitOfWork(db),
    )


def build_redrive_document_ingestion(db: Session) -> RedriveDocumentIngestion:
    return RedriveDocumentIngestion(
        repository=SqlAlchemyDocumentIngestionRepository(db),
        publisher=CeleryKnowledgeDocumentIngestionPublisher(celery_app),
        unit_of_work=SqlAlchemyUnitOfWork(db),
        progress=RedisDocumentIngestionProgressProjection(),
    )
