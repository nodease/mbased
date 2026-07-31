from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from apps.shared.domain.deployment_runtime_policy import DeploymentRuntimePolicy
from apps.shared.domain.schedule_dispatch import ScheduleDispatchSettings
from apps.workflow_engine.application.schedule_dispatch import (
    BudgetDecisionPort,
    ScheduleAdmissionAuditPort,
    ScheduleAdmissionRepositoryPort,
    ScheduleConfigurationPreflightPort,
    UnitOfWorkPort,
)


@dataclass(frozen=True, slots=True)
class ScheduleAdmissionDependencies:
    repository: ScheduleAdmissionRepositoryPort
    budget: BudgetDecisionPort
    configuration_preflight: ScheduleConfigurationPreflightPort
    audit: ScheduleAdmissionAuditPort
    uow: UnitOfWorkPort


def build_schedule_admission_dependencies(
    db: Session,
) -> ScheduleAdmissionDependencies:
    from apps.workflow_engine.adapters.schedule_dispatch_audit import (
        SqlAlchemyScheduleAdmissionAuditRecorder,
    )
    from apps.workflow_engine.adapters.schedule_dispatch_repository import (
        SharedWorkflowBudgetDecisionAdapter,
        SqlAlchemyScheduleAdmissionRepository,
        SqlAlchemyScheduleAdmissionUnitOfWork,
    )
    from apps.workflow_engine.adapters.schedule_configuration_preflight import (
        ScheduleConfigurationPreflightAdapter,
    )

    return ScheduleAdmissionDependencies(
        repository=SqlAlchemyScheduleAdmissionRepository(db),
        budget=SharedWorkflowBudgetDecisionAdapter(db),
        configuration_preflight=ScheduleConfigurationPreflightAdapter(),
        audit=SqlAlchemyScheduleAdmissionAuditRecorder(db),
        uow=SqlAlchemyScheduleAdmissionUnitOfWork(db),
    )


def build_scheduled_execution_use_case(
    *,
    settings: ScheduleDispatchSettings,
    runtime_policy: DeploymentRuntimePolicy,
):
    from apps.workflow_engine.application.schedule_dispatch import (
        ScheduledDeploymentExecutionUseCase,
    )

    return ScheduledDeploymentExecutionUseCase(
        settings=settings,
        runtime_policy=runtime_policy,
    )


def build_scheduled_workflow_engine(**kwargs):
    from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine

    return WorkflowEngine(**kwargs)
