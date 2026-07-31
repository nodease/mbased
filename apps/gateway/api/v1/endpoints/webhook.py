"""Webhook 수신 및 캡처 엔드포인트"""

import asyncio
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from apps.gateway.api.deps import (
    get_deployment_runtime_policy,
    get_webhook_ingress_policy,
)
from apps.gateway.application.webhook_ingress import (
    JsonObject,
    WebhookIngressError,
    WebhookIngressPolicy,
    WebhookIngressRequestMetadata,
)
from apps.gateway.application.webhook_ingress.errors import (
    PayloadInvalidError,
    PayloadTimeoutError,
)
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.auth.permissions import ensure_workflow_permission
from apps.gateway.middleware.webhook_query_redaction import (
    WEBHOOK_QUERY_TOKEN_PRESENT_STATE_KEY,
    webhook_query_token_present,
)
from apps.gateway.services.app_auth_secret_service import AppAuthSecretService
from apps.gateway.services.workflow_budget_service import WorkflowBudgetService
from apps.shared.celery_app import celery_app
from apps.shared.db.models.app import App
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.domain.deployment_runtime_policy import (
    SURFACE_WEBHOOK_RUN,
    DeploymentRuntimePolicy,
    is_deployment_type_allowed_for_surface,
)
from apps.shared.domain.app_auth_secret import APP_AUTH_SECRET_PREFIX
from apps.shared.db.session import get_db
from apps.shared.services.workflow_task_publisher import send_workflow_task

logger = logging.getLogger(__name__)
router = APIRouter()

# 캡처 세션용 메모리 저장소 (서버 메모리)
CAPTURE_SESSIONS: Dict[str, Dict[str, Any]] = {}
CAPTURE_SESSION_TTL_SECONDS = 60
CAPTURE_PREVIEW_MAX_DEPTH = 4
CAPTURE_PREVIEW_MAX_ITEMS = 50
CAPTURE_PREVIEW_MAX_KEY_CHARS = 200
CAPTURE_PREVIEW_MAX_STRING_CHARS = 2000
_REDACTED_KEY = "[REDACTED: sensitive key]"
_REDACTED_VALUE = "[REDACTED: sensitive value]"
_TRUNCATED_VALUE = "[TRUNCATED]"
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_VALUE_PATTERNS = [
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}\b"),
    re.compile(r"(?i)\b(?:sk|pk|rk|api)[-_][A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(
        rf"(?<![A-Za-z0-9_-]){re.escape(APP_AUTH_SECRET_PREFIX)}[A-Za-z0-9_-]{{32,}}(?![A-Za-z0-9_-])"
    ),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
]
_SENSITIVE_KEY_PARTS = {
    "access_key",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "credentials",
    "password",
    "private_key",
    "raw_body",
    "raw_content",
    "raw_payload",
    "refresh_token",
    "secret",
    "secret_key",
    "set_cookie",
    "signing_key",
    "signing_secret",
    "token",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_capture_session_expired(session: Dict[str, Any], now: datetime | None = None) -> bool:
    expires_at = session.get("expires_at")
    if not isinstance(expires_at, datetime):
        return True
    return expires_at <= (now or _now_utc())


def _cleanup_expired_capture_session(url_slug: str, now: datetime | None = None) -> None:
    session = CAPTURE_SESSIONS.get(url_slug)
    if session and _is_capture_session_expired(session, now):
        CAPTURE_SESSIONS.pop(url_slug, None)


def _capture_session_for_webhook(url_slug: str) -> Dict[str, Any] | None:
    _cleanup_expired_capture_session(url_slug)
    session = CAPTURE_SESSIONS.get(url_slug)
    if not session or session.get("status") != "waiting":
        return None
    return session


def _sensitive_key_path(key_path: str | None) -> bool:
    if not key_path:
        return False
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key_path)
    tokens = [
        token.lower()
        for token in re.split(r"[^A-Za-z0-9]+", normalized)
        if token
    ]
    for index, token in enumerate(tokens):
        if token in _SENSITIVE_KEY_PARTS:
            return True
        if index + 1 < len(tokens):
            joined = f"{token}_{tokens[index + 1]}"
            if joined in _SENSITIVE_KEY_PARTS:
                return True
    return False


