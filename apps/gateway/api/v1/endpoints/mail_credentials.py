from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.composition.google_oauth import build_gmail_oauth_service
from apps.gateway.services.mail_credential_service import (
    MailCredentialService,
    MailCredentialServiceError,
)
from apps.gateway.services.gmail_oauth_service import (
    resolve_gmail_oauth_redirect_uri,
)
from apps.gateway.services.organization_context import resolve_active_organization_id
from apps.gateway.utils.api_errors import raise_api_error
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.schemas.mail_credential import (
    MailCredentialCreate,
    MailCredentialOptionResponse,
    MailCredentialPermissionGrant,
    MailCredentialPermissionResponse,
    MailCredentialResponse,
    MailCredentialUpdate,
    GmailOAuthStartRequest,
    GmailOAuthStartResponse,
)
from apps.shared.schemas.team import ResourcePermissionListResponse


router = APIRouter()


def _https_redirect_uri(request: Request, route_name: str) -> str:
    return resolve_gmail_oauth_redirect_uri(str(request.url_for(route_name)))


@router.post("/credentials/oauth/google/start", response_model=GmailOAuthStartResponse)
def start_gmail_oauth(
    payload: GmailOAuthStartRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    try:
        authorization_url = build_gmail_oauth_service(db).start(
            actor_id=current_user.id,
            organization_id=organization_id,
            credential_name=payload.credential_name,
            redirect_uri=_https_redirect_uri(request, "complete_gmail_oauth"),
            session=request.session,
        )
        return GmailOAuthStartResponse(authorization_url=authorization_url)
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)


@router.get("/credentials/oauth/google/callback")
async def complete_gmail_oauth(
    request: Request,
    state: str = Query(min_length=16, max_length=512),
    code: str | None = Query(default=None, min_length=1, max_length=4096),
    error: str | None = Query(default=None, min_length=1, max_length=255),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        await build_gmail_oauth_service(db).handle_callback(
            actor_id=current_user.id,
            state=state,
            code=code,
            provider_error=error,
            session=request.session,
        )
        return RedirectResponse(
            url=str(request.url_for("gmail_oauth_result")),
            status_code=303,
        )
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)


@router.get(
    "/credentials/oauth/google/result",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def gmail_oauth_result() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Gmail connected</title></head><body>"
        "<main><h1>Gmail connected</h1>"
        "<p>You can close this window and refresh the credential list.</p>"
        "</main></body></html>",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _organization_id(
    db: Session,
    request: Request,
    raw_organization_id: str | None,
    current_user: User,
) -> UUID:
    return resolve_active_organization_id(
        db, request, raw_organization_id, current_user.id
    )


def _handle_service_error(request: Request, exc: MailCredentialServiceError) -> None:
    raise_api_error(request, exc.status_code, exc.code, exc.detail)


@router.post("/credentials", response_model=MailCredentialResponse)
def create_mail_credential(
    payload: MailCredentialCreate,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    try:
        return MailCredentialService(db).create(
            current_user.id, organization_id, payload
        )
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)


@router.get("/credentials", response_model=list[MailCredentialOptionResponse])
def list_mail_credentials(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    return MailCredentialService(db).list_available(current_user.id, organization_id)


@router.get("/credentials/{credential_id}", response_model=MailCredentialResponse)
def get_mail_credential(
    credential_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    try:
        return MailCredentialService(db).get(
            current_user.id, organization_id, credential_id
        )
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)


@router.patch("/credentials/{credential_id}", response_model=MailCredentialResponse)
def update_mail_credential(
    credential_id: UUID,
    payload: MailCredentialUpdate,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    try:
        return MailCredentialService(db).update(
            current_user.id, organization_id, credential_id, payload
        )
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)


@router.delete("/credentials/{credential_id}", response_model=MailCredentialResponse)
def revoke_mail_credential(
    credential_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    try:
        return MailCredentialService(db).revoke(
            current_user.id, organization_id, credential_id
        )
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)


@router.get(
    "/credentials/{credential_id}/permissions",
    response_model=ResourcePermissionListResponse,
)
def list_mail_credential_permissions(
    credential_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    try:
        return MailCredentialService(db).list_permissions(
            current_user.id, organization_id, credential_id
        )
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)


@router.put(
    "/credentials/{credential_id}/permissions/users/{user_id}",
    response_model=MailCredentialPermissionResponse,
)
def put_user_mail_credential_permission(
    credential_id: UUID,
    user_id: UUID,
    payload: MailCredentialPermissionGrant,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    try:
        return MailCredentialService(db).grant_user_permission(
            current_user.id, organization_id, credential_id, user_id, payload
        )
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)


@router.put(
    "/credentials/{credential_id}/permissions/teams/{team_id}",
    response_model=MailCredentialPermissionResponse,
)
def put_team_mail_credential_permission(
    credential_id: UUID,
    team_id: UUID,
    payload: MailCredentialPermissionGrant,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    try:
        return MailCredentialService(db).grant_team_permission(
            current_user.id, organization_id, credential_id, team_id, payload
        )
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)


@router.delete("/credentials/{credential_id}/permissions/users/{user_id}")
def delete_user_mail_credential_permission(
    credential_id: UUID,
    user_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    try:
        MailCredentialService(db).revoke_user_permission(
            current_user.id, organization_id, credential_id, user_id
        )
        return {"status": "deleted"}
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)


@router.delete("/credentials/{credential_id}/permissions/teams/{team_id}")
def delete_team_mail_credential_permission(
    credential_id: UUID,
    team_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _organization_id(db, request, x_organization_id, current_user)
    try:
        MailCredentialService(db).revoke_team_permission(
            current_user.id, organization_id, credential_id, team_id
        )
        return {"status": "deleted"}
    except MailCredentialServiceError as exc:
        _handle_service_error(request, exc)
