import inspect

from apps.gateway.application.deployment.schedule_dispatch import (
    ScheduleDispatchUseCase,
)
from apps.gateway.composition.deployment import (
    build_schedule_dispatch_dependencies,
)
from apps.gateway.services.scheduler_service import SchedulerService


def test_schedule_dispatch_sqlalchemy_wiring_lives_in_composition_root():
    session = object()

    dependencies = build_schedule_dispatch_dependencies(session)
    service_source = inspect.getsource(SchedulerService)

    assert dependencies.repository.db is session
    assert dependencies.audit.db is session
    assert dependencies.budget.db is session
    assert dependencies.uow.db is session
    assert "SqlAlchemyScheduleDispatchRepository" not in service_source
    assert "SqlAlchemyScheduleDispatchAuditRecorder" not in service_source
    assert "WorkflowBudgetDecisionAdapter" not in service_source
    assert "CeleryScheduleTaskPublisher" not in service_source
    assert "apps.gateway.composition" not in service_source
    assert "select(" not in service_source
    assert "func." not in service_source
    assert "schedule_dispatch_observability" not in inspect.getsource(
        ScheduleDispatchUseCase
    )
