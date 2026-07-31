from typing import NoReturn
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def error_detail(
    request: Request,
    code: str,
    message: str,
    details: dict | None = None,
) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", None),
            "details": details or {},
        }
    }


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_detail(request, code, message, details),
    )


def raise_api_error(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
    *,
    audit_recorded: bool = False,
) -> NoReturn:
    exc = HTTPException(
        status_code=status_code,
        detail=error_detail(request, code, message, details),
    )
    if audit_recorded:
        setattr(exc, "audit_recorded", True)
    raise exc


def parse_organization_id(request: Request, raw_organization_id: str | None) -> UUID:
    if raw_organization_id is None:
        raise_api_error(
            request,
            400,
            "organization.required",
            "X-Organization-Id header is required.",
        )

    try:
        return UUID(raw_organization_id)
    except ValueError:
        raise_api_error(
            request,
            422,
            "validation.failed",
            "X-Organization-Id must be a valid UUID.",
            {"field": "X-Organization-Id"},
        )


def auth_error_code(exc: HTTPException, auth_token: str | None) -> str:
    if exc.status_code == 401:
        return "auth.invalid" if auth_token else "auth.required"
    return "permission.denied"


def auth_error_message(exc: HTTPException) -> str:
    return exc.detail if isinstance(exc.detail, str) else "Authentication failed"
