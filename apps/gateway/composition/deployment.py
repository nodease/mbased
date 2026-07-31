from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from apps.gateway.adapters.audit.deployment_preflight import (
    DeploymentPermissionDenialAuditRecorder,
)
from apps.gateway.adapters.audit.sqlalchemy_deployment_browser_access_audit import (
    SqlAlchemyDeploymentBrowserAccessAuditRecorder,
)
from apps.gateway.adapters.audit.sqlalchemy_schedule_dispatch_audit import (
    SqlAlchemyScheduleDispatchAuditRecorder,
)
from apps.gateway.adapters.deployment_browser_access_activation import (
    DeploymentBrowserAccessActivationGuard,
)
from apps.gateway.adapters.db.deployment_browser_access_repository import (
    SqlAlchemyDeploymentBrowserAccessRepository,
)
from apps.gateway.adapters.db.deployment_preflight_repository import (
    SqlAlchemyDeploymentPreflightRepository,
)
from apps.gateway.adapters.db.schedule_dispatch_repository import (
    SqlAlchemyScheduleDispatchRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.adapters.queue.celery_schedule_publisher import (
    CeleryScheduleTaskPublisher,
)
from apps.gateway.adapters.schedule.apscheduler_next_fire import (
    ApschedulerNextFireCalculator,
)
from apps.gateway.adapters.schedule.configuration_preflight import (
    ScheduleConfigurationPreflightAdapter,
)
from apps.gateway.application.deployment.browser_access_models import (
    BrowserAccessPolicy,
)
from apps.gateway.application.deployment.browser_access_policy import (
    normalize_browser_access_policy,
)
from apps.gateway.application.deployment.browser_access_use_cases import (
    CreateBrowserAccessRevision,
    GetPublicBrowserAccessPolicy,
)
from apps.gateway.application.deployment.models import NodeCatalogSnapshot
from apps.gateway.application.deployment.preflight import DeploymentPreflightUseCase
from apps.gateway.application.deployment.workflow_node_binding import (
    WorkflowNodeBindingUseCase,
)
from apps.gateway.core.config import settings
from apps.shared.services.workflow_node_catalog import (
    implemented_node_types,
    node_side_effect_mapping,
)
from apps.gateway.services.scheduler_service import ScheduleDispatchDependencies
from apps.gateway.services.workflow_budget_service import WorkflowBudgetDecisionAdapter


def deployment_browser_access_environment() -> str | None:
    return os.environ.get("NODE_ENV")


def normalize_deployment_browser_access_policy(
    deployment_type,
    policy,
) -> BrowserAccessPolicy | None:
    raw_policy = policy.model_dump(mode="python") if policy is not None else None
    return normalize_browser_access_policy(
        deployment_type,
        raw_policy,
        environment=deployment_browser_access_environment(),
    )


def build_browser_access_revision_use_case(
    db: Session,
    *,
    actor: Any,
    scheduler_service=None,
) -> CreateBrowserAccessRevision:
    return CreateBrowserAccessRevision(
        SqlAlchemyDeploymentBrowserAccessRepository(
            db,
            scheduler_service=scheduler_service,
        ),
        DeploymentBrowserAccessActivationGuard(
            db,
            preflight_factory=lambda source, actor_id: (
                build_deployment_preflight_use_case(
                    db,
                    organization_id=source.organization_id,
                    principal_id=actor_id,
                    candidate_graphs_by_app_id={
                        source.app_id: source.graph_snapshot
                    },
                    candidate_deployment_types_by_app_id={
                        source.app_id: source.deployment_type
                    },
                )
            ),
            require_public_chat_conversation_contract=(
                settings.PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE == "strict"
            ),
        ),
        SqlAlchemyDeploymentBrowserAccessAuditRecorder(db, actor=actor),
        SqlAlchemyUnitOfWork(db),
    )


def build_public_browser_access_use_case(
    db: Session,
) -> GetPublicBrowserAccessPolicy:
    return GetPublicBrowserAccessPolicy(
        SqlAlchemyDeploymentBrowserAccessRepository(db)
    )


def build_schedule_dispatch_dependencies(
    db: Session,
) -> ScheduleDispatchDependencies:
    preflight_repository = SqlAlchemyDeploymentPreflightRepository(db)
    return ScheduleDispatchDependencies(
        repository=SqlAlchemyScheduleDispatchRepository(db),
        audit=SqlAlchemyScheduleDispatchAuditRecorder(db),
        budget=WorkflowBudgetDecisionAdapter(db),
        configuration_preflight=ScheduleConfigurationPreflightAdapter(
            preflight_repository,
            node_catalog_by_type=_node_catalog_snapshots(),
        ),
        uow=SqlAlchemyUnitOfWork(db),
    )


def build_schedule_next_fire_calculator() -> ApschedulerNextFireCalculator:
    return ApschedulerNextFireCalculator()


def build_schedule_task_publisher() -> CeleryScheduleTaskPublisher:
    from apps.shared.celery_app import celery_app

    return CeleryScheduleTaskPublisher(celery_app)


def build_deployment_preflight_use_case(
    db: Session,
    *,
    organization_id: uuid.UUID | None,
    principal_id: uuid.UUID | None = None,
    candidate_graphs_by_app_id: Mapping[uuid.UUID, dict] | None = None,
    candidate_deployment_types_by_app_id: Mapping[uuid.UUID, str] | None = None,
) -> DeploymentPreflightUseCase:
    repository = SqlAlchemyDeploymentPreflightRepository(db)
    return DeploymentPreflightUseCase(
        repository,
        organization_id=organization_id,
        principal_id=principal_id,
        node_catalog_by_type=_node_catalog_snapshots(),
        candidate_graphs_by_app_id=candidate_graphs_by_app_id,
        candidate_deployment_types_by_app_id=candidate_deployment_types_by_app_id,
        permission_denial_audit=DeploymentPermissionDenialAuditRecorder(),
    )


def _node_catalog_snapshots() -> dict[str, NodeCatalogSnapshot]:
    implemented = implemented_node_types()
    return {
        node_type: NodeCatalogSnapshot(
            side_effect=side_effect,
            implemented=node_type in implemented,
        )
        for node_type, side_effect in node_side_effect_mapping().items()
    }


def build_workflow_node_binding_use_case(
    db: Session,
    *,
    organization_id: uuid.UUID | None,
) -> WorkflowNodeBindingUseCase:
    return WorkflowNodeBindingUseCase(
        SqlAlchemyDeploymentPreflightRepository(db),
        organization_id=organization_id,
        side_effect_by_node_type=node_side_effect_mapping(),
    )
