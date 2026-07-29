# ruff: noqa: E402 - load local environment before importing configuration consumers
# .env 파일을 기본값으로 로드 ( 개발 환경 )
import logging
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Request

# ===================================================
# 로깅 설정 (FastAPI 시작 전 )
# ===================================================
# Python 표준 logger (logger.info, logger.error 등)가
# stdout으로 출력되도록 설정 → Promtail이 수집 → Loki로 전송
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s[%(asctime)s: %(levelname)s/%(processName)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Get logger for this module
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent.parent  # moduly/

# moduly/.env 로드
ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    logger.info(f"Loading .env from {ENV_PATH}")
    load_dotenv(dotenv_path=ENV_PATH, override=False)
else:
    logger.warning(f".env file not found at {ENV_PATH}")

import os

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from apps.gateway.api.api import api_router
from apps.gateway.application.resource_permissions.mutation import (
    PermissionMutationPersistenceFailed,
)
from apps.gateway.composition.authentication import (
    validate_login_security_configuration,
)
from apps.gateway.composition.csrf import (
    build_csrf_route_policy_registry,
    csrf_enforcement_enabled,
    csrf_token_service,
    record_csrf_auth_required,
    record_csrf_denial,
)
from apps.gateway.core.http_security import (
    parse_credentialed_cors_origins,
    resolve_session_signing_secret,
)
from apps.gateway.lifespan import lifespan  # Import lifespan from module
from apps.gateway.middleware.webhook_query_redaction import (
    WebhookQueryRedactionMiddleware,
)
from apps.gateway.middleware.public_conversation_cors import (
    PublicConversationCorsBoundaryMiddleware,
)
from apps.gateway.middleware.csrf import CsrfProtectionMiddleware
from apps.gateway.utils.api_errors import error_response
from apps.shared.audit import record_audit
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.context import (
    clear_current_metadata,
    get_current_metadata,
    set_current_metadata,
)

validate_login_security_configuration()

app = FastAPI(title="Moduly Gateway API", lifespan=lifespan)


# 요청별 request_id를 보장하고 audit 로그용 요청 metadata를 전파한다.
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    token = set_current_metadata(
        {
            "ip": request.client.host if request.client else None,
            "method": request.method,
            "path": request.url.path,
            "user_agent": request.headers.get("user-agent"),
            "request_id": request_id,
        }
    )
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        clear_current_metadata(token)


@app.exception_handler(HTTPException)
async def audit_permission_denied(request: Request, exc: HTTPException):
    if (
        request.method != "OPTIONS"
        and exc.status_code in (401, 403)
        and not getattr(exc, "audit_recorded", False)
    ):
        record_audit(
            action=AuditAction.AUTH_PERMISSION_DENIED,
            category="action",
            actor_type="system",
            status="failure",
            metadata={
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "detail": str(exc.detail),
                **get_current_metadata(),
            },
        )
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        content = exc.detail
    else:
        content = {"detail": exc.detail}

    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_failed(request: Request, exc: RequestValidationError):
    def _strip_validation_input(value):
        if isinstance(value, dict):
            return {
                key: _strip_validation_input(item)
                for key, item in value.items()
                if key != "input"
            }
        if isinstance(value, list):
            return [_strip_validation_input(item) for item in value]
        return value

    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation.failed",
                "message": "Request validation failed.",
                "request_id": getattr(request.state, "request_id", None),
                "details": {
                    "errors": _strip_validation_input(jsonable_encoder(exc.errors()))
                },
            }
        },
    )


@app.exception_handler(PermissionMutationPersistenceFailed)
async def permission_mutation_persistence_failed(
    request: Request,
    _exc: PermissionMutationPersistenceFailed,
):
    return error_response(
        request,
        500,
        "audit.persistence_failed",
        "The required audit record could not be persisted.",
    )


origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
origins = parse_credentialed_cors_origins(
    origins_str,
    node_env=os.getenv("NODE_ENV"),
)
app.state.credentialed_cors_origins = tuple(origins)

# 정적 파일 서빙 (widget.js) - 옵션
STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# API 라우터 등록
app.include_router(api_router, prefix="/api/v1")

# Unsafe routes are classified after router inclusion. Startup fails closed if
# a new mutation has no explicit cookie/public/server policy.
csrf_route_policy_registry = build_csrf_route_policy_registry(app)

# Middleware is registered from inner to outer because Starlette prepends each
# new entry. CORS must wrap CSRF so allowed browser origins can read 401/403,
# while the public conversation boundary must remain outside legacy CORS.
app.add_middleware(
    SessionMiddleware,
    secret_key=resolve_session_signing_secret(
        os.getenv("SECRET_KEY"),
        node_env=os.getenv("NODE_ENV"),
    ),
    https_only=os.getenv("NODE_ENV") == "production",
)
app.add_middleware(
    CsrfProtectionMiddleware,
    registry=csrf_route_policy_registry,
    token_service=csrf_token_service(),
    allowed_origins=tuple(origins),
    enforcement_enabled=csrf_enforcement_enabled(),
    on_denied=record_csrf_denial,
    on_auth_required=record_csrf_auth_required,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)
app.add_middleware(PublicConversationCorsBoundaryMiddleware)

# Added last so this transport sanitizer remains outermost and earlier
# middleware failures cannot expose legacy webhook query credentials through
# the ASGI server access log.
app.add_middleware(WebhookQueryRedactionMiddleware)


@app.get("/")
def root():
    return {"status": "ok", "service": "gateway"}
