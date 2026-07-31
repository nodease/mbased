from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session

from apps.gateway.services.admin_audit_display_service import AdminAuditDisplayService
from apps.shared.db.models.audit_log import AuditLog, AuditStatus
from apps.shared.db.models.team import Team, TeamAuditPermission, TeamMembership
from apps.shared.db.models.user import User
from apps.shared.schemas.audit import (
    AdminAuditLogListResponse,
    AdminAuditLogSchema,
    AuditDisplayReference,
    AuditLogDetailResponse,
)
from apps.shared.permissions import AUTH_STATE_NONE
from apps.shared.schemas.permission import AUDIT_AUTH_STATE_RANK
from apps.shared.domain.schedule_dispatch import (
    validate_operation_correlation,
    validate_outcome_resolution,
)
from apps.shared.services.permission_audit import record_resource_permission_denied
from apps.shared.services.permissions import has_organization_manager_permission

KST = ZoneInfo("Asia/Seoul")
AUDIT_READER_RANK = AUDIT_AUTH_STATE_RANK["auditor"]
AUDIT_RESOURCE_TYPE = "audit"
AUDIT_READ_ACTION = "read"
DETAIL_METADATA_KEYS = {
    "affected_resource_source_count",
    "denial_reason",
    "organization_id",
    "policy_reason",
    "reason",
    "request_id",
    "requested_operation",
    "required_permission",
    "requested_action",
    "resource_id",
    "resource_type",
    "summary",
    "target_user_id",
    "team_id",
}
_DETAIL_UUID_KEYS = {
    "organization_id",
    "resource_id",
    "target_user_id",
    "team_id",
}
_DETAIL_STRING_KEYS = {
    "denial_reason",
    "reason",
    "request_id",
    "requested_operation",
    "required_permission",
    "requested_action",
    "resource_type",
}
_DETAIL_REQUIRED_PERMISSIONS = {"security_alert.manage"}
_DETAIL_REQUESTED_OPERATIONS = {
    "security_alert.list",
    "security_alert.summary",
    "security_alert.detail",
    "security_alert.evidence.list",
    "security_alert.acknowledge",
    "security_alert.resolve",
    "security_alert.reopen",
}
_DETAIL_DENIAL_REASONS = {"organization_manager_required"}
_DETAIL_RESOURCE_TYPES = {
    "workflow",
    "knowledge_base",
    "llm_credential",
    "mail_credential",
}
_DETAIL_POLICY_REASONS = {
    "access_management.self_control_forbidden",
    "access_management.last_active_manager",
    "access_management.manager_override_active",
    "access_management.member_state_not_manageable",
    "access_management.target_user_inactive",
    "access_management.stale_state",
}
SECRET_METADATA_KEYS = {
    "authorization",
    "encrypted_config",
    "payload",
    "raw_payload",
    "api_key",
    "token",
    "secret",
    "password",
}
_UUID_FIELDS = {
    "grantee_organization_id",
    "knowledge_base_id",
    "llm_credential_id",
    "mail_credential_id",
    "organization_id",
    "team_id",
    "user_id",
    "workflow_id",
}
_MEMBERSHIP_STATES = {"invited", "active", "suspended", "removed"}
_ORGANIZATION_AUTH_STATES = {"member", "manager"}
_RESOURCE_AUTH_STATES = {"none", "viewer", "operator", "builder", "manager"}
_CHANGE_SUMMARY_SPECS: dict[tuple[str, str], tuple[str, str, frozenset[str]]] = {
    (
        "organization.member.update",
        "organization_membership",
    ): (
        "update",
        "organization_id",
        frozenset(
            {
                "organization_id",
                "user_id",
                "membership_state",
                "organization_auth_state",
            }
        ),
    ),
    (
        "team_membership.created",
        "team_membership",
    ): (
        "create",
        "grantee_organization_id",
        frozenset({"grantee_organization_id", "team_id", "user_id"}),
    ),
    (
        "team_membership.deleted",
        "team_membership",
    ): (
        "delete",
        "grantee_organization_id",
        frozenset({"grantee_organization_id", "team_id", "user_id"}),
    ),
    (
        "user_app_creation_permission.created",
        "user_app_creation_permission",
    ): (
        "create",
        "grantee_organization_id",
        frozenset({"grantee_organization_id", "user_id"}),
    ),
    (
        "user_app_creation_permission.deleted",
        "user_app_creation_permission",
    ): (
        "delete",
        "grantee_organization_id",
        frozenset({"grantee_organization_id", "user_id"}),
    ),
}

