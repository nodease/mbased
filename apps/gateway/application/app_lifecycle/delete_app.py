from __future__ import annotations

from .errors import (
    AppDeleteInProgress,
    AppDeleteRequiresRepair,
    AppPermissionDenied,
    AppResourceHidden,
)
from .models import DeleteAppCommand, DeleteAppResult, LockedApp, LockedWorkflow
from .ports import (
    AppLifecycleAuditPort,
    AppLifecycleAuthorizationPort,
    AppLifecycleRepositoryPort,
    UnitOfWorkPort,
)


MAX_CONNECTED_WORKFLOWS = 100


class DeleteApp:
    def __init__(
        self,
        repository: AppLifecycleRepositoryPort,
        authorization: AppLifecycleAuthorizationPort,
        audit: AppLifecycleAuditPort,
        unit_of_work: UnitOfWorkPort,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.audit = audit
        self.unit_of_work = unit_of_work

    def execute(self, command: DeleteAppCommand) -> DeleteAppResult:
        try:
            app = self.repository.lock_app(command.app_id)
            if app is None or (
                command.organization_id is not None
                and app.organization_id != command.organization_id
            ):
                raise AppResourceHidden()

            workflows = self.repository.lock_connected_workflows(
                app,
                limit=MAX_CONNECTED_WORKFLOWS + 1,
            )
            if not self.authorization.can_manage(command.actor_id, app):
                if self.authorization.has_organization_scope(command.actor_id, app):
                    raise AppPermissionDenied()
                raise AppResourceHidden()

            self._validate_relationships(app, workflows)
            if self.repository.has_active_blocker(app, workflows):
                raise AppDeleteInProgress()

            deletion = self.repository.delete_active_resources(app, workflows)
            self.audit.record_delete(command, app, workflows, deletion)
            self.unit_of_work.flush()
            self.unit_of_work.commit()
        except Exception:
            self.unit_of_work.rollback()
            raise

        return DeleteAppResult(
            app_id=app.id,
            workflow_ids=tuple(workflow.id for workflow in workflows),
            counts=deletion.counts,
        )

    @staticmethod
    def _validate_relationships(
        app: LockedApp,
        workflows: tuple[LockedWorkflow, ...],
    ) -> None:
        if len(workflows) > MAX_CONNECTED_WORKFLOWS:
            raise AppDeleteRequiresRepair()

        workflow_ids = {workflow.id for workflow in workflows}
        if (
            app.primary_workflow_id is not None
            and app.primary_workflow_id not in workflow_ids
        ):
            raise AppDeleteRequiresRepair()

        if any(
            workflow.app_id != app.id
            or workflow.organization_id != app.organization_id
            for workflow in workflows
        ):
            raise AppDeleteRequiresRepair()