def _redact_capture_string(value: str) -> str:
    sanitized = _CONTROL_CHARS_RE.sub(" ", value)
    for pattern in _SECRET_VALUE_PATTERNS:
        sanitized = pattern.sub(_REDACTED_VALUE, sanitized)
    if len(sanitized) > CAPTURE_PREVIEW_MAX_STRING_CHARS:
        return sanitized[:CAPTURE_PREVIEW_MAX_STRING_CHARS] + f"\n{_TRUNCATED_VALUE}"
    return sanitized


def _redact_capture_key_text(value: Any) -> str:
    sanitized = _CONTROL_CHARS_RE.sub(" ", str(value))
    if any(pattern.search(sanitized) for pattern in _SECRET_VALUE_PATTERNS):
        return _REDACTED_KEY
    if len(sanitized) > CAPTURE_PREVIEW_MAX_KEY_CHARS:
        return sanitized[:CAPTURE_PREVIEW_MAX_KEY_CHARS] + f"\n{_TRUNCATED_VALUE}"
    return sanitized


def _unique_capture_preview_key(preview: dict[str, Any], key_text: str) -> str:
    if key_text not in preview:
        return key_text

    suffix = 2
    while f"{key_text}#{suffix}" in preview:
        suffix += 1
    return f"{key_text}#{suffix}"


def _redact_capture_payload(
    value: Any,
    *,
    key_path: str | None = None,
    depth: int = CAPTURE_PREVIEW_MAX_DEPTH,
) -> Any:
    if _sensitive_key_path(key_path):
        return _REDACTED_VALUE
    if value is None:
        return None
    if depth <= 0:
        return _TRUNCATED_VALUE
    if isinstance(value, str):
        return _redact_capture_string(value)
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        preview: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= CAPTURE_PREVIEW_MAX_ITEMS:
                preview["__truncated__"] = True
                break
            key_text = str(key)
            preview_key = _unique_capture_preview_key(
                preview,
                _redact_capture_key_text(key_text),
            )
            child_path = f"{key_path}.{key_text}" if key_path else key_text
            preview[preview_key] = _redact_capture_payload(
                child,
                key_path=child_path,
                depth=depth - 1,
            )
        return preview
    if isinstance(value, (list, tuple, set)):
        preview = []
        for index, child in enumerate(value):
            if index >= CAPTURE_PREVIEW_MAX_ITEMS:
                preview.append(_TRUNCATED_VALUE)
                break
            preview.append(
                _redact_capture_payload(child, key_path=key_path, depth=depth - 1)
            )
        return preview
    return _redact_capture_string(str(value))


def _ensure_capture_access(db: Session, current_user: User, url_slug: str) -> App:
    app = db.query(App).filter(App.url_slug == url_slug).first()
    if not app or not app.workflow_id:
        raise HTTPException(status_code=404, detail="App not found")
    ensure_workflow_permission(db, current_user, app.workflow_id, "deploy")
    return app


def _capture_session_for_request(
    url_slug: str,
    capture_id: str,
    current_user: User,
) -> Dict[str, Any]:
    _cleanup_expired_capture_session(url_slug)
    session = CAPTURE_SESSIONS.get(url_slug)
    if not session:
        raise HTTPException(status_code=404, detail="No capture session found")
    if (
        session.get("capture_id") != capture_id
        or session.get("requested_by") != str(current_user.id)
    ):
        raise HTTPException(status_code=404, detail="No capture session found")
    return session


def _raw_header_values(request: Request, expected_name: bytes) -> tuple[bytes, ...]:
    return tuple(
        value
        for name, value in request.scope.get("headers", ())
        if name.lower() == expected_name
    )


