from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from apps.gateway.adapters.audit.knowledge_collection_sync import (
    SqlAlchemyCollectionSyncAudit,
)
from apps.gateway.adapters.db.knowledge_collection_sync_repository import (
    SqlAlchemyCollectionSyncAuthorization,
    SqlAlchemyCollectionSyncRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.adapters.queue.knowledge_collection_sync_publisher import (
    CeleryCollectionSyncPublisher,
)
from apps.gateway.application.knowledge_collection_sync.use_cases import (
    ReadKnowledgeCollectionSyncStatus,
    RequestKnowledgeCollectionSync,
)
from apps.shared.celery_app import celery_app


@dataclass(frozen=True, slots=True)
class KnowledgeCollectionSyncUseCases:
    request: RequestKnowledgeCollectionSync
    read: ReadKnowledgeCollectionSyncStatus


def build_knowledge_collection_sync_use_cases(
    db: Session,
) -> KnowledgeCollectionSyncUseCases:
    authorization = SqlAlchemyCollectionSyncAuthorization(db)
    repository = SqlAlchemyCollectionSyncRepository(db)
    uow = SqlAlchemyUnitOfWork(db)
    return KnowledgeCollectionSyncUseCases(
        request=RequestKnowledgeCollectionSync(
            authorization=authorization,
            repository=repository,
            audit=SqlAlchemyCollectionSyncAudit(db),
            publisher=CeleryCollectionSyncPublisher(celery_app),
            unit_of_work=uow,
        ),
        read=ReadKnowledgeCollectionSyncStatus(
            authorization=authorization,
            repository=repository,
            unit_of_work=uow,
        ),
    )
