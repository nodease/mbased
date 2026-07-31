from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from apps.gateway.services.security_alert_access import (
    get_security_alert_in_organization_or_404,
)
from apps.gateway.adapters.audit.management_reason_sanitizer import (
    ManagementReasonSanitizer,
)
from apps.gateway.utils.api_errors import raise_api_error
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.security_alert import (
    SecurityAlert,
    SecurityAlertAuditEvent,
)
from apps.shared.db.models.user import User
from apps.shared.schemas.security_alert import (
    SecurityAlertAcknowledgement,
    SecurityAlertAuditLogItem,
    SecurityAlertAuditLogListResponse,
    SecurityAlertDetail,
    SecurityAlertListItem,
    SecurityAlertListResponse,
    SecurityAlertResolution,
    SecurityAlertSafeActor,
    SecurityAlertSummaryItem,
    SecurityAlertSummaryResponse,
)
from apps.shared.services.security_alert_lifecycle import (
    acknowledge_security_alert,
    reopen_security_alert,
    resolve_security_alert,
)
from apps.shared.services.security_alert_notification_outbox import (
    dispatch_security_alert_notification_outbox,
    enqueue_security_alert_notification,
    lifecycle_notification_idempotency_key,
)

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SecurityAlertPeriod:
    start_at: datetime | None = None
    end_at: datetime | None = None


