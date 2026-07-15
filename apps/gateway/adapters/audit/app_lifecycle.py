from __future__ import annotations

from sqlalchemy.orm import Session

from apps.gateway.application.app_lifecycle.models import (
    ActiveResourceDeletion,
    DeleteAppCommand,
    LockedApp,
    LockedWorkflow,
)
from apps.gateway.services.audit_records import add_action_audit, add_data_change_audit
from apps.shared.audit.actions import AuditAction


class SqlAlchemyAppLifecycleAuditAdapter:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record_delete(
        self,
        command: DeleteAppCommand,
        app: LockedApp,
        workflows: tuple[LockedWorkflow, ...],
        deletion: ActiveResourceDeletion,
    ) -> None:
        counts = deletion.counts
        self._record_permission_deletes(command, app, deletion)
        add_action_audit(
            self.db,
            AuditAction.APP_DELETE,
            command.actor_id,
            "app",
            app.id,
            organization_id=app.organization_id,
            metadata={
                "actor_id": str(command.actor_id),
                "app_id": str(app.id),
                "workflow_ids": [str(workflow.id) for workflow in workflows],
                "deleted_counts": {
                    "permissions": counts.permissions,
                    "budgets": counts.budgets,
                    "deployments": counts.deployments,
                    "schedules": counts.schedules,
                    "active_routing_policies": counts.active_routing_policies,
                    "llm_node_versions": counts.llm_node_versions,
                },
            },
        )

    def _record_permission_deletes(
        self,
        command: DeleteAppCommand,
        app: LockedApp,
        deletion: ActiveResourceDeletion,
    ) -> None:
        for permission in deletion.permissions:
            target_type = f"{permission.subject_type}_workflow_permission"
            subject_key = f"{permission.subject_type}_id"
            add_data_change_audit(
                self.db,
                action=f"{target_type}.deleted",
                actor_id=command.actor_id,
                target_type=target_type,
                target_id=permission.id,
                before={
                    "organization_id": permission.organization_id,
                    "workflow_id": permission.workflow_id,
                    subject_key: permission.subject_id,
                },
                after=None,
                organization_id=permission.organization_id,
                metadata={
                    "actor_id": command.actor_id,
                    "app_id": app.id,
                    "workflow_id": permission.workflow_id,
                },
            )
