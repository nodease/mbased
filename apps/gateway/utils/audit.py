"""
계층 A — 사용자 행동 캡처 데코레이터

인증된 엔드포인트(`user: User = Depends(get_current_user)`)에 부착해
의미 있는 사용자 행동을 기록한다.

- actor: 핸들러 인자에서 User 인스턴스를 찾아 스냅샷(id/email/name)을 구성
- ip / user_agent: 인자에서 Request 인스턴스를 찾아 추출
- status: 핸들러 정상 완료 → success, 예외 → failure(기록 후 예외 재전파)
- target_id: target_param으로 지정한 인자에서 추출
- 요청 동안 actor contextvar를 세팅 → 같은 요청이 일으킨 데이터 변경(계층 B)에도 actor 전파

주의: 로그인 등 인증 전 엔드포인트는 actor가 인자에 없으므로, 이 데코레이터 대신
핸들러 내부에서 record_audit()을 명시적으로 호출한다.
"""

import asyncio
import functools
import logging
from collections.abc import Callable, Mapping
from typing import Any, Optional

from fastapi import HTTPException, Request

from apps.shared.audit.context import (
    AuditActor,
    clear_current_actor,
    get_current_metadata,
    set_current_actor,
)
from apps.shared.audit.logger import record_audit
from apps.shared.db.models.user import User

logger = logging.getLogger(__name__)


def _safe_failure_metadata(exc: Exception) -> dict[str, str | int]:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code and len(code) <= 100:
        return {"error_code": code}
    if isinstance(exc, HTTPException):
        return {"error_code": "http.request_failed", "status_code": exc.status_code}
    return {"error_code": "internal.request_failed"}


def _find_user(kwargs) -> Optional[User]:
    return next((value for value in kwargs.values() if isinstance(value, User)), None)


def _find_request(kwargs) -> Optional[Request]:
    return next(
        (value for value in kwargs.values() if isinstance(value, Request)), None
    )


def _build_actor(user: Optional[User]):
    if user is None:
        return None
    snapshot = {
        "id": str(user.id),
        "email": getattr(user, "email", None),
        "name": getattr(user, "name", None),
    }
    return AuditActor(actor_id=str(user.id), actor_type="user", snapshot=snapshot)


def _request_metadata(request: Optional[Request]) -> dict:
    meta = get_current_metadata()
    if request is None:
        return meta
    if request.client and "ip" not in meta:
        meta["ip"] = request.client.host
    meta.setdefault("user_agent", request.headers.get("user-agent"))
    meta.setdefault("request_id", getattr(request.state, "request_id", None))
    return meta


def audit(
    action: str,
    *,
    target_param: Optional[str] = None,
    target_type: Optional[str] = None,
    metadata_factory: Optional[
        Callable[[dict[str, Any]], Mapping[str, Any]]
    ] = None,
):
    """
    Args:
        action: 기록할 행동 타입. AuditAction 상수를 넘긴다(예: AuditAction.WORKFLOW_DEPLOY).
        target_param: target_id를 담은 핸들러 인자 이름(예: "workflow_id").
        target_type: 대상 리소스 타입(예: "workflow"). 생략 시 action의 접두사를 사용한다.
        metadata_factory: handler 인자에서 domain-safe metadata만 만드는 함수.
    """

    def decorator(func):
        resolved_target_type = target_type or action.split(".")[0]

        def _emit(kwargs, status, extra_meta=None):
            actor = _build_actor(_find_user(kwargs))
            metadata = _request_metadata(_find_request(kwargs))
            if actor is not None:
                metadata["actor"] = actor.snapshot
            if metadata_factory is not None:
                try:
                    domain_metadata = metadata_factory(kwargs)
                except Exception:
                    logger.warning(
                        "Audit metadata factory failed: action=%s",
                        action,
                    )
                else:
                    if isinstance(domain_metadata, Mapping):
                        metadata.update(domain_metadata)
            if extra_meta:
                metadata.update(extra_meta)

            record_audit(
                action=action,
                category="action",
                actor_id=actor.actor_id if actor else None,
                actor_type=actor.actor_type if actor else "system",
                target_type=resolved_target_type,
                target_id=kwargs.get(target_param) if target_param else None,
                status=status,
                metadata=metadata,
            )

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            token = set_current_actor(_build_actor(_find_user(kwargs)))
            try:
                result = await func(*args, **kwargs)
            except Exception as e:
                _emit(kwargs, "failure", _safe_failure_metadata(e))
                raise
            else:
                _emit(kwargs, "success")
                return result
            finally:
                clear_current_actor(token)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            token = set_current_actor(_build_actor(_find_user(kwargs)))
            try:
                result = func(*args, **kwargs)
            except Exception as e:
                _emit(kwargs, "failure", _safe_failure_metadata(e))
                raise
            else:
                _emit(kwargs, "success")
                return result
            finally:
                clear_current_actor(token)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
