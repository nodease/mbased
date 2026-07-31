from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.api.deps import get_access_management_application
from apps.gateway.application.access_management.errors import (
    AccessManagementError,
    AuditPersistenceFailed,
    InputValidationError,
    LastActiveManager,
    PermissionDenied,
    PolicyBlocked,
    ResourceHidden,
    SelfControlForbidden,
    StaleState,
    WorkflowPrimaryChanged,
)
from apps.gateway.application.access_management.models import AccessActionCommand
from apps.gateway.composition.access_management import AccessManagementApplication
from apps.gateway.services.organization_member_service import OrganizationMemberService
from apps.gateway.utils.api_errors import (
    error_detail,
    error_response,
    parse_organization_id,
    raise_api_error,
)
from apps.gateway.utils.audit import audit
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.schemas.organization import (
    OrganizationPatchRequest,
    OrganizationResponse,
)
from apps.shared.schemas.organization_membership import (
    OrganizationMemberInviteRequest,
    OrganizationMemberListItemResponse,
    OrganizationMemberRemoveResponse,
    OrganizationMemberResponse,
    OrganizationMemberUpdateRequest,
    OrganizationSummaryResponse,
)
from apps.shared.schemas.member_access import (
    MemberAccessActionRequest,
    MemberAccessActionResponse,
    MemberAccessProfileResponse,
    MemberResourceAccessItem,
    MemberResourceAccessListResponse,
    MemberTeamMembershipItem,
    MemberTeamMembershipListResponse,
)
from apps.shared.services.permission_audit import record_resource_permission_denied
from apps.shared.services.permissions import (
    has_organization_manager_permission,
    has_organization_scope_access,
)

router = APIRouter()


def _service_error_response(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    message = str(exc.detail)
    if exc.status_code == 404:
        return error_response(request, 404, "resource.not_found", message)
    if exc.status_code == 409:
        return error_response(request, 409, "resource.conflict", message)
    if exc.status_code == 403:
        return error_response(request, 403, "permission.denied", message)
    if exc.status_code == 400:
        return error_response(request, 400, "validation.failed", message)
    return error_response(request, exc.status_code, "operation.failed", message)


def _raise_access_management_error(
    request: Request,
    exc: AccessManagementError,
) -> NoReturn:
    if isinstance(exc, ResourceHidden):
        status_code = 404
    elif isinstance(exc, PermissionDenied):
        status_code = 403
    elif isinstance(exc, SelfControlForbidden):
        status_code = 400
    elif isinstance(
        exc,
        (PolicyBlocked, StaleState, LastActiveManager, WorkflowPrimaryChanged),
    ):
        status_code = 409
    elif isinstance(exc, InputValidationError):
        status_code = 422
    elif isinstance(exc, AuditPersistenceFailed):
        status_code = 500
    else:
        status_code = 500

    http_error = HTTPException(
        status_code=status_code,
        detail=error_detail(request, exc.code, str(exc)),
    )
    if isinstance(exc, PermissionDenied):
        setattr(http_error, "audit_recorded", True)
    raise http_error


def _get_organization_in_active_membership_scope(
    db: Session,
    organization_id: UUID,
    user_id: UUID,
) -> Organization | None:
    # Active organization은 서버에 저장하지 않고, 요청 header/path 값이
    # 현재 사용자의 organization membership scope 안에 있는지로 판정한다.
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        return None
    if not has_organization_scope_access(db, user_id, organization_id):
        return None
    return organization


def _ensure_matching_active_organization_header(
    request: Request,
    path_organization_id: UUID,
    raw_organization_id: str | None,
) -> UUID:
    organization_id = parse_organization_id(request, raw_organization_id)
    if organization_id != path_organization_id:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )
    return organization_id


def _to_organization_response(
    db: Session,
    organization: Organization,
    user_id: UUID,
) -> OrganizationResponse:
    return OrganizationResponse(
        id=organization.id,
        name=organization.name,
        options=organization.options,
        is_active=organization.is_active,
        is_manager=has_organization_manager_permission(db, user_id, organization.id),
        created_at=organization.created_at,
        updated_at=organization.updated_at,
    )


# 인증된 사용자가 active context로 사용할 수 있는 organization 목록을 조회하는 API.
@router.get("", response_model=list[OrganizationResponse])
def list_organizations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organizations = OrganizationMemberService.list_active_organizations(
        db,
        current_user,
    )
    return [
        _to_organization_response(db, organization, current_user.id)
        for organization in organizations
    ]


