from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from apps.gateway.adapters.db.access_management_locking import (
    lock_access_subject_rows,
)
from apps.gateway.services.workflow_permission_lock import (
    lock_workflow_permission_scope,
)
from apps.gateway.services.admin_usage_service import AdminUsageService
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.context import get_current_metadata
from apps.shared.db.models.app import App
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MEMBER,
    ORGANIZATION_AUTH_MANAGER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    ORGANIZATION_MEMBERSHIP_INVITED,
    ORGANIZATION_MEMBERSHIP_REMOVED,
    ORGANIZATION_MEMBERSHIP_SUSPENDED,
    OrganizationMembership,
)
from apps.shared.db.models.team import (
    TeamMembership,
    UserKnowledgePermission,
    UserLLMPermission,
    UserMailCredentialPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.user_app_creation_permission import UserAppCreationPermission
from apps.shared.db.models.workflow import Workflow
from apps.shared.schemas.organization_membership import (
    MemberCurrentMonthUsage,
    OrganizationMemberInviteRequest,
    OrganizationMemberListItemResponse,
    OrganizationMemberRemoveResponse,
    OrganizationMemberResponse,
    OrganizationMemberUpdateRequest,
    OrganizationSummaryResponse,
    RevokedUserPermissionCounts,
)
from apps.shared.services.permission_audit import record_resource_permission_denied
from apps.shared.services.permissions import (
    has_organization_manager_permission,
    has_organization_scope_access,
)
from apps.shared.services.provider_usage_cost_read_model import (
    provider_usage_subject_aggregate_subquery,
    summarize_usage_by_execution_subject,
)

from .notification_service import publish_notifications_changed

VISIBLE_ORGANIZATION_STATES = {
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    ORGANIZATION_MEMBERSHIP_INVITED,
}
LIST_MEMBER_STATES = {
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    ORGANIZATION_MEMBERSHIP_INVITED,
    ORGANIZATION_MEMBERSHIP_SUSPENDED,
}
ALL_MEMBER_STATES = {
    *LIST_MEMBER_STATES,
    ORGANIZATION_MEMBERSHIP_REMOVED,
}
_MAX_WORKFLOW_PERMISSION_SCOPE_LOCK_PASSES = 64


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock_member_workflow_permission_scopes(
    db: Session,
    *,
    organization_id: Any,
    user_id: Any,
) -> None:
    """Freeze the member's Workflow grants before organization-wide cleanup.

    A primary transition can create a target grant while removal waits on the
    source Workflow scope. Re-reading after each acquired scope closes that
    race without taking App locks from the member-removal path.
    """
    locked_workflow_ids: set[Any] = set()
    for _ in range(_MAX_WORKFLOW_PERMISSION_SCOPE_LOCK_PASSES):
        workflow_ids = {
            permission.workflow_id
            for permission in db.query(UserWorkflowPermission)
            .filter(
                UserWorkflowPermission.grantee_organization_id == organization_id,
                UserWorkflowPermission.user_id == user_id,
            )
            .all()
        }
        pending_workflow_ids = sorted(
            workflow_ids - locked_workflow_ids,
            key=str,
        )
        if not pending_workflow_ids:
            return
        for workflow_id in pending_workflow_ids:
            lock_workflow_permission_scope(
                db,
                organization_id=organization_id,
                workflow_id=workflow_id,
            )
            locked_workflow_ids.add(workflow_id)
    raise RuntimeError("Workflow permission scope set did not stabilize")


def _flush_or_conflict(db: Session, message: str) -> None:
    # INSERT는 commit이 아니라 flush 시점에 실행되므로, unique 충돌을 409로
    # 변환하려면 flush를 감싸야 한다. commit을 감싸면 race를 놓친다.
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=message) from exc


def _get_active_organization(db: Session, organization_id: Any) -> Organization:
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return organization


def _get_active_user(db: Session, user_id: Any) -> User:
    user = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.deactivated_at.is_(None),
        )
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


