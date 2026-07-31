from __future__ import annotations

from typing import Any

from apps.shared.audit.actions import AuditAction
from apps.shared.audit.logger import record_audit


def build_resource_permission_denied_metadata(
    *,
    resource_type: str,
    resource_id: Any,
    action: str,
    effective_auth_state: str,
    organization_id: Any = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audit_metadata: dict[str, Any] = {
        "policy_result": "deny",
        "resource_type": resource_type,
        "resource_id": str(resource_id),
        "required_permission": action,
        "permission_action": action,
        "effective_auth_state": effective_auth_state,
    }
    if metadata:
        audit_metadata.update(metadata)
    if organization_id is not None:
        audit_metadata["organization_id"] = str(organization_id)
    return audit_metadata


def _record_resource_permission_denied(
    *,
    actor_id: Any,
    actor_type: str,
    resource_type: str,
    resource_id: Any,
    action: str,
    effective_auth_state: str,
    organization_id: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Resource permission 거부를 audit_logs.permission.denied로 기록한다."""

    record_audit(
        action=AuditAction.PERMISSION_DENIED,
        category="action",
        actor_id=actor_id,
        actor_type=actor_type,
        target_type=resource_type,
        target_id=resource_id,
        status="failure",
        metadata=build_resource_permission_denied_metadata(
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            effective_auth_state=effective_auth_state,
            organization_id=organization_id,
            metadata=metadata,
        ),
    )


def record_resource_permission_denied(
    *,
    user_id: Any,
    resource_type: str,
    resource_id: Any,
    action: str,
    effective_auth_state: str,
    organization_id: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """User resource permission denial을 user actor audit으로 기록한다."""

    _record_resource_permission_denied(
        actor_id=user_id,
        actor_type="user",
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        effective_auth_state=effective_auth_state,
        organization_id=organization_id,
        metadata=metadata,
    )


def record_system_resource_permission_denied(
    *,
    resource_type: str,
    resource_id: Any,
    action: str,
    effective_auth_state: str,
    organization_id: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """System execution denial을 synthetic user 없이 기록한다."""

    _record_resource_permission_denied(
        actor_id=None,
        actor_type="system",
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        effective_auth_state=effective_auth_state,
        organization_id=organization_id,
        metadata=metadata,
    )


def record_public_resource_permission_denied(
    *,
    resource_type: str,
    resource_id: Any,
    action: str,
    effective_auth_state: str,
    organization_id: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Public execution denial을 synthetic user 없이 기록한다."""

    _record_resource_permission_denied(
        actor_id=None,
        actor_type="public",
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        effective_auth_state=effective_auth_state,
        organization_id=organization_id,
        metadata=metadata,
    )