def _webhook_request_metadata(request: Request) -> WebhookIngressRequestMetadata:
    raw_query = request.scope.get("query_string", b"")
    state = request.scope.get("state", {})
    return WebhookIngressRequestMetadata(
        query_token_present=bool(
            state.get(WEBHOOK_QUERY_TOKEN_PRESENT_STATE_KEY, False)
        )
        or webhook_query_token_present(raw_query),
        authorization_headers=_raw_header_values(request, b"authorization"),
        webhook_secret_headers=_raw_header_values(request, b"x-webhook-secret"),
        content_type_headers=_raw_header_values(request, b"content-type"),
        content_encoding_headers=_raw_header_values(request, b"content-encoding"),
        content_length_headers=_raw_header_values(request, b"content-length"),
    )


async def _read_webhook_payload(
    request: Request,
    ingress_policy: WebhookIngressPolicy,
) -> JsonObject:
    deadline = ingress_policy.start_deadline()
    body = bytearray()
    stream = request.stream().__aiter__()

    while True:
        timeout = ingress_policy.remaining_seconds(deadline)
        try:
            chunk = await asyncio.wait_for(anext(stream), timeout=timeout)
        except StopAsyncIteration:
            break
        except TimeoutError:
            raise PayloadTimeoutError() from None
        except ClientDisconnect:
            raise PayloadInvalidError() from None
        except Exception:
            raise PayloadInvalidError() from None

        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise PayloadInvalidError()
        ingress_policy.validate_actual_body_size(len(body) + len(chunk))
        body.extend(chunk)

    ingress_policy.ensure_within_deadline(deadline)
    return ingress_policy.parse_json(bytes(body), deadline)


def _raise_webhook_http_error(error: WebhookIngressError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.code) from None


def run_webhook_workflow(
    deployment_id: str,
    payload: Dict[str, Any],
    app_created_by: str,
    workflow_id: str,
    app_id: str,
    organization_id: str,
):
    """
    백그라운드에서 워크플로우를 Celery 태스크로 실행하는 함수

    Args:
        deployment_id: 배포 ID
        payload: Webhook Payload (JSON)
        app_created_by: 앱 생성자 ID
        workflow_id: 워크플로우 ID
    """
    try:
        # execution_context 구성
        execution_context = {
            "user_id": app_created_by,
            "workflow_id": workflow_id,
            "organization_id": organization_id,
            "app_id": app_id,
            "trigger_mode": "webhook",
            "deployment_id": deployment_id,
        }

        # Celery 태스크로 워크플로우 실행 위임 (비동기, 결과 대기 안 함)
        # 배포 그래프 데이터는 Celery Worker에서 조회
        send_workflow_task(
            celery_app,
            "workflow.execute_by_deployment",
            args=[deployment_id, payload, execution_context],
        )

    except Exception as exc:
        logger.error(
            "Webhook workflow publish failed: error_type=%s",
            type(exc).__name__,
        )