@dataclass(frozen=True)
class SecurityAlertFilters:
    severity: str | None = None
    status: str | None = None
    rule_id: str | None = None
    actor_id: UUID | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class SecurityAlertService:
    @staticmethod
    def resolve_period(
        request: Any,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> SecurityAlertPeriod:
        start = _ensure_timezone(start_at)
        end = _ensure_timezone(end_at)
        if start is not None and end is not None and end <= start:
            raise_api_error(
                request,
                400,
                "period.invalid",
                "endAt must be later than startAt.",
            )
        return SecurityAlertPeriod(start_at=start, end_at=end)

    @staticmethod
    def list_alerts(
        db: Session,
        *,
        organization_id: UUID,
        filters: SecurityAlertFilters | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> SecurityAlertListResponse:
        filters = filters or SecurityAlertFilters()
        query = _filtered_alert_query(db, organization_id, filters)
        total = query.count()
        alerts = (
            query.order_by(
                desc(SecurityAlert.last_detected_at),
                desc(SecurityAlert.id),
            )
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        actors = _safe_actors_by_id(
            db,
            organization_id,
            {alert.subject_actor_id for alert in alerts},
        )
        return SecurityAlertListResponse(
            total=total,
            items=[_list_item(alert, actors) for alert in alerts],
        )

    @staticmethod
    def get_detail(
        db: Session,
        *,
        request: Any,
        organization_id: UUID,
        alert_id: UUID,
    ) -> SecurityAlertDetail:
        alert = get_security_alert_in_organization_or_404(
            db, request, organization_id, alert_id
        )
        actor_ids = {
            actor_id
            for actor_id in (
                alert.subject_actor_id,
                alert.acknowledged_by,
                alert.resolved_by,
            )
            if actor_id is not None
        }
        actors = _safe_actors_by_id(db, organization_id, actor_ids)
        item = _list_item(alert, actors)
        return SecurityAlertDetail(
            **item.model_dump(),
            evidence_count=_evidence_count(db, alert.id),
            acknowledged=_acknowledgement(alert, actors),
            resolution=_resolution(alert, actors),
        )

    @staticmethod
    def get_summary(
        db: Session,
        *,
        organization_id: UUID,
    ) -> SecurityAlertSummaryResponse:
        open_query = db.query(SecurityAlert).filter(
            SecurityAlert.organization_id == organization_id,
            SecurityAlert.status == "open",
        )
        open_count = open_query.count()
        high_open_count = open_query.filter(SecurityAlert.severity == "high").count()
        alerts = (
            open_query.order_by(
                desc(SecurityAlert.last_detected_at),
                desc(SecurityAlert.id),
            )
            .limit(5)
            .all()
        )
        actors = _safe_actors_by_id(
            db,
            organization_id,
            {alert.subject_actor_id for alert in alerts},
        )
        return SecurityAlertSummaryResponse(
            open_count=open_count,
            high_open_count=high_open_count,
            recent_items=[
                SecurityAlertSummaryItem(
                    id=alert.id,
                    rule_id=alert.rule_id,
                    severity=alert.severity,
                    actor=_actor_or_deleted(alert.subject_actor_id, actors),
                    occurrence_count=alert.occurrence_count,
                    last_detected_at=alert.last_detected_at,
                )
                for alert in alerts
            ],
        )

    @staticmethod
    def list_evidence(
        db: Session,
        *,
        request: Any,
        organization_id: UUID,
        alert_id: UUID,
        page: int = 1,
        limit: int = 20,
    ) -> SecurityAlertAuditLogListResponse:
        get_security_alert_in_organization_or_404(
            db, request, organization_id, alert_id
        )
        query = (
            db.query(AuditLog)
            .join(
                SecurityAlertAuditEvent,
                SecurityAlertAuditEvent.audit_log_id == AuditLog.id,
            )
            .filter(SecurityAlertAuditEvent.security_alert_id == alert_id)
        )
        total = query.count()
        audits = (
            query.order_by(desc(AuditLog.occurred_at), desc(AuditLog.id))
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return SecurityAlertAuditLogListResponse(
            total=total,
            items=[_safe_audit_item(audit, organization_id) for audit in audits],
        )

    @staticmethod
    def acknowledge(
        db: Session,
        *,
        request: Any,
        organization_id: UUID,
        alert_id: UUID,
        manager_id: UUID,
        expected_version: int,
        now: datetime | None = None,
    ) -> SecurityAlertDetail:
        alert = get_security_alert_in_organization_or_404(
            db, request, organization_id, alert_id
        )
        acknowledge_security_alert(
            db,
            alert=alert,
            manager_id=manager_id,
            acknowledged_at=now or datetime.now(timezone.utc),
            expected_version=expected_version,
        )
        return _detail_then_commit(
            db,
            request=request,
            organization_id=organization_id,
            alert_id=alert_id,
        )

    @staticmethod
    def resolve(
        db: Session,
        *,
        request: Any,
        organization_id: UUID,
        alert_id: UUID,
        manager_id: UUID,
        expected_version: int,
        resolution_type: str,
        reason: str,
        reason_sanitizer: Any | None = None,
        now: datetime | None = None,
    ) -> SecurityAlertDetail:
        alert = get_security_alert_in_organization_or_404(
            db, request, organization_id, alert_id
        )
        resolve_security_alert(
            db,
            alert=alert,
            manager_id=manager_id,
            resolved_at=now or datetime.now(timezone.utc),
            expected_version=expected_version,
            resolution_type=resolution_type,
            reason=reason,
            reason_sanitizer=reason_sanitizer or ManagementReasonSanitizer(),
        )
        return _detail_then_commit(
            db,
            request=request,
            organization_id=organization_id,
            alert_id=alert_id,
        )

    @staticmethod
    def reopen(
        db: Session,
        *,
        request: Any,
        organization_id: UUID,
        alert_id: UUID,
        manager_id: UUID,
        expected_version: int,
        now: datetime | None = None,
    ) -> SecurityAlertDetail:
        alert = get_security_alert_in_organization_or_404(
            db, request, organization_id, alert_id
        )
        reopen_security_alert(
            db,
            alert=alert,
            manager_id=manager_id,
            reopened_at=now or datetime.now(timezone.utc),
            expected_version=expected_version,
        )
        return _detail_then_commit(
            db,
            request=request,
            organization_id=organization_id,
            alert_id=alert_id,
        )


def _filtered_alert_query(
    db: Session,
    organization_id: UUID,
    filters: SecurityAlertFilters,
):
    query = db.query(SecurityAlert).filter(
        SecurityAlert.organization_id == organization_id
    )
    if filters.severity is not None:
        query = query.filter(SecurityAlert.severity == filters.severity)
    if filters.status is not None:
        query = query.filter(SecurityAlert.status == filters.status)
    if filters.rule_id is not None:
        query = query.filter(SecurityAlert.rule_id == filters.rule_id)
    if filters.actor_id is not None:
        query = query.filter(SecurityAlert.subject_actor_id == filters.actor_id)
    if filters.start_at is not None:
        query = query.filter(SecurityAlert.last_detected_at >= filters.start_at)
    if filters.end_at is not None:
        query = query.filter(SecurityAlert.last_detected_at < filters.end_at)
    return query


def _safe_actors_by_id(
    db: Session,
    organization_id: UUID,
    actor_ids: set[UUID],
) -> dict[UUID, SecurityAlertSafeActor]:
    if not actor_ids:
        return {}
    users = {
        user.id: user
        for user in db.query(User).filter(User.id.in_(actor_ids)).all()
    }
    memberships = {
        membership.user_id: membership
        for membership in db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id.in_(actor_ids),
        )
        .all()
    }
    result: dict[UUID, SecurityAlertSafeActor] = {}
    for actor_id in actor_ids:
        user = users.get(actor_id)
        membership = memberships.get(actor_id)
        if user is None or user.deactivated_at is not None:
            state = "deleted"
            display_name = None
        elif membership is None:
            state = "removed"
            display_name = None
        else:
            state = membership.membership_state
            if state not in {"active", "suspended", "removed"}:
                state = "removed"
            display_name = user.name if state in {"active", "suspended"} else None
        result[actor_id] = SecurityAlertSafeActor(
            id=actor_id,
            display_name=display_name,
            state=state,
        )
    return result


def _actor_or_deleted(
    actor_id: UUID | None,
    actors: dict[UUID, SecurityAlertSafeActor],
) -> SecurityAlertSafeActor:
    if actor_id is None:
        return SecurityAlertSafeActor(id=None, display_name=None, state="deleted")
    return actors.get(
        actor_id,
        SecurityAlertSafeActor(id=actor_id, display_name=None, state="deleted"),
    )


def _list_item(
    alert: Any,
    actors: dict[UUID, SecurityAlertSafeActor],
) -> SecurityAlertListItem:
    return SecurityAlertListItem(
        id=alert.id,
        organization_id=alert.organization_id,
        rule_id=alert.rule_id,
        rule_version=alert.rule_version,
        severity=alert.severity,
        status=alert.status,
        policy_reason=alert.policy_reason,
        actor=_actor_or_deleted(alert.subject_actor_id, actors),
        occurrence_count=alert.occurrence_count,
        first_detected_at=alert.first_detected_at,
        last_detected_at=alert.last_detected_at,
        version=alert.lifecycle_version,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )


def _acknowledgement(
    alert: Any,
    actors: dict[UUID, SecurityAlertSafeActor],
) -> SecurityAlertAcknowledgement | None:
    if alert.acknowledged_at is None:
        return None
    return SecurityAlertAcknowledgement(
        by=_actor_or_deleted(alert.acknowledged_by, actors),
        at=alert.acknowledged_at,
    )


def _resolution(
    alert: Any,
    actors: dict[UUID, SecurityAlertSafeActor],
) -> SecurityAlertResolution | None:
    if alert.status != "resolved" or alert.resolved_at is None:
        return None
    return SecurityAlertResolution(
        type=alert.resolution_type,
        reason=alert.resolution_reason,
        by=_actor_or_deleted(alert.resolved_by, actors),
        at=alert.resolved_at,
    )


def _evidence_count(db: Session, alert_id: UUID) -> int:
    return int(
        db.query(func.count(SecurityAlertAuditEvent.id))
        .filter(SecurityAlertAuditEvent.security_alert_id == alert_id)
        .scalar()
        or 0
    )


def _safe_audit_item(
    audit: Any,
    organization_id: UUID,
) -> SecurityAlertAuditLogItem:
    metadata = audit.audit_metadata if isinstance(audit.audit_metadata, dict) else {}
    organization_matches = metadata.get("organization_id") == str(organization_id)
    target_is_safe = (
        organization_matches
        and audit.action == "permission.denied"
        and isinstance(audit.target_type, str)
        and bool(audit.target_type)
        and isinstance(audit.target_id, str)
        and bool(audit.target_id)
    )
    request_id = metadata.get("request_id")
    required_permission = (
        metadata.get("required_permission")
        if organization_matches
        and audit.action == "permission.denied"
        and metadata.get("required_permission") == "security_alert.manage"
        else None
    )
    requested_operation = (
        metadata.get("requested_operation")
        if required_permission
        and metadata.get("requested_operation")
        in {
            "security_alert.list",
            "security_alert.summary",
            "security_alert.detail",
            "security_alert.evidence.list",
            "security_alert.acknowledge",
            "security_alert.resolve",
            "security_alert.reopen",
        }
        else None
    )
    denial_reason = (
        metadata.get("denial_reason")
        if required_permission
        and metadata.get("denial_reason") == "organization_manager_required"
        else None
    )
    return SecurityAlertAuditLogItem(
        id=audit.id,
        occurred_at=audit.occurred_at,
        actor_id=audit.actor_id,
        actor_type=audit.actor_type,
        category=audit.category,
        action=audit.action,
        target_type=audit.target_type if target_is_safe else None,
        target_id=audit.target_id if target_is_safe else None,
        status=audit.status,
        request_id=request_id if isinstance(request_id, str) else None,
        required_permission=required_permission,
        requested_operation=requested_operation,
        denial_reason=denial_reason,
    )


def _detail_then_commit(
    db: Session,
    *,
    request: Any,
    organization_id: UUID,
    alert_id: UUID,
) -> SecurityAlertDetail:
    try:
        detail = SecurityAlertService.get_detail(
            db,
            request=request,
            organization_id=organization_id,
            alert_id=alert_id,
        )
        enqueue_security_alert_notification(
            db,
            scoped_organization_id=organization_id,
            idempotency_key=lifecycle_notification_idempotency_key(
                alert_id,
                detail.version,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    try:
        dispatch_security_alert_notification_outbox()
    except Exception as error:
        logger.warning(
            "Failed to dispatch security alert notification outbox: error_type=%s",
            type(error).__name__,
        )
    return detail


def _ensure_timezone(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=KST)
    return value
