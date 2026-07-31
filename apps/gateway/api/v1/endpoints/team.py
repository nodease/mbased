from typing import TypeVar
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from apps.gateway.services.auth_service import AuthService
from apps.gateway.services.team_service import TeamManagerScope, TeamService
from apps.gateway.utils.api_errors import (
    auth_error_code,
    auth_error_message,
    error_response,
    parse_organization_id,
)
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.schemas.team import (
    TeamCreateBody,
    TeamCreateRequest,
    TeamMemberResponse,
    TeamMembershipRequest,
    TeamResponse,
    TeamUpdateRequest,
)

router = APIRouter()
BodyModel = TypeVar("BodyModel", bound=BaseModel)


def _authenticate(
    request: Request,
    db: Session,
    auth_token: str | None,
) -> tuple[User | None, JSONResponse | None]:
    try:
        return AuthService.get_user_from_token(db, auth_token), None
    except HTTPException as exc:
        return None, error_response(
            request,
            exc.status_code,
            auth_error_code(exc, auth_token),
            auth_error_message(exc),
        )


def _parse_organization_id(
    request: Request, raw_organization_id: str | None
) -> tuple[UUID | None, JSONResponse | None]:
    try:
        return parse_organization_id(request, raw_organization_id), None
    except HTTPException as exc:
        return None, JSONResponse(status_code=exc.status_code, content=exc.detail)


def _parse_limit(
    request: Request, raw_limit: str
) -> tuple[int | None, JSONResponse | None]:
    try:
        limit = int(raw_limit)
    except ValueError:
        return None, error_response(
            request,
            422,
            "validation.failed",
            "limit must be an integer.",
            {"field": "limit"},
        )

    if limit < 1 or limit > 100:
        return None, error_response(
            request,
            422,
            "validation.failed",
            "limit must be between 1 and 100.",
            {"field": "limit"},
        )

    return limit, None


async def _parse_body(
    request: Request,
    model: type[BodyModel],
) -> tuple[BodyModel | None, JSONResponse | None]:
    try:
        raw_payload = await request.json()
    except ValueError:
        return None, error_response(
            request,
            422,
            "validation.failed",
            "Request validation failed.",
            {"errors": [{"loc": ["body"], "msg": "Invalid JSON."}]},
        )

    try:
        return model.model_validate(raw_payload), None
    except ValidationError as exc:
        errors = []
        for item in exc.errors():
            error = dict(item)
            error["loc"] = ["body", *error.get("loc", [])]
            errors.append(error)
        return None, error_response(
            request,
            422,
            "validation.failed",
            "Request validation failed.",
            {"errors": jsonable_encoder(errors)},
        )


def _require_organization_manager(
    request: Request,
    db: Session,
    organization_id: UUID,
    current_user: User,
) -> tuple[TeamManagerScope | None, JSONResponse | None]:
    try:
        return (
            TeamService.ensure_organization_manager_scope(
                db,
                current_user,
                organization_id,
            ),
            None,
        )
    except HTTPException as exc:
        return None, _service_error_response(request, exc)