# 인증된 사용자가 속한 active/invited organization membership 목록을 조회하는 API.
@router.get("/memberships", response_model=list[OrganizationSummaryResponse])
def list_organization_memberships(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return OrganizationMemberService.list_organization_memberships(db, current_user)


# literal path인 current가 /{organization_id} UUID path parameter로 해석되지 않도록
# 먼저 등록한다.
@router.get("/current", response_model=OrganizationResponse)
def get_current_organization(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = parse_organization_id(request, x_organization_id)
    organization = _get_organization_in_active_membership_scope(
        db,
        organization_id,
        current_user.id,
    )

    if organization is None:
        raise_api_error(request, 404, "resource.not_found", "Organization not found.")

    return _to_organization_response(db, organization, current_user.id)


@router.get(
    "/{organization_id}/members",
    response_model=list[OrganizationMemberListItemResponse],
)
def list_members(
    request: Request,
    organization_id: UUID,
    state: str | None = None,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_matching_active_organization_header(
        request,
        organization_id,
        x_organization_id,
    )
    try:
        return OrganizationMemberService.list_members(
            db,
            current_user,
            organization_id,
            state,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)


# 조직 관리자가 멤버를 초대하거나 제거된 멤버를 다시 초대하는 API.
@router.post(
    "/{organization_id}/members/invitations",
    response_model=OrganizationMemberResponse,
)
def invite_member(
    request: Request,
    organization_id: UUID,
    payload: OrganizationMemberInviteRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_matching_active_organization_header(
        request,
        organization_id,
        x_organization_id,
    )
    try:
        return OrganizationMemberService.invite_member(
            db,
            current_user,
            organization_id,
            payload,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)


# 초대받은 사용자가 본인의 조직 초대를 수락하는 API.
@router.post(
    "/{organization_id}/members/me/accept",
    response_model=OrganizationMemberResponse,
)
def accept_invitation(
    request: Request,
    organization_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return OrganizationMemberService.accept_invitation(
            db,
            current_user,
            organization_id,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)


# 초대받은 사용자가 본인의 조직 초대를 거절하는 API.
@router.post(
    "/{organization_id}/members/me/decline",
    response_model=OrganizationMemberResponse,
)
def decline_invitation(
    request: Request,
    organization_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return OrganizationMemberService.decline_invitation(
            db,
            current_user,
            organization_id,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)


# 조직 관리자가 멤버 상태와 권한을 변경하는 API.
@router.patch(
    "/{organization_id}/members/{user_id}",
    response_model=OrganizationMemberResponse,
)
def update_member(
    request: Request,
    organization_id: UUID,
    user_id: UUID,
    payload: OrganizationMemberUpdateRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_matching_active_organization_header(
        request,
        organization_id,
        x_organization_id,
    )
    try:
        return OrganizationMemberService.update_member(
            db,
            current_user,
            organization_id,
            user_id,
            payload,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)


# 조직 관리자가 멤버를 제거하고 연결된 권한을 정리하는 API.
@router.delete(
    "/{organization_id}/members/{user_id}",
    response_model=OrganizationMemberRemoveResponse,
)
def remove_member(
    request: Request,
    organization_id: UUID,
    user_id: UUID,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_matching_active_organization_header(
        request,
        organization_id,
        x_organization_id,
    )
    try:
        return OrganizationMemberService.remove_member(
            db,
            current_user,
            organization_id,
            user_id,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)


@router.get(
    "/{organization_id}/members/{user_id}/access-profile",
    response_model=MemberAccessProfileResponse,
)
def get_member_access_profile(
    request: Request,
    organization_id: UUID,
    user_id: UUID,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    current_user: User = Depends(get_current_user),
    application: AccessManagementApplication = Depends(
        get_access_management_application
    ),
):
    _ensure_matching_active_organization_header(
        request,
        organization_id,
        x_organization_id,
    )
    try:
        result = application.get_access_profile.execute(
            actor_id=current_user.id,
            organization_id=organization_id,
            target_user_id=user_id,
        )
    except AccessManagementError as exc:
        _raise_access_management_error(request, exc)
    return MemberAccessProfileResponse.model_validate(result, from_attributes=True)


@router.get(
    "/{organization_id}/members/{user_id}/team-memberships",
    response_model=MemberTeamMembershipListResponse,
)
def get_member_team_memberships(
    request: Request,
    organization_id: UUID,
    user_id: UUID,
    team_id: Annotated[UUID | None, Query(alias="teamId")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    current_user: User = Depends(get_current_user),
    application: AccessManagementApplication = Depends(
        get_access_management_application
    ),
):
    _ensure_matching_active_organization_header(
        request,
        organization_id,
        x_organization_id,
    )
    try:
        result = application.list_team_memberships.execute(
            actor_id=current_user.id,
            organization_id=organization_id,
            target_user_id=user_id,
            team_id=team_id,
            page=page,
            limit=limit,
        )
    except AccessManagementError as exc:
        _raise_access_management_error(request, exc)
    return MemberTeamMembershipListResponse(
        total=result.total,
        items=[
            MemberTeamMembershipItem.model_validate(item, from_attributes=True)
            for item in result.items
        ],
    )


@router.get(
    "/{organization_id}/members/{user_id}/resource-access",
    response_model=MemberResourceAccessListResponse,
)
def get_member_resource_access(
    request: Request,
    organization_id: UUID,
    user_id: UUID,
    resource_type: Annotated[
        Literal[
            "workflow",
            "knowledge_base",
            "llm_credential",
            "mail_credential",
        ],
        Query(alias="resourceType"),
    ],
    resource_id: Annotated[UUID | None, Query(alias="resourceId")] = None,
    source: Annotated[Literal["all", "direct", "team"], Query()] = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    current_user: User = Depends(get_current_user),
    application: AccessManagementApplication = Depends(
        get_access_management_application
    ),
):
    _ensure_matching_active_organization_header(
        request,
        organization_id,
        x_organization_id,
    )
    try:
        result = application.list_resource_access.execute(
            actor_id=current_user.id,
            organization_id=organization_id,
            target_user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            source=source,
            page=page,
            limit=limit,
        )
    except AccessManagementError as exc:
        _raise_access_management_error(request, exc)
    return MemberResourceAccessListResponse(
        total=result.total,
        items=[
            MemberResourceAccessItem.model_validate(item, from_attributes=True)
            for item in result.items
        ],
    )


@router.post(
    "/{organization_id}/members/{user_id}/access-actions",
    response_model=MemberAccessActionResponse,
)
def execute_member_access_action(
    request: Request,
    organization_id: UUID,
    user_id: UUID,
    payload: MemberAccessActionRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    current_user: User = Depends(get_current_user),
    application: AccessManagementApplication = Depends(
        get_access_management_application
    ),
):
    _ensure_matching_active_organization_header(
        request,
        organization_id,
        x_organization_id,
    )
    command = AccessActionCommand(
        actor_id=current_user.id,
        organization_id=organization_id,
        target_user_id=user_id,
        **payload.model_dump(),
    )
    try:
        result = application.execute_access_action.execute(command)
    except AccessManagementError as exc:
        _raise_access_management_error(request, exc)
    return MemberAccessActionResponse.model_validate(result, from_attributes=True)


# 인증된 사용자가 접근 가능한 특정 active organization 상세를 조회하는 API.
@router.get("/{organization_id}", response_model=OrganizationResponse)
def get_organization(
    request: Request,
    organization_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization = _get_organization_in_active_membership_scope(
        db,
        organization_id,
        current_user.id,
    )

    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    return _to_organization_response(db, organization, current_user.id)


@router.patch("/{organization_id}", response_model=OrganizationResponse)
@audit(AuditAction.ORGANIZATION_UPDATE, target_param="organization_id")
def update_organization(
    request: Request,
    organization_id: UUID,
    payload: OrganizationPatchRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_matching_active_organization_header(
        request,
        organization_id,
        x_organization_id,
    )

    fields = payload.model_fields_set
    if not fields:
        raise_api_error(
            request,
            400,
            "validation.failed",
            "No organization fields to update.",
        )

    if "name" in fields and (payload.name is None or payload.name.strip() == ""):
        raise_api_error(
            request,
            400,
            "validation.failed",
            "Organization name is required.",
            {"field": "name"},
        )

    if "options" in fields and payload.options is None:
        raise_api_error(
            request,
            400,
            "validation.failed",
            "Organization options are required.",
            {"field": "options"},
        )

    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )

    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    if not has_organization_manager_permission(db, current_user.id, organization_id):
        record_resource_permission_denied(
            user_id=current_user.id,
            resource_type="organization",
            resource_id=organization_id,
            action="manage",
            effective_auth_state="member",
            organization_id=organization_id,
        )
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Permission denied.",
            audit_recorded=True,
        )

    if "name" in fields:
        organization.name = payload.name.strip()
    if "options" in fields:
        organization.options = payload.options

    db.commit()
    db.refresh(organization)

    return _to_organization_response(db, organization, current_user.id)
