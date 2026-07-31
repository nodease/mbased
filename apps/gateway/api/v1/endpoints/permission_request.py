from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.services.organization_context import resolve_active_organization_id
from apps.gateway.services.permission_request_service import PermissionRequestService
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.schemas.permission_request import (
    PermissionRequestCreateRequest,
    PermissionRequestResponse,
)

router = APIRouter()


@router.post("", response_model=PermissionRequestResponse, status_code=201)
def submit_permission_request(
    request: Request,
    payload: PermissionRequestCreateRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """workflow 생성/배포(App 생성) 권한 신청 제출 (FR-041, ADR-0016)."""
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    created = PermissionRequestService.submit_request(
        db,
        user=current_user,
        organization_id=organization_id,
        requested_permission=payload.requested_permission,
        reason=payload.reason,
    )
    return PermissionRequestResponse(
        id=created.id,
        requested_permission=created.requested_permission,
        reason=created.reason,
        status=created.status,
        created_at=created.created_at,
        decided_by=created.decided_by,
        decided_at=created.decided_at,
    )
