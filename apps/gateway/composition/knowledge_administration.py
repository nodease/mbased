from sqlalchemy.orm import Session

from apps.gateway.adapters.audit.knowledge_collection_operations import (
    SqlAlchemyKnowledgeCollectionOperationAudit,
)
from apps.gateway.adapters.audit.knowledge_domain_permissions import (
    SqlAlchemyKnowledgeDomainPermissionAudit,
)
from apps.gateway.adapters.db.knowledge_collection_operations import (
    SqlAlchemyKnowledgeCollectionOperationAuthorization,
    SqlAlchemyKnowledgeCollectionOperationRepository,
)
from apps.gateway.adapters.db.knowledge_domain_permissions import (
    SqlAlchemyKnowledgeDomainAuthorization,
    SqlAlchemyKnowledgeDomainPermissionRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.application.knowledge_administration.domain_permissions import (
    KnowledgeDomainPermissionUseCase,
)
from apps.gateway.application.knowledge_administration.collection_operations import (
    CollectionLifecycleAndOrderUseCase,
)


def build_knowledge_domain_permission_use_case(
    db: Session,
) -> KnowledgeDomainPermissionUseCase:
    return KnowledgeDomainPermissionUseCase(
        SqlAlchemyKnowledgeDomainAuthorization(db),
        SqlAlchemyKnowledgeDomainPermissionRepository(db),
        SqlAlchemyKnowledgeDomainPermissionAudit(db),
        SqlAlchemyUnitOfWork(db),
    )


def build_knowledge_collection_lifecycle_and_order_use_case(
    db: Session,
) -> CollectionLifecycleAndOrderUseCase:
    return CollectionLifecycleAndOrderUseCase(
        SqlAlchemyKnowledgeCollectionOperationAuthorization(db),
        SqlAlchemyKnowledgeCollectionOperationRepository(db),
        SqlAlchemyKnowledgeCollectionOperationAudit(db),
        SqlAlchemyUnitOfWork(db),
    )
