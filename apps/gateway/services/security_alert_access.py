from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from apps.gateway.services.organization_context import resolve_active_organization_id
from apps.gateway.utils.api_errors import raise_api_error
from apps.shared.audit import record_audit
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.security_alert import SecurityAlert
from apps.shared.services.permissions import has_organization_manager_permission


def resolve_security_alert_manager_organization(
    db: Session,
    request: Request,
    raw_organization_id: str | None,
    user_id: UUID,
    requested_operation: str,
) -> UUID:
    """현재 active organization의 owner/manager 경계를 강제한다."""
    organization_id = resolve_active_organization_id(
        db,
        request,
        raw_organization_id,
        user_id,
    )
    if not has_organization_manager_permission(db, user_id, organization_id):
        # 사용자가 이미 접근 중인 organization만 safe target으로 기록한다.
        # Alert ID는 조회 전에 기록하지 않아 hidden target 존재 여부를 남기지 않는다.
        record_audit(
            action=AuditAction.PERMISSION_DENIED,
            category="action",
            actor_id=user_id,
            actor_type="user",
            target_type="organization",
            target_id=organization_id,
            status="failure",
            metadata={
                "organization_id": str(organization_id),
                "required_permission": "security_alert.manage",
                "requested_operation": requested_operation,
                "denial_reason": "organization_manager_required",
                "permission_action": "manage",
                "policy_result": "deny",
            },
        )
        exc = HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "permission.denied",
                    "message": "Organization manager permission is required.",
                    "request_id": getattr(request.state, "request_id", None),
                    "details": {},
                }
            },
        )
        setattr(exc, "audit_recorded", True)
        raise exc
    return organization_id


def get_security_alert_in_organization_or_404(
    db: Session,
    request: Request,
    organization_id: UUID,
    alert_id: UUID,
) -> SecurityAlert:
    """존재하지 않거나 다른 organization인 alert를 같은 404로 숨긴다."""
    alert = (
        db.query(SecurityAlert)
        .filter(
            SecurityAlert.id == alert_id,
            SecurityAlert.organization_id == organization_id,
        )
        .first()
    )
    if alert is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Security alert not found.",
        )
    return alert
