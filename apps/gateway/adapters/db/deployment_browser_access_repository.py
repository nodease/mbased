from __future__ import annotations

import copy
import uuid

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from apps.gateway.application.deployment.browser_access_models import (
    ActiveBrowserAccessSnapshot,
    BrowserAccessPolicy,
    BrowserAccessRevision,
    BrowserAccessSourceSnapshot,
)
from apps.shared.audit.manual_ownership import register_manual_audit_ownership
from apps.shared.db.models.app import App
from apps.shared.db.models.schedule import Schedule
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment


class SqlAlchemyDeploymentBrowserAccessRepository:
    def __init__(self, db: Session, *, scheduler_service=None) -> None:
        self.db = db
        self.scheduler_service = scheduler_service
        self._locked_app: App | None = None
        self._locked_source_id: uuid.UUID | None = None

    def lock_source(
        self,
        source_deployment_id: uuid.UUID,
    ) -> BrowserAccessSourceSnapshot | None:
        initial_source = (
            self.db.query(WorkflowDeployment)
            .filter(WorkflowDeployment.id == source_deployment_id)
            .first()
        )
        if initial_source is None:
            return None
        app = (
            self.db.query(App)
            .filter(App.id == initial_source.app_id)
            .with_for_update()
            .first()
        )
        if app is None or app.workflow_id is None:
            return None
        source = (
            self.db.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.id == source_deployment_id,
                WorkflowDeployment.app_id == app.id,
            )
            .with_for_update()
            .first()
        )
        if source is None:
            return None
        workflow = (
            self.db.query(Workflow)
            .filter(Workflow.id == app.workflow_id)
            .first()
        )
        if workflow is None or (
            app.organization_id is not None
            and workflow.organization_id != app.organization_id
        ):
            return None

        self._locked_app = app
        self._locked_source_id = source.id
        return BrowserAccessSourceSnapshot(
            id=source.id,
            app_id=source.app_id,
            workflow_id=workflow.id,
            organization_id=workflow.organization_id,
            version=source.version,
            deployment_type=source.type.value,
            graph_snapshot=source.graph_snapshot,
            config=source.config,
            input_schema=source.input_schema,
            output_schema=source.output_schema,
            description=source.description,
            url_slug=app.url_slug,
        )

    def create_revision(
        self,
        source: BrowserAccessSourceSnapshot,
        *,
        actor_id: uuid.UUID,
        policy: BrowserAccessPolicy,
        is_active: bool,
    ) -> BrowserAccessRevision:
        app = self._locked_app
        if app is None or self._locked_source_id != source.id:
            raise RuntimeError("App and source deployment must be locked")

        max_version = (
            self.db.query(func.max(WorkflowDeployment.version))
            .filter(WorkflowDeployment.app_id == source.app_id)
            .scalar()
            or 0
        )
        row = WorkflowDeployment(
            app_id=source.app_id,
            version=max_version + 1,
            type=DeploymentType(source.deployment_type),
            graph_snapshot=copy.deepcopy(source.graph_snapshot),
            config=copy.deepcopy(source.config),
            input_schema=copy.deepcopy(source.input_schema),
            output_schema=copy.deepcopy(source.output_schema),
            description=source.description,
            created_by=actor_id,
            is_active=is_active,
            browser_access_policy=copy.deepcopy(policy.to_dict()),
        )
        self.db.add(row)
        register_manual_audit_ownership(self.db, row, "created")
        self.db.flush()

        if is_active:
            self._deactivate_other_deployments(source.app_id, row.id)
            app.active_deployment_id = row.id
            register_manual_audit_ownership(self.db, app, "updated")

        return BrowserAccessRevision(
            id=row.id,
            app_id=row.app_id,
            version=row.version,
            deployment_type=row.type.value,
            graph_snapshot=row.graph_snapshot,
            config=row.config,
            input_schema=row.input_schema,
            output_schema=row.output_schema,
            description=row.description,
            created_by=row.created_by,
            created_at=row.created_at,
            is_active=row.is_active,
            browser_access_policy=row.browser_access_policy,
            url_slug=app.url_slug,
        )

    def get_active_by_slug(
        self,
        url_slug: str,
    ) -> ActiveBrowserAccessSnapshot | None:
        deployment = (
            self.db.query(
                WorkflowDeployment.version,
                WorkflowDeployment.type,
                WorkflowDeployment.browser_access_policy,
            )
            .join(
                App,
                and_(
                    WorkflowDeployment.app_id == App.id,
                    App.active_deployment_id == WorkflowDeployment.id,
                ),
            )
            .filter(
                App.url_slug == url_slug,
                App.active_deployment_id == WorkflowDeployment.id,
                WorkflowDeployment.is_active.is_(True),
                WorkflowDeployment.type.in_(
                    (DeploymentType.CHATBOT, DeploymentType.WIDGET)
                ),
            )
            .first()
        )
        if deployment is None:
            return None
        return ActiveBrowserAccessSnapshot(
            deployment_version=deployment.version,
            deployment_type=deployment.type.value,
            browser_access_policy=deployment.browser_access_policy,
        )

    def _deactivate_other_deployments(
        self,
        app_id: uuid.UUID,
        current_deployment_id: uuid.UUID,
    ) -> None:
        deployments = (
            self.db.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.app_id == app_id,
                WorkflowDeployment.id != current_deployment_id,
                WorkflowDeployment.is_active.is_(True),
            )
            .all()
        )
        for deployment in deployments:
            deployment.is_active = False
            register_manual_audit_ownership(self.db, deployment, "updated")
            if self.scheduler_service is None:
                continue
            schedule = (
                self.db.query(Schedule)
                .filter(Schedule.deployment_id == deployment.id)
                .first()
            )
            if schedule is not None:
                self.scheduler_service.remove_schedule(schedule.id)