def _service_error_response(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    message = str(exc.detail)
    if exc.status_code == 404:
        return error_response(request, 404, "resource.not_found", message)
    if exc.status_code == 409:
        return error_response(request, 409, "resource.conflict", message)
    if exc.status_code == 403:
        return error_response(
            request,
            403,
            "permission.denied",
            "Organization manager permission is required.",
        )
    if exc.status_code == 400:
        return error_response(request, 400, "validation.failed", message)
    return error_response(request, exc.status_code, "operation.failed", message)


def _validate_team_update_request(
    request: Request,
    payload: TeamUpdateRequest,
) -> JSONResponse | None:
    fields = payload.model_fields_set
    if not fields:
        return error_response(
            request,
            400,
            "validation.failed",
            "No team fields to update.",
        )

    if "name" in fields and (
        payload.name is None or payload.name.strip() == ""
    ):
        return error_response(
            request,
            400,
            "validation.failed",
            "Team name is required.",
            {"field": "name"},
        )

    return None


@router.get("", response_model=list[TeamResponse])
def list_teams(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    limit: str = Query(default="10"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    current_user, error = _authenticate(request, db, auth_token)
    if error is not None:
        return error

    organization_id, error = _parse_organization_id(request, x_organization_id)
    if error is not None:
        return error

    parsed_limit, error = _parse_limit(request, limit)
    if error is not None:
        return error

    manager_scope, error = _require_organization_manager(
        request,
        db,
        organization_id,
        current_user,
    )
    if error is not None:
        return error

    try:
        return TeamService.list_teams(
            db,
            current_user,
            organization_id,
            limit=parsed_limit,
            manager_scope=manager_scope,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)


@router.post("", response_model=TeamResponse, status_code=201)
async def create_team(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    current_user, error = _authenticate(request, db, auth_token)
    if error is not None:
        return error

    organization_id, error = _parse_organization_id(request, x_organization_id)
    if error is not None:
        return error

    manager_scope, error = _require_organization_manager(
        request,
        db,
        organization_id,
        current_user,
    )
    if error is not None:
        return error

    payload, error = await _parse_body(request, TeamCreateBody)
    if error is not None:
        return error

    try:
        return TeamService.create_team(
            db,
            current_user,
            TeamCreateRequest(
                organization_id=organization_id,
                name=payload.name,
                description=payload.description,
                is_auto_add=payload.is_auto_add,
            ),
            manager_scope=manager_scope,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    current_user, error = _authenticate(request, db, auth_token)
    if error is not None:
        return error

    organization_id, error = _parse_organization_id(request, x_organization_id)
    if error is not None:
        return error

    manager_scope, error = _require_organization_manager(
        request,
        db,
        organization_id,
        current_user,
    )
    if error is not None:
        return error

    payload, error = await _parse_body(request, TeamUpdateRequest)
    if error is not None:
        return error

    error = _validate_team_update_request(request, payload)
    if error is not None:
        return error

    try:
        return TeamService.update_team(
            db,
            current_user,
            team_id,
            payload,
            organization_id,
            manager_scope=manager_scope,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)


@router.get("/{team_id}/members", response_model=list[TeamMemberResponse])
def list_team_members(
    team_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    current_user, error = _authenticate(request, db, auth_token)
    if error is not None:
        return error

    organization_id, error = _parse_organization_id(request, x_organization_id)
    if error is not None:
        return error

    manager_scope, error = _require_organization_manager(
        request,
        db,
        organization_id,
        current_user,
    )
    if error is not None:
        return error

    try:
        memberships = TeamService.list_members(
            db,
            current_user,
            team_id,
            organization_id,
            manager_scope=manager_scope,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)

    return [
        {
            "id": membership.id,
            "user_id": membership.user_id,
            "email": membership.user.email,
            "name": membership.user.name,
            "assigned_at": membership.assigned_at,
        }
        for membership in memberships
    ]


@router.post("/{team_id}/members")
async def add_team_member(
    team_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    current_user, error = _authenticate(request, db, auth_token)
    if error is not None:
        return error

    organization_id, error = _parse_organization_id(request, x_organization_id)
    if error is not None:
        return error

    manager_scope, error = _require_organization_manager(
        request,
        db,
        organization_id,
        current_user,
    )
    if error is not None:
        return error

    payload, error = await _parse_body(request, TeamMembershipRequest)
    if error is not None:
        return error

    try:
        membership = TeamService.add_membership(
            db,
            current_user,
            team_id,
            payload,
            organization_id,
            manager_scope=manager_scope,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)

    return {"id": str(membership.id), "status": "added"}


@router.delete("/{team_id}/members/{user_id}")
def remove_team_member(
    team_id: UUID,
    user_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    current_user, error = _authenticate(request, db, auth_token)
    if error is not None:
        return error

    organization_id, error = _parse_organization_id(request, x_organization_id)
    if error is not None:
        return error

    manager_scope, error = _require_organization_manager(
        request,
        db,
        organization_id,
        current_user,
    )
    if error is not None:
        return error

    try:
        return TeamService.remove_membership(
            db,
            current_user,
            team_id,
            user_id,
            organization_id,
            manager_scope=manager_scope,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)


@router.delete("/{team_id}")
def deactivate_team(
    team_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    current_user, error = _authenticate(request, db, auth_token)
    if error is not None:
        return error

    organization_id, error = _parse_organization_id(request, x_organization_id)
    if error is not None:
        return error

    manager_scope, error = _require_organization_manager(
        request,
        db,
        organization_id,
        current_user,
    )
    if error is not None:
        return error

    try:
        return TeamService.deactivate_team(
            db,
            current_user,
            team_id,
            organization_id,
            manager_scope=manager_scope,
        )
    except HTTPException as exc:
        return _service_error_response(request, exc)
