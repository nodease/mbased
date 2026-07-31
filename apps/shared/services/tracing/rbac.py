import uuid
from typing import Any, Optional, Protocol

from apps.shared.permissions import (
    AUTH_STATE_NONE,
    AUTH_STATE_RANK,
    auth_state_at_least,
    normalize_auth_state,
)
from apps.shared.services.permissions import get_effective_workflow_auth_state
from sqlalchemy.orm import Session

TRACE_SYSTEM_ADMIN_PERMISSION = "tracing.system_admin"
TRACE_AUTH_STATE_RANK = AUTH_STATE_RANK


class TraceRbacProvider(Protocol):
    """추적 전용 권한 확인을 위한 RBAC 어댑터 경계."""

    def is_system_admin(self, db: Session, user: Any) -> bool:
        """현재 사용자가 추적 정책을 관리할 수 있는지 반환."""


class DenyAllTraceRbacProvider:
    """공용 사용자/RBAC 모델이 연결되기 전까지 사용하는 기본 제공자."""

    def is_system_admin(self, db: Session, user: Any) -> bool:
        return False


class TraceRbacService:
    """추적 접근 제어에서 실제 RBAC 모델을 조회하는 작은 통합 지점."""

    _provider: TraceRbacProvider = DenyAllTraceRbacProvider()

    @classmethod
    def configure_provider(cls, provider: TraceRbacProvider) -> None:
        cls._provider = provider

    @classmethod
    def reset_provider(cls) -> None:
        cls._provider = DenyAllTraceRbacProvider()

    @classmethod
    def is_system_admin(cls, db: Session, user: Any) -> bool:
        if user is None:
            return False
        try:
            return bool(cls._provider.is_system_admin(db, user))
        except Exception:
            return False

    @classmethod
    def get_workflow_auth_state(
        cls,
        db: Optional[Session],
        user: Any,
        workflow_id: Any,
        organization_id: Any = None,
    ) -> Optional[str]:
        """사용자가 workflow에 대해 가진 가장 높은 MVP auth_state를 반환합니다."""
        user_id = cls._coerce_uuid(getattr(user, "id", None))
        workflow_uuid = cls._coerce_uuid(workflow_id)
        if db is None or user_id is None or workflow_uuid is None:
            return None

        auth_state = get_effective_workflow_auth_state(
            db,
            user_id,
            workflow_uuid,
            organization_id=organization_id,
        )
        if auth_state == AUTH_STATE_NONE:
            return None
        return auth_state

    @classmethod
    def auth_state_at_least(cls, auth_state: Any, minimum: str) -> bool:
        return auth_state_at_least(auth_state, minimum)

    @staticmethod
    def normalize_auth_state(auth_state: Any) -> str:
        return normalize_auth_state(auth_state)

    @staticmethod
    def _coerce_uuid(value: Any) -> Optional[uuid.UUID]:
        if value is None or isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None