for _resource_name, _resource_field in (
    ("workflow", "workflow_id"),
    ("knowledge", "knowledge_base_id"),
    ("llm", "llm_credential_id"),
):
    _target_type = f"user_{_resource_name}_permission"
    _fields = frozenset(
        {"grantee_organization_id", "user_id", _resource_field, "auth_state"}
    )
    for _operation in ("created", "updated", "deleted"):
        _CHANGE_SUMMARY_SPECS[(f"{_target_type}.{_operation}", _target_type)] = (
            {"created": "create", "updated": "update", "deleted": "delete"}[_operation],
            "grantee_organization_id",
            _fields,
        )


@dataclass(frozen=True)
class AuditLogPeriod:
    start_at: datetime | None = None
    end_at: datetime | None = None


@dataclass(frozen=True)
class AdminAuditLogFilters:
    actor_id: UUID | None = None
    action: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    status: AuditStatus | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class AdminPermissionGuard:
    @staticmethod
    def require_audit_reader(db: Session, user: User, organization_id: Any) -> None:
        if has_organization_manager_permission(db, user.id, organization_id):
            return
        if _has_team_audit_reader_permission(db, user.id, organization_id):
            return
        _raise_audit_reader_denied(user.id, organization_id)


def _raise_audit_reader_denied(user_id: Any, organization_id: Any) -> None:
    record_resource_permission_denied(
        user_id=user_id,
        resource_type=AUDIT_RESOURCE_TYPE,
        resource_id=organization_id,
        action=AUDIT_READ_ACTION,
        effective_auth_state=AUTH_STATE_NONE,
        organization_id=organization_id,
    )
    exc = HTTPException(status_code=403, detail="Forbidden")
    setattr(exc, "audit_recorded", True)
    raise exc


