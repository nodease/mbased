from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from apps.gateway.application.resource_permissions.mutation import (
    PermissionMutationCommand,
    PermissionMutationResult,
    audit_target_type,
)
from apps.gateway.services.audit_records import add_data_change_audit
from apps.shared.audit.context import get_current_metadata


class SqlAlchemyResourcePermissionMutationAudit:
    def __init__(self, db: Session, *, actor: Any) -> None:
        self.db = db
        self.actor = actor

    def record(
        self,
        command: PermissionMutationCommand,
        result: PermissionMutationResult,
    ) -> None:
        if result.status == "unchanged":
            raise ValueError("unchanged permission mutation must not be audited")
        target_type = audit_target_type(command)
        metadata = get_current_metadata()
        metadata["actor"] = {
            "id": str(self.actor.id),
            "email": getattr(self.actor, "email", None),
            "name": getattr(self.actor, "name", None),
        }
        add_data_change_audit(
            self.db,
            action=f"{target_type}.{result.status}",
            actor_id=command.actor_id,
            target_type=target_type,
            target_id=result.permission.permission_id,
            before=result.before,
            after=result.after,
            organization_id=command.organization_id,
            metadata=metadata,
        )