@router.post("/hooks/{url_slug}")
async def receive_webhook(
    url_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    runtime_policy: Annotated[
        DeploymentRuntimePolicy,
        Depends(get_deployment_runtime_policy),
    ],
    ingress_policy: Annotated[
        WebhookIngressPolicy,
        Depends(get_webhook_ingress_policy),
    ],
    db: Session = Depends(get_db),
):
    """
    Webhook 수신 엔드포인트

    - 캡처 모드: Payload를 메모리에 저장
    - 실행 모드: WorkflowEngine을 BackgroundTasks로 실행

    인증 방식은 Bearer 또는 X-Webhook-Secret header 중 정확히 하나다.
    Query token은 지원하지 않는다.
    """
    # 1. App 조회 (url_slug로)
    app = db.query(App).filter(App.url_slug == url_slug).first()
    if not app:
        raise HTTPException(status_code=404, detail="App not found")

    # 2. 인증과 bounded JSON validation은 downstream action보다 먼저 끝낸다.
    try:
        request_metadata = _webhook_request_metadata(request)
        ingress_policy.authenticate(
            request_metadata,
            credential_verifier=lambda candidate: AppAuthSecretService.authenticate(
                app,
                candidate,
            ),
        )
        ingress_policy.validate_payload_metadata(request_metadata)
        payload = await _read_webhook_payload(request, ingress_policy)
    except WebhookIngressError as error:
        _raise_webhook_http_error(error)

    # 4. 캡처 모드 확인
    capture_session = _capture_session_for_webhook(url_slug)
    if capture_session is not None:
        # 캡처 모드: raw payload를 저장하지 않고 redacted/capped preview만 보존한다.
        capture_session["payload"] = _redact_capture_payload(payload)
        capture_session["captured_at"] = _now_utc()
        capture_session["status"] = "captured"
        return {"status": "captured", "message": "Payload captured successfully"}

    # 5. Active Deployment 조회
    if not app.active_deployment_id:
        raise HTTPException(
            status_code=400,
            detail="No active deployment. Please deploy the workflow first.",
        )

    deployment = (
        db.query(WorkflowDeployment)
        .filter(
            WorkflowDeployment.id == app.active_deployment_id,
            WorkflowDeployment.app_id == app.id,
            WorkflowDeployment.is_active.is_(True),
            WorkflowDeployment.type == DeploymentType.WEBHOOK,
        )
        .first()
    )
    if not deployment or not is_deployment_type_allowed_for_surface(
        deployment.type,
        SURFACE_WEBHOOK_RUN,
        policy=runtime_policy,
    ):
        raise HTTPException(status_code=404, detail="Active deployment not found")

    # 5-1. 예산 초과 차단 — background 예약 전에 429로 끝낸다 (BGT-REQ-030).
    WorkflowBudgetService.ensure_workflow_budget_allows_execution(
        db,
        workflow_id=app.workflow_id,
        trigger_mode="webhook",
        actor_id=None,
    )

    # 6. 실행 모드: Celery 태스크로 워크플로우 실행 위임
    background_tasks.add_task(
        run_webhook_workflow,
        str(deployment.id),
        payload,
        str(app.created_by),
        str(app.workflow_id) if app.workflow_id else None,
        str(app.id),
        str(app.organization_id) if app.organization_id else None,
    )

    return {
        "status": "accepted",
        "message": "Webhook received, processing in background",
    }


@router.get("/hooks/{url_slug}/capture/start")
def start_capture(
    url_slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """캡처 세션 시작"""
    _ensure_capture_access(db, current_user, url_slug)
    now = _now_utc()
    expires_at = now + timedelta(seconds=CAPTURE_SESSION_TTL_SECONDS)

    # 캡처 세션 생성. capture_id는 status 조회용 1회성 nonce다.
    capture_id = secrets.token_urlsafe(16)
    CAPTURE_SESSIONS[url_slug] = {
        "capture_id": capture_id,
        "created_at": now,
        "expires_at": expires_at,
        "payload": None,
        "requested_by": str(current_user.id),
        "status": "waiting",
    }
    return {
        "status": "waiting",
        "capture_id": capture_id,
        "expires_at": expires_at.isoformat(),
        "message": "Capture session started",
    }


@router.get("/hooks/{url_slug}/capture/status")
def get_capture_status(
    url_slug: str,
    capture_id: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """캡처 상태 조회"""
    _ensure_capture_access(db, current_user, url_slug)
    session = _capture_session_for_request(url_slug, capture_id, current_user)

    # 캡처 완료 시 자동 정리
    if session["status"] == "captured":
        payload = session["payload"]
        CAPTURE_SESSIONS.pop(url_slug, None)  # 메모리 정리
        return {
            "status": "captured",
            "payload": payload,
            "payload_redacted": True,
        }

    return {
        "status": session["status"],
        "capture_id": capture_id,
        "expires_at": session["expires_at"].isoformat(),
        "payload": None,
    }


@router.post("/hooks/{url_slug}/capture/cancel")
def cancel_capture(
    url_slug: str,
    capture_id: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """캡처 세션 취소"""
    _ensure_capture_access(db, current_user, url_slug)
    _capture_session_for_request(url_slug, capture_id, current_user)
    CAPTURE_SESSIONS.pop(url_slug, None)
    return {"status": "cancelled"}