def _get_membership(
    db: Session,
    organization_id: Any,
    user_id: Any,
) -> OrganizationMembership | None:
    return (
        db.query(OrganizationMembership)
        .options(joinedload(OrganizationMembership.user))
        .filter(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
        .first()
    )


def _ensure_manager(db: Session, current_user: User, organization_id: Any) -> None:
    if has_organization_manager_permission(db, current_user.id, organization_id):
        return
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise HTTPException(status_code=404, detail="Organization not found.")
    detail = "Organization manager permission is required."
    request_id = get_current_metadata().get("request_id")
    record_resource_permission_denied(
        user_id=current_user.id,
        resource_type="organization",
        resource_id=organization_id,
        action="manage_members",
        effective_auth_state=ORGANIZATION_AUTH_MEMBER,
        organization_id=organization_id,
        metadata={
            "request_id": request_id,
        },
    )
    exc = HTTPException(
        status_code=403,
        detail=detail,
    )
    setattr(exc, "audit_recorded", True)
    raise exc


def _member_response(membership: OrganizationMembership) -> OrganizationMemberResponse:
    user = membership.user
    return OrganizationMemberResponse(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        user_email=user.email,
        user_name=user.name,
        membership_state=membership.membership_state,
        organization_auth_state=membership.organization_auth_state,
        invited_by=membership.invited_by,
        invited_at=membership.invited_at,
        accepted_at=membership.accepted_at,
        removed_at=membership.removed_at,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


def _empty_member_current_month_usage() -> MemberCurrentMonthUsage:
    return MemberCurrentMonthUsage()


def _member_current_month_usage_response(
    member: OrganizationMemberResponse,
    usage: MemberCurrentMonthUsage,
) -> OrganizationMemberListItemResponse:
    return OrganizationMemberListItemResponse(
        **member.model_dump(),
        current_month_usage=usage,
    )


def _member_current_month_usage(
    db: Session,
    *,
    organization_id: Any,
    user_ids: list[Any],
) -> dict[Any, MemberCurrentMonthUsage]:
    if not user_ids:
        return {}

    period = AdminUsageService.resolve_month_period_kst(_now())
    if hasattr(db, "usage_logs"):
        return _member_current_month_usage_fake(
            db,
            organization_id=organization_id,
            user_ids=user_ids,
            start_at=period.start_at,
            end_at=period.end_at,
        )
    return _member_current_month_usage_query(
        db,
        organization_id=organization_id,
        user_ids=user_ids,
        start_at=period.start_at,
        end_at=period.end_at,
    )


def _member_current_month_usage_fake(
    db: Any,
    *,
    organization_id: Any,
    user_ids: list[Any],
    start_at: datetime,
    end_at: datetime,
) -> dict[Any, MemberCurrentMonthUsage]:
    workflow_organizations = {
        workflow.id: workflow.organization_id
        for workflow in getattr(db, "workflows", [])
    }
    eligible_workflow_ids = {
        app.workflow_id
        for app in getattr(db, "apps", [])
        if app.organization_id == organization_id
        and app.workflow_id is not None
        and workflow_organizations.get(app.workflow_id) == organization_id
    }
    aggregates = summarize_usage_by_execution_subject(
        organization_id=organization_id,
        eligible_workflow_ids=eligible_workflow_ids,
        user_ids=set(user_ids),
        start_at=start_at,
        end_at=end_at,
        legacy_usage_logs=db.usage_logs,
        provider_operations=getattr(db, "provider_usage_operations", ()),
    )
    return {
        user_id: _member_usage_costs(
            aggregate.total_cost,
            aggregate.agent_builder_cost,
            aggregate.unresolved_provider_call_count,
        )
        for user_id, aggregate in aggregates.items()
    }


def _member_current_month_usage_query(
    db: Session,
    *,
    organization_id: Any,
    user_ids: list[Any],
    start_at: datetime,
    end_at: datetime,
) -> dict[Any, MemberCurrentMonthUsage]:
    result = {user_id: _empty_member_current_month_usage() for user_id in user_ids}
    eligible_workflows = (
        db.query(App.workflow_id.label("workflow_id"))
        .join(
            Workflow,
            and_(
                Workflow.id == App.workflow_id,
                Workflow.organization_id == organization_id,
            ),
        )
        .filter(
            App.organization_id == organization_id,
            App.workflow_id.isnot(None),
        )
        .distinct()
        .subquery()
    )
    usage = provider_usage_subject_aggregate_subquery(
        organization_id=organization_id,
        start_at=start_at,
        end_at=end_at,
    )
    rows = (
        db.query(
            usage.c.attribution_user_id.label("user_id"),
            func.coalesce(func.sum(usage.c.total_cost), 0).label("total_cost"),
            func.coalesce(func.sum(usage.c.agent_builder_cost), 0).label(
                "agent_builder_cost"
            ),
            func.coalesce(
                func.sum(usage.c.unresolved_provider_call_count), 0
            ).label("unresolved_provider_call_count"),
        )
        .join(
            eligible_workflows,
            usage.c.workflow_id == eligible_workflows.c.workflow_id,
        )
        .filter(
            usage.c.attribution_user_id.in_(user_ids),
        )
        .group_by(usage.c.attribution_user_id)
        .all()
    )
    for row in rows:
        result[row.user_id] = _member_usage_costs(
            row.total_cost,
            row.agent_builder_cost,
            row.unresolved_provider_call_count,
        )
    return result


def _member_usage_costs(
    total_cost: Any,
    agent_builder_cost: Any,
    unresolved_provider_call_count: Any = 0,
) -> MemberCurrentMonthUsage:
    total = AdminUsageService.coalesce_cost(total_cost)
    agent_builder = AdminUsageService.coalesce_cost(agent_builder_cost)
    return MemberCurrentMonthUsage(
        total_cost=float(total),
        workflow_execution_cost=float(total - agent_builder),
        agent_builder_cost=float(agent_builder),
        usage_data_complete=int(unresolved_provider_call_count or 0) == 0,
        unresolved_provider_call_count=int(
            unresolved_provider_call_count or 0
        ),
    )


def _organization_summary(
    organization: Organization,
    membership: OrganizationMembership,
) -> OrganizationSummaryResponse:
    return OrganizationSummaryResponse(
        id=organization.id,
        name=organization.name,
        membership_state=membership.membership_state,
        organization_auth_state=membership.organization_auth_state,
        is_active=organization.is_active,
    )


def _audit_metadata(
    membership: OrganizationMembership,
    *,
    previous_membership_state: str | None = None,
    next_membership_state: str | None = None,
    previous_organization_auth_state: str | None = None,
    next_organization_auth_state: str | None = None,
    cleanup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = {
        "organization_id": str(membership.organization_id),
        "membership_id": str(membership.id),
        "target_user_id": str(membership.user_id),
    }
    if previous_membership_state is not None:
        metadata["previous_membership_state"] = previous_membership_state
    if next_membership_state is not None:
        metadata["next_membership_state"] = next_membership_state
    if previous_organization_auth_state is not None:
        metadata["previous_organization_auth_state"] = previous_organization_auth_state
    if next_organization_auth_state is not None:
        metadata["next_organization_auth_state"] = next_organization_auth_state
    if cleanup is not None:
        metadata["cleanup"] = cleanup
    return metadata


def _actor_snapshot(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": getattr(user, "email", None),
        "name": getattr(user, "name", None),
    }


def _merge_audit_context(
    current_user: User,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    enriched = get_current_metadata()
    enriched["actor"] = _actor_snapshot(current_user)
    enriched.update(metadata)
    return enriched


def _add_audit_log(
    db: Session,
    action: str,
    current_user: User,
    membership: OrganizationMembership,
    metadata: dict[str, Any],
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            action=action,
            category="action",
            actor_id=current_user.id,
            actor_type="user",
            target_type="organization_membership",
            target_id=str(membership.id),
            before=before,
            after=after,
            status="success",
            audit_metadata=_merge_audit_context(current_user, metadata),
        )
    )


def _membership_audit_snapshot(
    membership: OrganizationMembership,
) -> dict[str, Any]:
    return {
        "organization_id": str(membership.organization_id),
        "user_id": str(membership.user_id),
        "membership_state": membership.membership_state,
        "organization_auth_state": membership.organization_auth_state,
    }


def _guard_last_manager(
    membership: OrganizationMembership,
    next_membership_state: str,
    next_auth_state: str,
    *,
    active_manager_count: int | None,
    target_user_active: bool,
) -> None:
    if (
        not target_user_active
        or membership.membership_state != ORGANIZATION_MEMBERSHIP_ACTIVE
        or membership.organization_auth_state != ORGANIZATION_AUTH_MANAGER
    ):
        return
    if (
        next_membership_state == ORGANIZATION_MEMBERSHIP_ACTIVE
        and next_auth_state == ORGANIZATION_AUTH_MANAGER
    ):
        return
    if active_manager_count is None:
        raise RuntimeError("Active manager rows must be locked before reduction")
    if active_manager_count <= 1:
        raise HTTPException(
            status_code=409,
            detail="Cannot remove the last organization manager.",
        )


def _cleanup_counts(
    removed_team_memberships: int,
    revoked_user_permissions: RevokedUserPermissionCounts,
) -> dict[str, Any]:
    return {
        "team_memberships": removed_team_memberships,
        "user_workflow_permissions": revoked_user_permissions.workflow,
        "user_llm_permissions": revoked_user_permissions.llm_credential,
        "user_mail_credential_permissions": revoked_user_permissions.mail_credential,
        "user_app_creation_permissions": revoked_user_permissions.app_creation,
        "user_knowledge_permissions": revoked_user_permissions.knowledge_base,
        "user_audit_permissions": revoked_user_permissions.audit,
    }


class OrganizationMemberService:
    @staticmethod
    def list_active_organizations(
        db: Session,
        current_user: User,
    ) -> list[Organization]:
        rows = (
            db.query(Organization, OrganizationMembership)
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Organization.id,
            )
            .filter(
                OrganizationMembership.user_id == current_user.id,
                OrganizationMembership.membership_state
                == ORGANIZATION_MEMBERSHIP_ACTIVE,
                Organization.is_active.is_(True),
            )
            .order_by(
                OrganizationMembership.accepted_at.asc().nulls_last(),
                OrganizationMembership.created_at.asc(),
                Organization.name.asc(),
            )
            .all()
        )
        return [organization for organization, _membership in rows]

    @staticmethod
    def list_organization_memberships(
        db: Session,
        current_user: User,
    ) -> list[OrganizationSummaryResponse]:
        rows = (
            db.query(Organization, OrganizationMembership)
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Organization.id,
            )
            .filter(
                OrganizationMembership.user_id == current_user.id,
                OrganizationMembership.membership_state.in_(
                    VISIBLE_ORGANIZATION_STATES
                ),
                Organization.is_active.is_(True),
            )
            .order_by(
                OrganizationMembership.accepted_at.asc().nulls_last(),
                OrganizationMembership.created_at.asc(),
                Organization.name.asc(),
            )
            .all()
        )
        return [
            _organization_summary(organization, membership)
            for organization, membership in rows
        ]

    @staticmethod
    def list_members(
        db: Session,
        current_user: User,
        organization_id: Any,
        state: str | None = None,
    ) -> list[OrganizationMemberListItemResponse]:
        _get_active_organization(db, organization_id)
        _ensure_manager(db, current_user, organization_id)
        if state is not None:
            state = state.strip().lower()
            if state not in ALL_MEMBER_STATES:
                raise HTTPException(status_code=400, detail="Invalid membership state.")
            states = {state}
        else:
            states = LIST_MEMBER_STATES

        memberships = (
            db.query(OrganizationMembership)
            .options(joinedload(OrganizationMembership.user))
            .join(User, User.id == OrganizationMembership.user_id)
            .filter(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.membership_state.in_(states),
            )
            .order_by(
                User.name.asc(), User.email.asc(), OrganizationMembership.id.asc()
            )
            .all()
        )
        members = [_member_response(membership) for membership in memberships]
        usage_by_user = _member_current_month_usage(
            db,
            organization_id=organization_id,
            user_ids=[member.user_id for member in members],
        )
        return [
            _member_current_month_usage_response(
                member,
                usage_by_user.get(
                    member.user_id,
                    _empty_member_current_month_usage(),
                ),
            )
            for member in members
        ]

    @staticmethod
    def invite_member(
        db: Session,
        current_user: User,
        organization_id: Any,
        request: OrganizationMemberInviteRequest,
    ) -> OrganizationMemberResponse:
        _get_active_organization(db, organization_id)
        _ensure_manager(db, current_user, organization_id)
        if request.user_id == current_user.id:
            raise HTTPException(status_code=400, detail="Cannot invite yourself.")
        target_user = _get_active_user(db, request.user_id)
        membership = _get_membership(db, organization_id, request.user_id)
        if membership is not None:
            # 이미 활동 중이거나 초대 중이면 초대 요청을 멱등하게 처리한다.
            if membership.membership_state in {
                ORGANIZATION_MEMBERSHIP_ACTIVE,
                ORGANIZATION_MEMBERSHIP_INVITED,
            }:
                return _member_response(membership)
            if membership.membership_state == ORGANIZATION_MEMBERSHIP_SUSPENDED:
                raise HTTPException(
                    status_code=409,
                    detail="Suspended member must be reactivated with PATCH.",
                )

            # removed row는 unique 제약을 유지한 채 기존 membership을 재초대 상태로 되살린다.
            previous_state = membership.membership_state
            previous_auth_state = membership.organization_auth_state
            membership.membership_state = ORGANIZATION_MEMBERSHIP_INVITED
            membership.organization_auth_state = request.organization_auth_state
            membership.invited_by = current_user.id
            membership.invited_at = _now()
            membership.accepted_at = None
            membership.removed_at = None
        else:
            previous_state = None
            previous_auth_state = None
            membership = OrganizationMembership(
                organization_id=organization_id,
                user_id=request.user_id,
                membership_state=ORGANIZATION_MEMBERSHIP_INVITED,
                organization_auth_state=request.organization_auth_state,
                invited_by=current_user.id,
                invited_at=_now(),
            )
            membership.user = target_user
            db.add(membership)

        _flush_or_conflict(db, "Organization membership already exists.")
        _add_audit_log(
            db,
            AuditAction.ORGANIZATION_INVITE,
            current_user,
            membership,
            _audit_metadata(
                membership,
                previous_membership_state=previous_state,
                next_membership_state=membership.membership_state,
                previous_organization_auth_state=previous_auth_state,
                next_organization_auth_state=membership.organization_auth_state,
            ),
        )
        db.commit()
        publish_notifications_changed(membership.user_id)
        db.refresh(membership)
        return _member_response(membership)

    @staticmethod
    def accept_invitation(
        db: Session,
        current_user: User,
        organization_id: Any,
    ) -> OrganizationMemberResponse:
        _get_active_organization(db, organization_id)
        membership = _get_membership(db, organization_id, current_user.id)
        if membership is None:
            raise HTTPException(status_code=404, detail="Invitation not found.")
        if membership.membership_state == ORGANIZATION_MEMBERSHIP_ACTIVE:
            return _member_response(membership)
        if membership.membership_state != ORGANIZATION_MEMBERSHIP_INVITED:
            raise HTTPException(
                status_code=409, detail="Invitation cannot be accepted."
            )

        # 초대 수락은 사용자 본인만 수행하므로 manager 권한 검사를 하지 않는다.
        previous_state = membership.membership_state
        membership.membership_state = ORGANIZATION_MEMBERSHIP_ACTIVE
        membership.accepted_at = _now()
        membership.removed_at = None
        _add_audit_log(
            db,
            AuditAction.ORGANIZATION_MEMBER_ACCEPT,
            current_user,
            membership,
            _audit_metadata(
                membership,
                previous_membership_state=previous_state,
                next_membership_state=membership.membership_state,
            ),
        )
        db.commit()
        publish_notifications_changed(current_user.id)
        db.refresh(membership)
        return _member_response(membership)

    @staticmethod
    def decline_invitation(
        db: Session,
        current_user: User,
        organization_id: Any,
    ) -> OrganizationMemberResponse:
        _get_active_organization(db, organization_id)
        membership = _get_membership(db, organization_id, current_user.id)
        if membership is None:
            raise HTTPException(status_code=404, detail="Invitation not found.")
        if membership.membership_state != ORGANIZATION_MEMBERSHIP_INVITED:
            raise HTTPException(
                status_code=409, detail="Invitation cannot be declined."
            )

        previous_state = membership.membership_state
        membership.membership_state = ORGANIZATION_MEMBERSHIP_REMOVED
        membership.accepted_at = None
        membership.removed_at = _now()
        _add_audit_log(
            db,
            AuditAction.ORGANIZATION_MEMBER_DECLINE,
            current_user,
            membership,
            _audit_metadata(
                membership,
                previous_membership_state=previous_state,
                next_membership_state=membership.membership_state,
            ),
        )
        db.commit()
        publish_notifications_changed(current_user.id)
        db.refresh(membership)
        return _member_response(membership)

    @staticmethod
    def update_member(
        db: Session,
        current_user: User,
        organization_id: Any,
        user_id: Any,
        request: OrganizationMemberUpdateRequest,
    ) -> OrganizationMemberResponse:
        _get_active_organization(db, organization_id)
        _ensure_manager(db, current_user, organization_id)
        provided_fields = request.model_fields_set & {
            "membership_state",
            "organization_auth_state",
        }
        if not provided_fields or all(
            getattr(request, field_name) is None for field_name in provided_fields
        ):
            raise HTTPException(status_code=400, detail="No update fields provided.")

        manager_reduction = (
            request.membership_state is not None
            and request.membership_state != ORGANIZATION_MEMBERSHIP_ACTIVE
        ) or request.organization_auth_state == ORGANIZATION_AUTH_MEMBER
        locked = lock_access_subject_rows(
            db,
            organization_id,
            user_id,
            manager_reduction=manager_reduction,
            allowed_membership_states=None,
        )
        if locked is None:
            raise HTTPException(status_code=404, detail="Member not found.")
        membership = locked.membership
        membership.user = locked.user
        if membership.membership_state == ORGANIZATION_MEMBERSHIP_REMOVED:
            raise HTTPException(
                status_code=409,
                detail="Removed member must be re-invited.",
            )

        next_state = request.membership_state or membership.membership_state
        next_auth_state = (
            request.organization_auth_state or membership.organization_auth_state
        )
        if (
            membership.membership_state == ORGANIZATION_MEMBERSHIP_INVITED
            and request.membership_state is not None
        ):
            raise HTTPException(
                status_code=409,
                detail="Invited member must accept the invitation.",
            )
        if current_user.id == membership.user_id and (
            next_auth_state != membership.organization_auth_state
            or next_state != membership.membership_state
        ):
            # 본인 권한 강등/상태 변경은 마지막 manager 회피나 셀프 잠금을 막기 위해 금지한다.
            raise HTTPException(status_code=400, detail="Cannot update yourself.")
        if (
            next_state == membership.membership_state
            and next_auth_state == membership.organization_auth_state
        ):
            return _member_response(membership)

        _guard_last_manager(
            membership,
            next_state,
            next_auth_state,
            active_manager_count=locked.active_manager_count,
            target_user_active=locked.user.deactivated_at is None,
        )
        previous_state = membership.membership_state
        previous_auth_state = membership.organization_auth_state
        before = _membership_audit_snapshot(membership)
        membership.membership_state = next_state
        membership.organization_auth_state = next_auth_state
        if next_state == ORGANIZATION_MEMBERSHIP_ACTIVE:
            membership.removed_at = None
        if next_state == ORGANIZATION_MEMBERSHIP_SUSPENDED:
            membership.removed_at = None

        try:
            _add_audit_log(
                db,
                AuditAction.ORGANIZATION_MEMBER_UPDATE,
                current_user,
                membership,
                _audit_metadata(
                    membership,
                    previous_membership_state=previous_state,
                    next_membership_state=membership.membership_state,
                    previous_organization_auth_state=previous_auth_state,
                    next_organization_auth_state=membership.organization_auth_state,
                ),
                before=before,
                after=_membership_audit_snapshot(membership),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(membership)
        return _member_response(membership)

    @staticmethod
    def remove_member(
        db: Session,
        current_user: User,
        organization_id: Any,
        user_id: Any,
    ) -> OrganizationMemberRemoveResponse:
        _get_active_organization(db, organization_id)
        _ensure_manager(db, current_user, organization_id)
        if current_user.id == user_id:
            raise HTTPException(status_code=400, detail="Cannot remove yourself.")
        locked = lock_access_subject_rows(
            db,
            organization_id,
            user_id,
            manager_reduction=True,
            allowed_membership_states=None,
        )
        if locked is None:
            raise HTTPException(status_code=404, detail="Member not found.")
        membership = locked.membership
        membership.user = locked.user
        if membership.membership_state == ORGANIZATION_MEMBERSHIP_REMOVED:
            return OrganizationMemberRemoveResponse(
                status="removed",
                removed_team_memberships=0,
                revoked_user_permissions=RevokedUserPermissionCounts(),
            )

        was_invited = (
            membership.membership_state == ORGANIZATION_MEMBERSHIP_INVITED
        )
        _guard_last_manager(
            membership,
            ORGANIZATION_MEMBERSHIP_REMOVED,
            membership.organization_auth_state,
            active_manager_count=locked.active_manager_count,
            target_user_active=locked.user.deactivated_at is None,
        )
        _lock_member_workflow_permission_scopes(
            db,
            organization_id=organization_id,
            user_id=user_id,
        )
        previous_state = membership.membership_state
        previous_auth_state = membership.organization_auth_state
        # 조직에서 제거되면 팀 소속과 사용자별 리소스 권한도 함께 회수한다.
        removed_team_memberships = (
            db.query(TeamMembership)
            .filter(
                TeamMembership.grantee_organization_id == organization_id,
                TeamMembership.user_id == user_id,
            )
            .delete(synchronize_session=False)
        )
        revoked_workflow_permissions = (
            db.query(UserWorkflowPermission)
            .filter(
                UserWorkflowPermission.grantee_organization_id == organization_id,
                UserWorkflowPermission.user_id == user_id,
            )
            .delete(synchronize_session=False)
        )
        revoked_llm_permissions = (
            db.query(UserLLMPermission)
            .filter(
                UserLLMPermission.grantee_organization_id == organization_id,
                UserLLMPermission.user_id == user_id,
            )
            .delete(synchronize_session=False)
        )
        revoked_mail_permissions = (
            db.query(UserMailCredentialPermission)
            .filter(
                UserMailCredentialPermission.grantee_organization_id == organization_id,
                UserMailCredentialPermission.user_id == user_id,
            )
            .delete(synchronize_session=False)
        )
        revoked_knowledge_permissions = (
            db.query(UserKnowledgePermission)
            .filter(
                UserKnowledgePermission.grantee_organization_id == organization_id,
                UserKnowledgePermission.user_id == user_id,
            )
            .delete(synchronize_session=False)
        )
        revoked_app_creation_permissions = (
            db.query(UserAppCreationPermission)
            .filter(
                UserAppCreationPermission.grantee_organization_id == organization_id,
                UserAppCreationPermission.user_id == user_id,
            )
            .delete(synchronize_session=False)
        )
        membership.membership_state = ORGANIZATION_MEMBERSHIP_REMOVED
        membership.removed_at = _now()

        revoked_user_permissions = RevokedUserPermissionCounts(
            workflow=revoked_workflow_permissions,
            llm_credential=revoked_llm_permissions,
            mail_credential=revoked_mail_permissions,
            app_creation=revoked_app_creation_permissions,
            knowledge_base=revoked_knowledge_permissions,
            audit=0,
        )
        cleanup = _cleanup_counts(removed_team_memberships, revoked_user_permissions)
        try:
            _add_audit_log(
                db,
                AuditAction.ORGANIZATION_MEMBER_REMOVE,
                current_user,
                membership,
                _audit_metadata(
                    membership,
                    previous_membership_state=previous_state,
                    next_membership_state=membership.membership_state,
                    previous_organization_auth_state=previous_auth_state,
                    next_organization_auth_state=membership.organization_auth_state,
                    cleanup=cleanup,
                ),
            )
            _add_audit_log(
                db,
                AuditAction.PERMISSION_REVOKE,
                current_user,
                membership,
                {
                    **_audit_metadata(membership, cleanup=cleanup),
                    "reason": AuditAction.ORGANIZATION_MEMBER_REMOVE,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        if was_invited:
            publish_notifications_changed(user_id)
        return OrganizationMemberRemoveResponse(
            status="removed",
            removed_team_memberships=removed_team_memberships,
            revoked_user_permissions=revoked_user_permissions,
        )


__all__ = ["OrganizationMemberService"]