class AdminAuditLogService:
    @staticmethod
    def encode_cursor(item: Any) -> str:
        payload = json.dumps(
            {"occurred_at": item.occurred_at.isoformat(), "id": str(item.id)},
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
        if not cursor or len(cursor) > 512:
            raise HTTPException(status_code=400, detail="Invalid cursor")
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = base64.b64decode(padded, altchars=b"-_", validate=True)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            occurred_at = datetime.fromisoformat(payload["occurred_at"])
            audit_log_id = UUID(payload["id"])
            if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
                raise ValueError
        except (
            binascii.Error,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ) as error:
            raise HTTPException(status_code=400, detail="Invalid cursor") from error
        return occurred_at, audit_log_id

    @staticmethod
    def resolve_period(
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> AuditLogPeriod:
        start = _ensure_timezone(start_at)
        end = _ensure_timezone(end_at)
        if start is not None and end is not None and end <= start:
            raise HTTPException(status_code=400, detail="Invalid period")
        return AuditLogPeriod(start_at=start, end_at=end)

    @staticmethod
    def list_audit_logs(
        db: Session,
        current_user: User,
        organization_id: Any,
        filters: AdminAuditLogFilters | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> AdminAuditLogListResponse:
        AdminPermissionGuard.require_audit_reader(db, current_user, organization_id)
        cursor_position = AdminAuditLogService.decode_cursor(cursor) if cursor else None
        filters = filters or AdminAuditLogFilters()
        query = _filtered_query(db, organization_id, filters)
        total = query.count() if cursor_position is None else None
        if cursor_position:
            cursor_time, cursor_id = cursor_position
            query = query.filter(
                or_(
                    AuditLog.occurred_at < cursor_time,
                    and_(
                        AuditLog.occurred_at == cursor_time,
                        AuditLog.id < cursor_id,
                    ),
                )
            )
        rows = (
            query.order_by(desc(AuditLog.occurred_at), desc(AuditLog.id))
            .limit(limit + 1)
            .all()
        )
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = (
            AdminAuditLogService.encode_cursor(items[-1])
            if has_more and items
            else None
        )
        displays = AdminAuditDisplayService.resolve(db, organization_id, items)
        return AdminAuditLogListResponse(
            total=total,
            next_cursor=next_cursor,
            items=[
                _list_item(
                    item,
                    actor_display=displays.actors.get(item.id),
                    target_display=displays.targets.get(item.id),
                )
                for item in items
            ],
        )

    @staticmethod
    def get_audit_log_detail(
        db: Session,
        current_user: User,
        organization_id: Any,
        audit_log_id: Any,
    ) -> AuditLogDetailResponse:
        AdminPermissionGuard.require_audit_reader(db, current_user, organization_id)
        filters = AdminAuditLogFilters()
        item = (
            _filtered_query(db, organization_id, filters)
            .filter(AuditLog.id == audit_log_id)
            .first()
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Audit log not found")
        audit_metadata = _detail_metadata(item)
        change_summary = _change_summary(item, organization_id)
        displays = AdminAuditDisplayService.resolve(
            db,
            organization_id,
            [item],
            detail_metadata=audit_metadata,
            change_summary=change_summary,
        )
        return AuditLogDetailResponse(
            **_list_item(
                item,
                actor_display=displays.actors.get(item.id),
                target_display=displays.targets.get(item.id),
            ).model_dump(),
            audit_metadata=audit_metadata,
            change_summary=change_summary,
            resolved_references=displays.references,
        )

    @staticmethod
    def sanitize_audit_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        if not metadata:
            return {}
        sanitized: dict[str, Any] = {}
        for key, value in metadata.items():
            if _is_secret_key(key):
                continue
            sanitized[key] = _sanitize_metadata_value(value)
        return sanitized


def _ensure_timezone(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=KST)
    return value


def _has_team_audit_reader_permission(
    db: Session,
    user_id: Any,
    organization_id: Any,
) -> bool:
    permissions = (
        db.query(TeamAuditPermission)
        .join(
            TeamMembership,
            (TeamMembership.team_id == TeamAuditPermission.team_id)
            & (
                TeamMembership.grantee_organization_id
                == TeamAuditPermission.grantee_organization_id
            ),
        )
        .join(Team, Team.id == TeamAuditPermission.team_id)
        .filter(
            TeamMembership.user_id == user_id,
            TeamMembership.grantee_organization_id == organization_id,
            TeamAuditPermission.grantee_organization_id == organization_id,
            TeamAuditPermission.target_organization_id == organization_id,
            Team.organization_id == organization_id,
            Team.is_active.is_(True),
        )
        .all()
    )
    return any(_has_audit_reader_rank(_audit_auth_state(row)) for row in permissions)


def _audit_auth_state(row: Any) -> str | None:
    return getattr(row, "auth_state", None)


def _has_audit_reader_rank(auth_state: str | None) -> bool:
    return AUDIT_AUTH_STATE_RANK.get(auth_state, 0) >= AUDIT_READER_RANK


def _filtered_query(
    db: Session,
    organization_id: Any,
    filters: AdminAuditLogFilters,
):
    query = db.query(AuditLog).filter(
        AuditLog.audit_metadata["organization_id"].astext == str(organization_id)
    )
    if filters.actor_id is not None:
        query = query.filter(AuditLog.actor_id == filters.actor_id)
    if filters.action is not None:
        query = query.filter(AuditLog.action == filters.action)
    if filters.target_type is not None:
        query = query.filter(AuditLog.target_type == filters.target_type)
    if filters.target_id is not None:
        query = query.filter(AuditLog.target_id == filters.target_id)
    if filters.status is not None:
        query = query.filter(AuditLog.status == filters.status)
    if filters.start_at is not None:
        query = query.filter(AuditLog.occurred_at >= filters.start_at)
    if filters.end_at is not None:
        query = query.filter(AuditLog.occurred_at < filters.end_at)
    return query


def _list_item(
    item: AuditLog,
    *,
    actor_display: AuditDisplayReference | None = None,
    target_display: AuditDisplayReference | None = None,
) -> AdminAuditLogSchema:
    request_id = (item.audit_metadata or {}).get("request_id")
    return AdminAuditLogSchema(
        id=item.id,
        occurred_at=item.occurred_at,
        actor_id=item.actor_id,
        actor_display=actor_display,
        actor_type=item.actor_type,
        category=item.category,
        action=item.action,
        target_type=item.target_type,
        target_id=item.target_id,
        target_display=target_display,
        workflow_run_id=item.workflow_run_id,
        workflow_node_run_id=item.workflow_node_run_id,
        status=item.status,
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _detail_metadata(item: AuditLog) -> dict[str, Any]:
    sanitized = AdminAuditLogService.sanitize_audit_metadata(item.audit_metadata)
    detail: dict[str, Any] = {}
    for key in DETAIL_METADATA_KEYS:
        if key not in sanitized:
            continue
        value = _safe_detail_metadata_value(key, sanitized[key])
        if value is not None:
            detail[key] = value
    if (
        item.action == "schedule_dispatch.outcome_reviewed"
        and item.target_type == "schedule_dispatch_claim"
    ):
        correlation = sanitized.get("operation_correlation_id")
        resolution = sanitized.get("outcome_resolution_code")
        try:
            detail["operation_correlation_id"] = validate_operation_correlation(
                correlation
            )
            detail["outcome_resolution_code"] = validate_outcome_resolution(resolution)
        except (TypeError, ValueError):
            detail.pop("operation_correlation_id", None)
            detail.pop("outcome_resolution_code", None)
    return detail


def _sanitize_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        return AdminAuditLogService.sanitize_audit_metadata(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_metadata_value(item) for item in value]
    return value


def _safe_detail_metadata_value(key: str, value: Any) -> Any | None:
    if key in _DETAIL_UUID_KEYS:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            return None
    if key == "affected_resource_source_count":
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else None
        )
    if key == "policy_reason":
        return value if value in _DETAIL_POLICY_REASONS else None
    if key == "resource_type":
        return value if value in _DETAIL_RESOURCE_TYPES else None
    if key == "required_permission":
        return value if value in _DETAIL_REQUIRED_PERMISSIONS else None
    if key == "requested_operation":
        return value if value in _DETAIL_REQUESTED_OPERATIONS else None
    if key == "denial_reason":
        return value if value in _DETAIL_DENIAL_REASONS else None
    if key in _DETAIL_STRING_KEYS:
        return value if isinstance(value, str) else None
    if key == "summary":
        return value if isinstance(value, (dict, list, str, int, float, bool)) else None
    return None


def _change_summary(item: AuditLog, organization_id: Any) -> dict[str, Any] | None:
    if item.target_type is None:
        return None
    spec = _CHANGE_SUMMARY_SPECS.get((item.action, item.target_type))
    if spec is None:
        return None
    operation, provenance_field, allowed_fields = spec
    required_sides = {
        "create": ("after",),
        "delete": ("before",),
        "update": ("before", "after"),
    }[operation]
    snapshots = {"before": item.before, "after": item.after}
    safe_sides: dict[str, dict[str, Any] | None] = {
        "before": None,
        "after": None,
    }
    for side in required_sides:
        snapshot = snapshots[side]
        safe_snapshot = _safe_complete_snapshot(snapshot, allowed_fields)
        if safe_snapshot is None:
            return None
        if safe_snapshot[provenance_field] != str(organization_id):
            return None
        safe_sides[side] = safe_snapshot
    return safe_sides


def _safe_complete_snapshot(
    snapshot: Any,
    allowed_fields: frozenset[str],
) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict) or not allowed_fields.issubset(snapshot):
        return None
    safe: dict[str, Any] = {}
    for field in allowed_fields:
        value = snapshot[field]
        if field in _UUID_FIELDS:
            try:
                value = str(UUID(str(value)))
            except (TypeError, ValueError, AttributeError):
                return None
        elif field == "membership_state":
            if value not in _MEMBERSHIP_STATES:
                return None
        elif field == "organization_auth_state":
            if value not in _ORGANIZATION_AUTH_STATES:
                return None
        elif field == "auth_state":
            if value not in _RESOURCE_AUTH_STATES:
                return None
        elif not isinstance(value, (str, int, float, bool)) and value is not None:
            return None
        safe[field] = value
    return safe


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in SECRET_METADATA_KEYS or any(
        marker in normalized for marker in ("api_key", "token", "secret", "password")
    )
