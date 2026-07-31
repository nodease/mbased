from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from apps.gateway.application.access_management.models import (
    AccessActionCommand,
    AppliedMutationDescriptor,
)
from apps.shared.audit.context import get_current_metadata
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.domain.policy_reason import ACCESS_MANAGEMENT_POLICY_REASONS
from apps.shared.services.permission_audit import record_resource_permission_denied


class SqlAlchemyAccessManagementAuditRecorder:
    def __init__(self, db: Session, *, actor: Any) -> None:
        self.db = db
        self.actor = actor

    def record_mutation(
        self,
        descriptor: AppliedMutationDescriptor,
        command: AccessActionCommand,
        reason: str | None,
    ) -> None:
        metadata = self._metadata(command.organization_id, reason)
        metadata["target_user_id"] = str(command.target_user_id)
        if descriptor.affected_resource_source_count is not None:
            metadata["affected_resource_source_count"] = (
                descriptor.affected_resource_source_count
            )
        self.db.add(
            AuditLog(
                action=descriptor.action,
                category=descriptor.category,
                actor_id=command.actor_id,
                actor_type="user",
                target_type=descriptor.target_type,
                target_id=str(descriptor.target_id),
                before=_json_safe(descriptor.before),
                after=_json_safe(descriptor.after),
                status="success",
                audit_metadata=metadata,
            )
        )

    def record_policy_block(
        self,
        command: AccessActionCommand,
        *,
        membership_id: uuid.UUID,
        policy_reason: str,
        reason: str | None,
    ) -> None:
        if policy_reason not in ACCESS_MANAGEMENT_POLICY_REASONS:
            raise ValueError("Unsupported access-management policy reason")
        metadata = self._policy_block_metadata(command.organization_id, reason)
        metadata.update(
            {
                "target_user_id": str(command.target_user_id),
                "requested_action": command.action,
                "policy_reason": policy_reason,
            }
        )
        if command.resource_type is not None and command.resource_id is not None:
            metadata["resource_type"] = command.resource_type
            metadata["resource_id"] = str(command.resource_id)
        if command.team_id is not None:
            metadata["team_id"] = str(command.team_id)
        self.db.add(
            AuditLog(
                action="policy.block",
                category="action",
                actor_id=command.actor_id,
                actor_type="user",
                target_type="organization_membership",
                target_id=str(membership_id),
                before=None,
                after=None,
                status="failure",
                audit_metadata=metadata,
            )
        )

    def _policy_block_metadata(
        self,
        organization_id: uuid.UUID,
        reason: str | None,
    ) -> dict[str, Any]:
        metadata = self._metadata(organization_id, reason)
        for key in ("ip", "user_agent"):
            metadata.pop(key, None)
        metadata["actor"] = {"id": str(self.actor.id)}
        return metadata

    def _metadata(
        self,
        organization_id: uuid.UUID,
        reason: str | None,
    ) -> dict[str, Any]:
        metadata = get_current_metadata()
        metadata["organization_id"] = str(organization_id)
        metadata["actor"] = {
            "id": str(self.actor.id),
            "email": getattr(self.actor, "email", None),
            "name": getattr(self.actor, "name", None),
        }
        if reason is not None:
            metadata["reason"] = reason
        return _json_safe(metadata)


class AccessManagementPermissionDenialRecorder:
    def record_permission_denied(
        self,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> None:
        record_resource_permission_denied(
            user_id=actor_id,
            resource_type="organization",
            resource_id=organization_id,
            action="manage_members",
            effective_auth_state="member",
            organization_id=organization_id,
        )


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
