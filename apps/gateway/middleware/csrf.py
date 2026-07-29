from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from apps.gateway.application.csrf.models import (
    CsrfContentKind,
    CsrfRoutePolicy,
    CsrfRoutePolicyKind,
    CsrfRoutePolicyRegistry,
)
from apps.gateway.application.csrf.token import (
    CSRF_ANON_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRF_ORGANIZATION_HEADER_NAME,
    CsrfBindingKind,
    CsrfTokenService,
    CsrfValidationReason,
)
from apps.gateway.application.csrf.telemetry import run_bounded_csrf_telemetry
from apps.gateway.core.request_id import safe_request_id

logger = logging.getLogger(__name__)

DenialCallback = Callable[
    [CsrfValidationReason, CsrfRoutePolicy, str, str],
    Any,
]
AuthRequiredCallback = Callable[[CsrfRoutePolicy, str, str], Any]

_ALLOWED_FETCH_SITES = frozenset({"same-origin", "same-site"})
_JSON_MEDIA_TYPE = "application/json"
_MULTIPART_MEDIA_TYPE = "multipart/form-data"
_MULTIPART_BOUNDARY_PATTERN = re.compile(r"^[0-9A-Za-z._-]{1,70}$")


class CsrfProtectionMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        registry: CsrfRoutePolicyRegistry,
        token_service: CsrfTokenService,
        allowed_origins: Sequence[str],
        enforcement_enabled: bool,
        on_denied: DenialCallback,
        on_auth_required: AuthRequiredCallback | None = None,
    ):
        super().__init__(app)
        self._registry = registry
        self._token_service = token_service
        self._allowed_origins = frozenset(allowed_origins)
        self._enforcement_enabled = enforcement_enabled
        self._on_denied = on_denied
        self._on_auth_required = on_auth_required

    async def dispatch(self, request: Request, call_next) -> Response:
        policy = self._registry.match(request.method, request.url.path)
        if policy is None or not policy.requires_csrf or not self._enforcement_enabled:
            return await call_next(request)

        if (
            policy.policy_kind is CsrfRoutePolicyKind.COOKIE_AUTHENTICATED
            and not request.cookies.get("auth_token")
        ):
            return await self._authentication_required(request, policy)

        reason = self._validate_browser_boundary(request, policy)
        if reason is None:
            return await call_next(request)
        return await self._denied(request, policy, reason)

    def _validate_browser_boundary(
        self,
        request: Request,
        policy: CsrfRoutePolicy,
    ) -> CsrfValidationReason | None:
        origin = request.headers.get("origin")
        if origin is None or origin not in self._allowed_origins:
            return CsrfValidationReason.ORIGIN_INVALID

        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site.lower() not in _ALLOWED_FETCH_SITES:
            return CsrfValidationReason.FETCH_METADATA_INVALID

        if not self._content_type_allowed(request, policy.content_kind):
            return CsrfValidationReason.CONTENT_TYPE_INVALID

        auth_cookie = request.cookies.get("auth_token")
        if policy.policy_kind is CsrfRoutePolicyKind.COOKIE_AUTHENTICATED:
            binding_kind = CsrfBindingKind.AUTHENTICATED
            binding_secret = auth_cookie
        elif auth_cookie:
            binding_kind = CsrfBindingKind.AUTHENTICATED
            binding_secret = auth_cookie
        else:
            binding_kind = CsrfBindingKind.PRE_AUTH
            binding_secret = request.cookies.get(CSRF_ANON_COOKIE_NAME)

        return self._token_service.validate(
            header_token=request.headers.get(CSRF_HEADER_NAME),
            cookie_token=request.cookies.get(CSRF_COOKIE_NAME),
            binding_kind=binding_kind,
            binding_secret=binding_secret,
            organization_scope=request.headers.get(CSRF_ORGANIZATION_HEADER_NAME),
        )

    @staticmethod
    def _content_type_allowed(
        request: Request,
        content_kind: CsrfContentKind,
    ) -> bool:
        if content_kind is CsrfContentKind.UNRESTRICTED:
            return True

        raw_content_type = request.headers.get("content-type")
        content_length = request.headers.get("content-length")
        has_transfer_encoding = (
            request.headers.get("transfer-encoding") is not None
        )
        if (
            content_kind is CsrfContentKind.BODY_OPTIONAL
            and raw_content_type
            and content_length == "0"
            and not has_transfer_encoding
        ):
            return True
        if not raw_content_type:
            if content_kind is not CsrfContentKind.BODY_OPTIONAL:
                return False
            return (
                content_length in {None, "", "0"}
                and not has_transfer_encoding
            )

        media_type, *raw_parameters = raw_content_type.split(";")
        normalized_media_type = media_type.strip().lower()
        parameters = CsrfProtectionMiddleware._parse_parameters(raw_parameters)
        if parameters is None:
            return False

        if normalized_media_type == _JSON_MEDIA_TYPE:
            return not parameters or parameters == {"charset": "utf-8"}

        if (
            content_kind is CsrfContentKind.MULTIPART
            and normalized_media_type == _MULTIPART_MEDIA_TYPE
        ):
            boundary = parameters.get("boundary")
            return (
                set(parameters) == {"boundary"}
                and boundary is not None
                and _MULTIPART_BOUNDARY_PATTERN.fullmatch(boundary) is not None
            )
        return False

    @staticmethod
    def _parse_parameters(
        raw_parameters: list[str],
    ) -> dict[str, str] | None:
        parameters: dict[str, str] = {}
        for raw_parameter in raw_parameters:
            if not raw_parameter.strip() or "=" not in raw_parameter:
                return None
            raw_name, raw_value = raw_parameter.split("=", 1)
            name = raw_name.strip().lower()
            value = raw_value.strip().strip('"')
            if not name or not value or name in parameters:
                return None
            parameters[name] = value.lower() if name == "charset" else value
        return parameters

    async def _authentication_required(
        self,
        request: Request,
        policy: CsrfRoutePolicy,
    ) -> Response:
        request_id = safe_request_id(
            request.headers.get("X-Request-ID")
            or getattr(request.state, "request_id", None)
        )
        request.state.request_id = request_id
        if self._on_auth_required is not None:
            try:
                await run_bounded_csrf_telemetry(
                    self._on_auth_required,
                    policy,
                    request.method.upper(),
                    request_id,
                )
            except Exception as exc:
                logger.error(
                    "Authentication denial telemetry failed: error_type=%s",
                    type(exc).__name__,
                )
        response = JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "auth.required",
                    "message": "Authentication is required.",
                    "request_id": request_id,
                }
            },
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Request-ID"] = request_id
        return response

    async def _denied(
        self,
        request: Request,
        policy: CsrfRoutePolicy,
        reason: CsrfValidationReason,
    ) -> Response:
        request_id = safe_request_id(
            request.headers.get("X-Request-ID")
            or getattr(request.state, "request_id", None)
        )
        request.state.request_id = request_id
        try:
            await run_bounded_csrf_telemetry(
                self._on_denied,
                reason,
                policy,
                request.method.upper(),
                request_id,
            )
        except Exception as exc:
            logger.error(
                "CSRF denial telemetry failed: error_type=%s",
                type(exc).__name__,
            )

        response = JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "auth.csrf_validation_failed",
                    "message": "CSRF validation failed.",
                    "request_id": request_id,
                }
            },
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Request-ID"] = request_id
        return response
