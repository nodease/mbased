from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy.orm import Session

from apps.gateway.application.knowledge_administration.domain_permissions import (
    DomainPermissionCommand,
)
from apps.shared.db.models.audit_log import (
    ActorType,
    AuditCategory,
    AuditLog,
    AuditStatus,
)


class SqlAlchemyKnowledgeDomainPermissionAudit:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        *,
        command: DomainPermissionCommand,
        permission_id: uuid.UUID,
        operation: Literal["created", "updated", "deleted"],
    ) -> None:
        safe_state = {
            "permission_action": command.permission_action,
            "expires_at": (
                command.expires_at.isoformat()
                if command.expires_at is not None
                else None
            ),
        }
        self.db.add(
            AuditLog(
                action=f"{command.subject_type}_knowledge_domain_permission.{operation}",
                category=AuditCategory.DATA_CHANGE,
                actor_id=command.actor_id,
                actor_type=ActorType.USER,
                target_type=f"{command.subject_type}_knowledge_domain_permission",
                target_id=str(permission_id),
                before=safe_state if operation == "deleted" else None,
                after=safe_state if operation != "deleted" else None,
                status=AuditStatus.SUCCESS,
                audit_metadata={
                    "organization_id": str(command.organization_id),
                    "subject_type": command.subject_type,
                },
            )
        )
