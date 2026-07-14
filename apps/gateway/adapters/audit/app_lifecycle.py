from __future__ import annotations

from sqlalchemy.orm import Session

from apps.gateway.application.app_lifecycle.models import (
    AppDeletionCounts,
    DeleteAppCommand,
    LockedApp,
    LockedWorkflow,
)
from apps.gateway.services.audit_records import add_action_audit
from apps.shared.audit.actions import AuditAction


class SqlAlchemyAppLifecycleAuditAdapter:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record_delete(
        self,
        command: DeleteAppCommand,
        app: LockedApp,
        workflows: tuple[LockedWorkflow, ...],
        counts: AppDeletionCounts,
    ) -> None:
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
