import inspect

from apps.workflow_engine import tasks
from apps.workflow_engine.application.schedule_dispatch import (
    ScheduledDeploymentExecutionUseCase,
)
from apps.workflow_engine.composition.schedule_dispatch import (
    build_schedule_admission_dependencies,
)


def test_schedule_admission_sqlalchemy_wiring_lives_in_composition_root():
    session = object()

    dependencies = build_schedule_admission_dependencies(session)
    task_source = inspect.getsource(tasks._execute_scheduled_deployment_claim)

    assert dependencies.repository.db is session
    assert dependencies.budget.db is session
    assert hasattr(dependencies.configuration_preflight, "is_ready")
    assert dependencies.audit.db is session
    assert dependencies.uow.db is session
    assert "SqlAlchemyScheduleAdmissionRepository" not in task_source
    assert "SharedWorkflowBudgetDecisionAdapter" not in task_source
    assert "SqlAlchemyScheduleAdmissionAuditRecorder" not in task_source
    assert "schedule_dispatch_observability" not in inspect.getsource(
        ScheduledDeploymentExecutionUseCase
    )
