import uuid
from dataclasses import asdict, dataclass
from typing import Any, Optional, Type

from apps.shared.db.models.workflow_run import (
    TraceRedactionPolicy,
    TraceRetentionPolicy,
    TraceVisibilityPolicy,
)
from sqlalchemy.orm import Session

SCOPE_GLOBAL = "global"
SCOPE_ORGANIZATION = "organization"
SCOPE_APP = "app"
VALID_SCOPE_TYPES = {SCOPE_GLOBAL, SCOPE_ORGANIZATION, SCOPE_APP}


def _coerce_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ResolvedRedactionPolicy:
    id: Optional[uuid.UUID] = None
    scope_type: str = SCOPE_GLOBAL
    scope_id: Optional[uuid.UUID] = None
    redaction_enabled: bool = True
    raw_payload_storage_enabled: bool = False
    prompt_completion_storage_enabled: bool = True
    pii_detection_enabled: bool = True
    store_redacted_copy_only: bool = True
    sensitive_headers: tuple[str, ...] = ()
    sensitive_json_paths: tuple[str, ...] = ()
    sensitive_keywords: tuple[str, ...] = ()
    regex_rules: tuple[dict[str, Any], ...] = ()
    replacement: str = "[REDACTED]"
    is_active: bool = True

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResolvedRetentionPolicy:
    id: Optional[uuid.UUID] = None
    scope_type: str = SCOPE_GLOBAL
    scope_id: Optional[uuid.UUID] = None
    metadata_retention_days: int = 90
    raw_payload_retention_days: int = 7
    redacted_payload_retention_days: int = 30
    prompt_completion_retention_days: int = 30
    failed_trace_retention_days: int = 90
    retention_action: str = "delete"
    is_active: bool = True

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResolvedVisibilityPolicy:
    id: Optional[uuid.UUID] = None
    scope_type: str = SCOPE_GLOBAL
    scope_id: Optional[uuid.UUID] = None
    owner_trace_access_enabled: bool = True
    owner_redacted_payload_access_enabled: bool = False
    owner_raw_payload_access_enabled: bool = False
    owner_prompt_completion_access_enabled: bool = False
    admin_raw_payload_access_enabled: bool = False
    admin_prompt_completion_access_enabled: bool = False
    deny_owner_trace_access: bool = False
    default_view_level: str = "metadata"
    is_active: bool = True

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class TracePolicyService:
    """앱 > 조직 > 전역 우선순위로 추적 정책을 해석."""

    @staticmethod
    def bootstrap_redaction_policy() -> ResolvedRedactionPolicy:
        return ResolvedRedactionPolicy()

    @staticmethod
    def fail_closed_redaction_policy() -> ResolvedRedactionPolicy:
        return ResolvedRedactionPolicy(
            raw_payload_storage_enabled=False,
            prompt_completion_storage_enabled=False,
            store_redacted_copy_only=True,
        )

    @staticmethod
    def bootstrap_retention_policy() -> ResolvedRetentionPolicy:
        return ResolvedRetentionPolicy()

    @staticmethod
    def bootstrap_visibility_policy() -> ResolvedVisibilityPolicy:
        return ResolvedVisibilityPolicy()

    @staticmethod
    def _scope_candidates(
        app_id: Any = None, organization_id: Any = None
    ) -> list[tuple[str, Optional[uuid.UUID]]]:
        candidates: list[tuple[str, Optional[uuid.UUID]]] = []
        app_uuid = _coerce_uuid(app_id)
        organization_uuid = _coerce_uuid(organization_id)
        if app_uuid:
            candidates.append((SCOPE_APP, app_uuid))
        if organization_uuid:
            candidates.append((SCOPE_ORGANIZATION, organization_uuid))
        candidates.append((SCOPE_GLOBAL, None))
        return candidates

    @staticmethod
    def _resolve_policy(
        db: Optional[Session],
        model: Type,
        fallback: Any,
        app_id: Any = None,
        organization_id: Any = None,
    ) -> Any:
        if db is None:
            return fallback

        for scope_type, scope_id in TracePolicyService._scope_candidates(
            app_id=app_id, organization_id=organization_id
        ):
            query = db.query(model).filter(
                model.scope_type == scope_type,
                model.is_active.is_(True),
            )
            if scope_id is None:
                query = query.filter(model.scope_id.is_(None))
            else:
                query = query.filter(model.scope_id == scope_id)

            policy = query.order_by(model.updated_at.desc()).first()
            if policy:
                return TracePolicyService._to_resolved(policy, fallback)

        return fallback

    @staticmethod
    def _to_resolved(policy: Any, fallback: Any) -> Any:
        data = fallback.model_dump()
        for key in data:
            if hasattr(policy, key):
                value = getattr(policy, key)
                if value is not None:
                    data[key] = value
        if "sensitive_headers" in data:
            data["sensitive_headers"] = tuple(data["sensitive_headers"] or ())
            data["sensitive_json_paths"] = tuple(data["sensitive_json_paths"] or ())
            data["sensitive_keywords"] = tuple(data["sensitive_keywords"] or ())
            data["regex_rules"] = tuple(data["regex_rules"] or ())
        return type(fallback)(**data)

    @staticmethod
    def resolve_redaction_policy(
        db: Optional[Session], app_id: Any = None, organization_id: Any = None
    ) -> ResolvedRedactionPolicy:
        return TracePolicyService._resolve_policy(
            db,
            TraceRedactionPolicy,
            TracePolicyService.bootstrap_redaction_policy(),
            app_id=app_id,
            organization_id=organization_id,
        )

    @staticmethod
    def resolve_retention_policy(
        db: Optional[Session], app_id: Any = None, organization_id: Any = None
    ) -> ResolvedRetentionPolicy:
        return TracePolicyService._resolve_policy(
            db,
            TraceRetentionPolicy,
            TracePolicyService.bootstrap_retention_policy(),
            app_id=app_id,
            organization_id=organization_id,
        )

    @staticmethod
    def resolve_visibility_policy(
        db: Optional[Session], app_id: Any = None, organization_id: Any = None
    ) -> ResolvedVisibilityPolicy:
        return TracePolicyService._resolve_policy(
            db,
            TraceVisibilityPolicy,
            TracePolicyService.bootstrap_visibility_policy(),
            app_id=app_id,
            organization_id=organization_id,
        )

    @staticmethod
    def validate_policy_scope(scope_type: str, scope_id: Any = None) -> Optional[uuid.UUID]:
        # 1차 구현에서는 명확한 전역/앱 범위만 관리 API에서 허용합니다.
        if scope_type not in VALID_SCOPE_TYPES:
            raise ValueError("Invalid policy scope_type")
        scope_uuid = _coerce_uuid(scope_id)
        if scope_type == SCOPE_GLOBAL:
            if scope_id is not None:
                raise ValueError("global_scope_must_not_have_scope_id")
            return None
        if scope_type == SCOPE_APP:
            if scope_uuid is None:
                raise ValueError("app_scope_requires_scope_id")
            return scope_uuid
        raise ValueError("organization_scope_policy_not_supported")

    @staticmethod
    def get_policy(db: Session, model: Type, scope_type: str, scope_id: Any = None):
        scope_uuid = TracePolicyService.validate_policy_scope(scope_type, scope_id)
        query = db.query(model).filter(model.scope_type == scope_type)
        if scope_uuid is None:
            query = query.filter(model.scope_id.is_(None))
        else:
            query = query.filter(model.scope_id == scope_uuid)
        return query.order_by(model.updated_at.desc()).first()

    @staticmethod
    def upsert_policy(
        db: Session,
        model: Type,
        scope_type: str,
        scope_id: Any,
        values: dict[str, Any],
        updated_by: Any,
    ):
        scope_uuid = TracePolicyService.validate_policy_scope(scope_type, scope_id)
        existing = TracePolicyService.get_policy(db, model, scope_type, scope_uuid)
        if existing is None:
            existing = model(scope_type=scope_type, scope_id=scope_uuid)
            db.add(existing)

        for key, value in values.items():
            if value is not None and hasattr(existing, key):
                setattr(existing, key, value)

        existing.updated_by = _coerce_uuid(updated_by)
        db.commit()
        db.refresh(existing)
        return existing
