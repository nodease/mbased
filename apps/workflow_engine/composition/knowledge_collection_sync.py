from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from apps.workflow_engine.application.knowledge_collection_sync import (
    ExecuteKnowledgeCollectionSync,
    RecoverKnowledgeCollectionSyncJobs,
    WorkerSyncAuditPort,
    WorkerSyncAuthorizationPort,
    WorkerSyncDocumentPort,
    WorkerSyncPublisherPort,
    WorkerSyncRepositoryPort,
    WorkerSyncUnitOfWorkPort,
)


@dataclass(frozen=True, slots=True)
class KnowledgeCollectionSyncDependencies:
    authorization: WorkerSyncAuthorizationPort
    repository: WorkerSyncRepositoryPort
    document: WorkerSyncDocumentPort
    audit: WorkerSyncAuditPort
    publisher: WorkerSyncPublisherPort
    unit_of_work: WorkerSyncUnitOfWorkPort


def build_knowledge_collection_sync_dependencies(
    db: Session,
) -> KnowledgeCollectionSyncDependencies:
    from apps.workflow_engine.adapters.knowledge_collection_sync_audit import (
        SqlAlchemyKnowledgeCollectionSyncAudit,
    )
    from apps.workflow_engine.adapters.knowledge_collection_sync_document import (
        SqlAlchemyKnowledgeCollectionSyncDocument,
    )
    from apps.workflow_engine.adapters.knowledge_collection_sync_publisher import (
        CeleryKnowledgeCollectionSyncPublisher,
    )
    from apps.workflow_engine.adapters.knowledge_collection_sync_repository import (
        SqlAlchemyWorkerSyncAuthorization,
        SqlAlchemyWorkerSyncRepository,
        SqlAlchemyWorkerSyncUnitOfWork,
    )

    return KnowledgeCollectionSyncDependencies(
        authorization=SqlAlchemyWorkerSyncAuthorization(db),
        repository=SqlAlchemyWorkerSyncRepository(db),
        document=SqlAlchemyKnowledgeCollectionSyncDocument(db),
        audit=SqlAlchemyKnowledgeCollectionSyncAudit(db),
        publisher=CeleryKnowledgeCollectionSyncPublisher(),
        unit_of_work=SqlAlchemyWorkerSyncUnitOfWork(db),
    )


def build_execute_knowledge_collection_sync(
    db: Session,
) -> ExecuteKnowledgeCollectionSync:
    dependencies = build_knowledge_collection_sync_dependencies(db)
    return ExecuteKnowledgeCollectionSync(
        authorization=dependencies.authorization,
        repository=dependencies.repository,
        document=dependencies.document,
        audit=dependencies.audit,
        publisher=dependencies.publisher,
        unit_of_work=dependencies.unit_of_work,
    )


def build_recover_knowledge_collection_sync_jobs(
    db: Session,
) -> RecoverKnowledgeCollectionSyncJobs:
    dependencies = build_knowledge_collection_sync_dependencies(db)
    return RecoverKnowledgeCollectionSyncJobs(
        repository=dependencies.repository,
        publisher=dependencies.publisher,
        unit_of_work=dependencies.unit_of_work,
    )
