from sqlalchemy.orm import Session

from apps.gateway.adapters.audit.app_lifecycle import (
    SqlAlchemyAppLifecycleAuditAdapter,
)
from apps.gateway.adapters.db.app_lifecycle_repository import (
    SqlAlchemyAppLifecycleAuthorization,
    SqlAlchemyAppLifecycleRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.application.app_lifecycle.delete_app import DeleteApp


def build_delete_app_use_case(db: Session) -> DeleteApp:
    return DeleteApp(
        repository=SqlAlchemyAppLifecycleRepository(db),
        authorization=SqlAlchemyAppLifecycleAuthorization(db),
        audit=SqlAlchemyAppLifecycleAuditAdapter(db),
        unit_of_work=SqlAlchemyUnitOfWork(db),
    )
