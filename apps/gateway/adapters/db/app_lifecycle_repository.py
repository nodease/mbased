from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from apps.gateway.application.app_lifecycle.models import (
    ActiveResourceDeletion,
    AppDeletionCounts,
    DeletedWorkflowPermission,
    LockedApp,
    LockedWorkflow,
)
from apps.shared.audit.manual_ownership import register_manual_audit_ownership
from apps.shared.db.models.app import App
from apps.shared.db.models.llm_node_version import LLMNodeVersion
from apps.shared.db.models.mail_processing import (
    MailDraftEffect,
    MailMessageProcessing,
)
from apps.shared.db.models.model_routing_policy import LLMNodeModelRoutingPolicy
from apps.shared.db.models.schedule import Schedule
from apps.shared.db.models.schedule_dispatch import ScheduleDispatchClaim
from apps.shared.db.models.team import (
    TeamWorkflowPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_budget import WorkflowBudget
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.models.workflow_node_effect_attempt import (
    WorkflowNodeEffectAttempt,
)
from apps.shared.db.models.workflow_run import RunStatus, WorkflowRun
from apps.shared.services.permissions import (
    has_organization_manager_permission,
    has_organization_scope_access,
    has_workflow_permission,
)


class SqlAlchemyAppLifecycleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._app_row: App | None = None
        self._workflow_rows: tuple[Workflow, ...] = ()

    def lock_app(self, app_id: UUID) -> LockedApp | None:
        row = (
            self.db.query(App)
            .filter(App.id == app_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        self._app_row = row
        if row is None:
            return None
        return LockedApp(
            id=row.id,
            organization_id=row.organization_id,
            primary_workflow_id=row.workflow_id,
            created_by=row.created_by,
        )

    def lock_connected_workflows(
        self,
        app: LockedApp,
        *,
        limit: int,
    ) -> tuple[LockedWorkflow, ...]:
        predicate = Workflow.app_id == app.id
        if app.primary_workflow_id is not None:
            predicate = or_(predicate, Workflow.id == app.primary_workflow_id)

        rows = tuple(
            self.db.query(Workflow)
            .filter(predicate)
            .order_by(Workflow.id.asc())
            .limit(limit)
            .populate_existing()
            .with_for_update()
            .all()
        )
        self._workflow_rows = rows
        return tuple(
            LockedWorkflow(
                id=row.id,
                organization_id=row.organization_id,
                app_id=row.app_id,
            )
            for row in rows
        )

    def has_active_blocker(
        self,
        app: LockedApp,
        workflows: tuple[LockedWorkflow, ...],
    ) -> bool:
        workflow_ids = tuple(workflow.id for workflow in workflows)
        deployment_ids = tuple(
            row[0]
            for row in self.db.query(WorkflowDeployment.id)
            .filter(WorkflowDeployment.app_id == app.id)
            .all()
        )

        if workflow_ids and (
            self.db.query(WorkflowRun.id)
            .filter(
                WorkflowRun.workflow_id.in_(workflow_ids),
                WorkflowRun.status == RunStatus.RUNNING,
            )
            .first()
            is not None
        ):
            return True

        if deployment_ids and (
            self.db.query(ScheduleDispatchClaim.id)
            .filter(
                ScheduleDispatchClaim.deployment_id.in_(deployment_ids),
                or_(
                    ScheduleDispatchClaim.status.in_(
                        ("pending", "dispatching", "enqueued", "running")
                    ),
                    (
                        (ScheduleDispatchClaim.status == "dead_lettered")
                        & (
                            ScheduleDispatchClaim.safe_reason_code
                            == "execution_outcome_unknown"
                        )
                        & ScheduleDispatchClaim.outcome_reviewed_at.is_(None)
                    ),
                ),
            )
            .first()
            is not None
        ):
            return True

        effect_scope = WorkflowNodeEffectAttempt.app_id == app.id
        if workflow_ids:
            effect_scope = or_(
                effect_scope,
                WorkflowNodeEffectAttempt.workflow_id.in_(workflow_ids),
            )
        if (
            self.db.query(WorkflowNodeEffectAttempt.id)
            .filter(
                effect_scope,
                or_(
                    WorkflowNodeEffectAttempt.status.in_(("prepared", "in_flight")),
                    (
                        (WorkflowNodeEffectAttempt.status == "terminal")
                        & (
                            WorkflowNodeEffectAttempt.outcome
                            == "effect_outcome_unknown"
                        )
                        & (
                            WorkflowNodeEffectAttempt.replay_decision
                            == "replay_same_key"
                        )
                    ),
                ),
            )
            .first()
            is not None
        ):
            return True

        if workflow_ids and (
            self.db.query(MailMessageProcessing.id)
            .filter(
                MailMessageProcessing.workflow_id.in_(workflow_ids),
                MailMessageProcessing.status.in_(
                    ("pending", "processing", "ack_pending", "outcome_unknown")
                ),
            )
            .first()
            is not None
        ):
            return True

        return bool(
            workflow_ids
            and self.db.query(MailDraftEffect.id)
            .join(
                MailMessageProcessing,
                MailMessageProcessing.id == MailDraftEffect.processing_id,
            )
            .filter(
                MailMessageProcessing.workflow_id.in_(workflow_ids),
                MailDraftEffect.status.in_(
                    (
                        "pending",
                        "claimed",
                        "failed_before_effect",
                        "outcome_unknown",
                    )
                ),
            )
            .first()
            is not None
        )

    def delete_active_resources(
        self,
        app: LockedApp,
        workflows: tuple[LockedWorkflow, ...],
    ) -> ActiveResourceDeletion:
        self._assert_locked_rows(app, workflows)
        workflow_ids = tuple(workflow.id for workflow in workflows)

        deployments = tuple(
            self.db.query(WorkflowDeployment)
            .filter(WorkflowDeployment.app_id == app.id)
            .all()
        )
        deployment_ids = tuple(row.id for row in deployments)
        schedules = self._rows_for_ids(Schedule, Schedule.deployment_id, deployment_ids)
        routing_policies = self._routing_policies(workflow_ids, deployment_ids)
        team_permissions = self._rows_for_ids(
            TeamWorkflowPermission,
            TeamWorkflowPermission.workflow_id,
            workflow_ids,
        )
        user_permissions = self._rows_for_ids(
            UserWorkflowPermission,
            UserWorkflowPermission.workflow_id,
            workflow_ids,
        )
        budgets = self._rows_for_ids(
            WorkflowBudget,
            WorkflowBudget.workflow_id,
            workflow_ids,
        )
        llm_node_versions = tuple(
            self.db.query(LLMNodeVersion)
            .filter(LLMNodeVersion.app_id == app.id)
            .all()
        )
        deleted_permissions = tuple(
            DeletedWorkflowPermission(
                id=row.id,
                subject_type="team",
                organization_id=app.organization_id,
                workflow_id=row.workflow_id,
                subject_id=row.team_id,
            )
            for row in team_permissions
        ) + tuple(
            DeletedWorkflowPermission(
                id=row.id,
                subject_type="user",
                organization_id=app.organization_id,
                workflow_id=row.workflow_id,
                subject_id=row.user_id,
            )
            for row in user_permissions
        )

        for permission in (*team_permissions, *user_permissions):
            register_manual_audit_ownership(self.db, permission, "deleted")

        # App.workflow_id와 Workflow.app_id가 서로를 가리키므로 먼저 한쪽을 끊는다.
        # flush는 같은 트랜잭션 안에 있어 이후 실패 시 함께 rollback된다.
        self._app_row.workflow_id = None
        self.db.flush()

        for rows in (
            schedules,
            routing_policies,
            team_permissions,
            user_permissions,
            budgets,
            llm_node_versions,
        ):
            for row in rows:
                self.db.delete(row)

        # Flush lifecycle children before their DB-cascading parents. Otherwise
        # PostgreSQL can remove a child first and SQLAlchemy may issue the same
        # DELETE later, producing a misleading zero-row warning.
        self.db.flush()
        for deployment in deployments:
            self.db.delete(deployment)
        self.db.flush()
        for workflow in self._workflow_rows:
            self.db.delete(workflow)
        self.db.flush()
        self.db.delete(self._app_row)

        return ActiveResourceDeletion(
            counts=AppDeletionCounts(
                permissions=len(team_permissions) + len(user_permissions),
                budgets=len(budgets),
                deployments=len(deployments),
                schedules=len(schedules),
                active_routing_policies=len(routing_policies),
                llm_node_versions=len(llm_node_versions),
            ),
            permissions=deleted_permissions,
        )

    def _assert_locked_rows(
        self,
        app: LockedApp,
        workflows: tuple[LockedWorkflow, ...],
    ) -> None:
        if self._app_row is None or self._app_row.id != app.id:
            raise RuntimeError("app lifecycle delete requires a locked app")
        if tuple(row.id for row in self._workflow_rows) != tuple(
            workflow.id for workflow in workflows
        ):
            raise RuntimeError("app lifecycle delete requires locked workflows")

    def _routing_policies(
        self,
        workflow_ids: tuple[UUID, ...],
        deployment_ids: tuple[UUID, ...],
    ) -> tuple[LLMNodeModelRoutingPolicy, ...]:
        predicates = []
        if workflow_ids:
            predicates.append(LLMNodeModelRoutingPolicy.workflow_id.in_(workflow_ids))
        if deployment_ids:
            predicates.append(
                LLMNodeModelRoutingPolicy.deployment_id.in_(deployment_ids)
            )
        if not predicates:
            return ()
        return tuple(
            self.db.query(LLMNodeModelRoutingPolicy)
            .filter(or_(*predicates))
            .all()
        )

    def _rows_for_ids(self, model, column, values: tuple[UUID, ...]) -> tuple:
        if not values:
            return ()
        return tuple(self.db.query(model).filter(column.in_(values)).all())


class SqlAlchemyAppLifecycleAuthorization:
    def __init__(self, db: Session) -> None:
        self.db = db

    def can_manage(self, actor_id: UUID, app: LockedApp) -> bool:
        if app.organization_id is not None:
            if has_organization_manager_permission(
                self.db,
                actor_id,
                app.organization_id,
            ):
                return True
            if app.primary_workflow_id is not None:
                return has_workflow_permission(
                    self.db,
                    actor_id,
                    app.primary_workflow_id,
                    "manage",
                    organization_id=app.organization_id,
                )
            return False
        return app.created_by == actor_id

    def has_organization_scope(self, actor_id: UUID, app: LockedApp) -> bool:
        if app.organization_id is None:
            return app.created_by == actor_id
        return has_organization_scope_access(
            self.db,
            actor_id,
            app.organization_id,
        )
