from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.logger import record_audit
from apps.shared.db.models.llm import LLMCredential
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.session import get_db
from apps.shared.services.permission_audit import (
    build_resource_permission_denied_metadata,
)
from apps.shared.services.permissions import (
    get_effective_llm_credential_auth_state,
    get_effective_workflow_auth_state,
    has_organization_scope_access,
    has_llm_credential_permission,
    has_workflow_permission,
)


def record_permission_denied(
    user: User,
    resource_type: str,
    resource_id: Any,
    action: str,
    effective_auth_state: str,
    organization_id: Any,
) -> None:
    record_audit(
        action=AuditAction.PERMISSION_DENIED,
        category="action",
        actor_id=user.id,
        actor_type="user",
        target_type=resource_type,
        target_id=resource_id,
        status="failure",
        metadata=build_resource_permission_denied_metadata(
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            effective_auth_state=effective_auth_state,
            organization_id=organization_id,
        ),
    )


def recorded_permission_denied_exception(detail: Any = "Forbidden") -> HTTPException:
    exc = HTTPException(status_code=403, detail=detail)
    setattr(exc, "audit_recorded", True)
    return exc


def ensure_workflow_permission(
    db: Session,
    current_user: User,
    workflow_id: Any,
    action: str,
) -> Workflow:
    try:
        workflow_uuid = UUID(str(workflow_id))
    except (TypeError, ValueError):
        # Workflow identity는 UUID다. Placeholder나 malformed 값은 DB UUID cast까지
        # 보내지 않고 존재하지 않는 리소스와 같은 응답으로 닫는다.
        raise HTTPException(status_code=404, detail="Workflow not found") from None

    workflow = db.query(Workflow).filter(Workflow.id == workflow_uuid).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if workflow.organization_id and not has_organization_scope_access(
        db, current_user.id, workflow.organization_id
    ):
        raise HTTPException(status_code=404, detail="Workflow not found")

    effective_auth_state = get_effective_workflow_auth_state(
        db,
        current_user.id,
        workflow.id,
        organization_id=workflow.organization_id,
    )
    if not has_workflow_permission(
        db,
        current_user.id,
        workflow.id,
        action,
        organization_id=workflow.organization_id,
    ):
        record_permission_denied(
            current_user,
            "workflow",
            workflow.id,
            action,
            effective_auth_state,
            workflow.organization_id,
        )
        raise recorded_permission_denied_exception()
    return workflow


def ensure_llm_credential_permission(
    db: Session,
    current_user: User,
    credential_id: Any,
    action: str,
    *,
    active_organization_id: Any = None,
) -> LLMCredential:
    filters = [LLMCredential.id == credential_id]
    if active_organization_id is not None:
        filters.append(LLMCredential.organization_id == active_organization_id)
    credential = db.query(LLMCredential).filter(*filters).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")

    permission_organization_id = (
        active_organization_id or credential.organization_id
    )
    if credential.organization_id and not has_organization_scope_access(
        db, current_user.id, credential.organization_id
    ):
        raise HTTPException(status_code=404, detail="Credential not found")

    effective_auth_state = get_effective_llm_credential_auth_state(
        db,
        current_user.id,
        credential.id,
        organization_id=permission_organization_id,
    )
    if not has_llm_credential_permission(
        db,
        current_user.id,
        credential.id,
        action,
        organization_id=permission_organization_id,
    ):
        record_permission_denied(
            current_user,
            "llm_credential",
            credential.id,
            action,
            effective_auth_state,
            permission_organization_id,
        )
        raise recorded_permission_denied_exception()
    return credential


def require_workflow_permission(action: str):
    def dependency(
        workflow_id: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> Workflow:
        return ensure_workflow_permission(db, current_user, workflow_id, action)

    return dependency


def require_llm_credential_permission(action: str):
    def dependency(
        credential_id: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> LLMCredential:
        return ensure_llm_credential_permission(db, current_user, credential_id, action)

    return dependency
