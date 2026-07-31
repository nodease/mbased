from sqlalchemy.orm import Session

from apps.gateway.adapters.audit.resource_permission_mutations import (
    SqlAlchemyResourcePermissionMutationAudit,
)
from apps.gateway.adapters.db.resource_permission_mutations import (
    SqlAlchemyResourcePermissionMutationRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.application.resource_permissions.mutation import (
    ResourcePermissionMutationUseCase,
)


def build_resource_permission_mutation_use_case(
    db: Session,
    *,
    actor,
) -> ResourcePermissionMutationUseCase:
    return ResourcePermissionMutationUseCase(
        SqlAlchemyResourcePermissionMutationRepository(db),
        SqlAlchemyResourcePermissionMutationAudit(db, actor=actor),
        SqlAlchemyUnitOfWork(db),
    )
