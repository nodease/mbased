from typing import List, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from apps.gateway.application.app_lifecycle.errors import (
    AppDeleteInProgress,
    AppDeleteRequiresRepair,
    AppPermissionDenied,
)
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.services.organization_context import resolve_active_organization_id
from apps.gateway.utils.api_errors import raise_api_error
from apps.gateway.utils.audit import audit
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.app import App
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.schemas.app import (
    AppCreateRequest,
    AppOperationRow,
    AppResponse,
    AppUpdateRequest,
)
from apps.shared.services import permissions as shared_permissions
from apps.gateway.services.app_service import AppService

router = APIRouter()

OperationPermissionFilter = Literal["viewer", "operator", "builder", "manager"]
OperationCapabilityFilter = Literal["execute", "write", "manage"]
OperationDeploymentFilter = Literal["active", "inactive", "undeployed"]
OperationRunFilter = Literal[
    "running", "success", "failed", "not_started", "unavailable"
]


def _app_access_exception(status_code: int) -> HTTPException:
    if status_code == 403:
        return HTTPException(status_code=403, detail="Forbidden")
    return HTTPException(status_code=404, detail="App not found")


def _get_app_or_404(db: Session, app_id: str) -> App:
    app = db.query(App).filter(App.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    return app


@router.patch("/{app_id}", response_model=AppResponse)
@audit(AuditAction.APP_UPDATE, target_param="app_id")
def update_app(
    app_id: str,
    request: AppUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    앱 정보를 수정합니다. (본인 앱만)
    """
    try:
        app_record = _get_app_or_404(db, app_id)
        denial_status = AppService.access_denial_status(
            db, app_record, current_user.id, "manage"
        )
        if denial_status is not None:
            raise _app_access_exception(denial_status)

        app = AppService.update_app(db, app_id, request, user_id=current_user.id)
        if not app:
            raise HTTPException(status_code=404, detail="App not found")
        return app
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("", response_model=AppResponse)
@audit(AuditAction.APP_CREATE)
def create_app(
    request: Request,
    payload: AppCreateRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    새로운 앱을 생성합니다. (인증 필요)
    """
    try:
        organization_id = resolve_active_organization_id(
            db, request, x_organization_id, current_user.id
        )
        # 조직 수준 App 생성 능력 판정 (ADR-0016). owner/manager 또는
        # user_app_creation_permissions row 보유자만 허용한다.
        if not shared_permissions.has_app_creation_permission(
            db, current_user.id, organization_id
        ):
            raise HTTPException(status_code=403, detail="Forbidden")
        return AppService.create_app(
            db, payload, user_id=current_user.id, organization_id=organization_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/explore", response_model=List[AppResponse])
def list_explore_apps(
    db: Session = Depends(get_db),
    # 탐색 페이지는 공개 접근을 의미하지만, 보통 사용자는 여전히 로그인 상태입니다.
    # 로그인 없이 공개 접근을 원한다면 current_user 의존성을 제거하면 됩니다.
    # 현재 시스템 설계상 복제/조회를 위해 로그인이 필요하다고 가정합니다.
    current_user: User = Depends(get_current_user),
):
    """
    공개된 앱 목록 조회 (커뮤니티 탐색)
    """
    return AppService.list_explore_apps(db, user_id=current_user.id)


@router.get("", response_model=List[AppResponse])
def list_apps(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    현재 유저의 앱 목록 조회
    """
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    apps = AppService.get_user_apps(
        db, user_id=current_user.id, organization_id=organization_id
    )
    return apps


@router.get(
    "/operations",
    response_model=List[AppOperationRow],
    response_model_exclude_none=True,
)
def list_app_operations(
    request: Request,
    q: str | None = Query(default=None),
    permission: OperationPermissionFilter | None = Query(default=None),
    capability: OperationCapabilityFilter | None = Query(default=None),
    deployment_state: OperationDeploymentFilter | None = Query(default=None),
    run_state: OperationRunFilter | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    내 모듈 운영 현황 summary 조회
    """
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    return AppService.list_app_operations(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
        q=q,
        permission=permission,
        capability=capability,
        deployment_state=deployment_state,
        run_state=run_state,
        limit=limit,
        offset=offset,
    )


@router.get("/{app_id}", response_model=AppResponse)
def get_app(
    app_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    특정 앱 정보 조회
    """
    app_record = _get_app_or_404(db, app_id)
    denial_status = AppService.access_denial_status(
        db, app_record, current_user.id, "read"
    )
    if denial_status is not None:
        raise _app_access_exception(denial_status)

    app = AppService.get_app(db, app_id, user_id=current_user.id)
    return app


@router.post("/{app_id}/clone", response_model=AppResponse)
@audit(AuditAction.APP_CLONE, target_param="app_id")
def clone_app(
    app_id: str,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    앱을 복제합니다. (내 스튜디오로 복사)
    """
    try:
        source_app = _get_app_or_404(db, app_id)
        denial_status = AppService.access_denial_status(
            db, source_app, current_user.id, "read"
        )
        if denial_status is not None:
            raise _app_access_exception(denial_status)

        organization_id = resolve_active_organization_id(
            db, request, x_organization_id, current_user.id
        )
        app = AppService.clone_app(
            db,
            user_id=current_user.id,
            source_app_id=app_id,
            organization_id=organization_id,
        )
        if not app:
            raise HTTPException(status_code=404, detail="App not found")
        return app
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{app_id}")
def delete_app(
    app_id: str,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    active organization 안의 앱과 활성 lifecycle 데이터를 삭제합니다.
    """
    organization_id = resolve_active_organization_id(
        db,
        request,
        x_organization_id,
        current_user.id,
    )

    try:
        result = AppService.delete_app(
            db,
            app_id,
            user_id=current_user.id,
            organization_id=organization_id,
        )
    except AppPermissionDenied:
        raise_api_error(
            request,
            403,
            "permission.denied",
            "App manage permission is required.",
        )
    except AppDeleteInProgress:
        raise_api_error(
            request,
            409,
            "app.delete_in_progress",
            "App deletion is blocked by an active operation.",
        )
    except AppDeleteRequiresRepair:
        raise_api_error(
            request,
            409,
            "app.delete_requires_repair",
            "App relationships must be repaired before deletion.",
        )

    if not result:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "App not found.",
        )

    return {"message": "App deleted successfully"}
