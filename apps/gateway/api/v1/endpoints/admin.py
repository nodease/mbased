from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.services.admin_audit_log_service import (
    AdminAuditLogFilters,
    AdminAuditLogService,
)
from apps.gateway.services.admin_usage_service import KST, AdminUsageService
from apps.gateway.services.app_lifecycle_lock import (
    AppPrimaryChangedDuringMutationError,
)
from apps.gateway.services.app_creation_permission_service import (
    AppCreationPermissionService,
)
from apps.gateway.services.organization_context import resolve_active_organization_id
from apps.gateway.services.permission_request_service import PermissionRequestService
from apps.gateway.services.security_alert_access import (
    resolve_security_alert_manager_organization,
)
from apps.gateway.services.security_alert_service import (
    SecurityAlertFilters,
    SecurityAlertService,
)
from apps.gateway.services.workflow_budget_service import (
    WorkflowBudgetScopeUnavailableError,
    WorkflowBudgetService,
)
from apps.gateway.utils.api_errors import raise_api_error
from apps.shared.db.models.app import App
from apps.shared.db.models.audit_log import AuditStatus
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_budget import WorkflowBudget
from apps.shared.db.session import get_db
from apps.shared.schemas.admin_usage import (
    AdminOrganizationSummaryResponse,
    AdminWorkflowUsageResponse,
)
from apps.shared.schemas.audit import AdminAuditLogListResponse, AuditLogDetailResponse
from apps.shared.schemas.permission_request import (
    AppCreationPermissionListResponse,
    AppCreationPermissionResponse,
    AppCreationPermissionRevokeResponse,
    PermissionRequestListResponse,
    PermissionRequestResponse,
    PermissionRequestUserSchema,
)
from apps.shared.schemas.security_alert import (
    SecurityAlertAcknowledgeRequest,
    SecurityAlertAuditLogListResponse,
    SecurityAlertDetail,
    SecurityAlertListResponse,
    SecurityAlertReopenRequest,
    SecurityAlertResolveRequest,
    SecurityAlertRuleId,
    SecurityAlertSeverity,
    SecurityAlertStatus,
    SecurityAlertSummaryResponse,
)
from apps.shared.schemas.workflow_budget import (
    WorkflowBudgetListResponse,
    WorkflowBudgetResponse,
    WorkflowBudgetUpsertRequest,
)
from apps.shared.services import permissions as shared_permissions
from apps.shared.services.security_alert_lifecycle import (
    SecurityAlertStaleStateError,
)

router = APIRouter()


def _resolve_active_organization(
    db: Session,
    request: Request,
    x_organization_id: str | None,
    current_user: User,
):
    return resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )


def _resolve_managed_organization(
    db: Session,
    request: Request,
    x_organization_id: str | None,
    current_user: User,
):
    """active organization을 해석하고 owner/manager가 아니면 403으로 닫는다."""
    organization_id = _resolve_active_organization(
        db, request, x_organization_id, current_user
    )
    if not shared_permissions.has_organization_manager_permission(
        db, current_user.id, organization_id
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    return organization_id


def _resolve_security_alert_organization(
    db: Session,
    request: Request,
    x_organization_id: str | None,
    current_user: User,
    requested_operation: str,
):
    return resolve_security_alert_manager_organization(
        db,
        request,
        x_organization_id,
        current_user.id,
        requested_operation,
    )


def _run_security_alert_mutation(request: Request, operation):
    try:
        return operation()
    except HTTPException:
        raise
    except SecurityAlertStaleStateError:
        raise_api_error(
            request,
            409,
            "stale_state",
            "Security alert state changed. Refresh and try again.",
        )
    except ValueError:
        raise_api_error(
            request,
            422,
            "validation.failed",
            "Security alert mutation is invalid.",
        )
    except Exception:
        raise_api_error(
            request,
            500,
            "audit.persistence_failed",
            "Security alert update could not be persisted.",
        )


def _users_by_id(db: Session, items) -> dict:
    """`user_id`를 가진 row 목록에서 관련 User를 한 번에 조회한다."""
    user_ids = {item.user_id for item in items}
    if not user_ids:
        return {}
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    return {user.id: user for user in users}


def _user_schema_or_none(user) -> PermissionRequestUserSchema | None:
    if user is None:
        return None
    return PermissionRequestUserSchema.model_validate(user)


def _serialize_request(item, requester) -> PermissionRequestResponse:
    return PermissionRequestResponse(
        id=item.id,
        user=_user_schema_or_none(requester),
        requested_permission=item.requested_permission,
        reason=item.reason,
        status=item.status,
        created_at=item.created_at,
        decided_by=item.decided_by,
        decided_at=item.decided_at,
    )


def _serialize_requests(db: Session, items) -> list[PermissionRequestResponse]:
    requesters = _users_by_id(db, items)
    return [
        _serialize_request(item, requesters.get(item.user_id)) for item in items
    ]


def _serialize_app_creation_permissions(
    db: Session, items
) -> list[AppCreationPermissionResponse]:
    holders = _users_by_id(db, items)
    return [
        AppCreationPermissionResponse(
            id=item.id,
            user=_user_schema_or_none(holders.get(item.user_id)),
            assigned_by=item.assigned_by,
            assigned_at=item.assigned_at,
        )
        for item in items
    ]


def _workflow_name_in_scope(
    db: Session,
    organization_id,
    workflow_id: UUID,
) -> str:
    row = (
        db.query(Workflow, App.name)
        .join(App, Workflow.app_id == App.id)
        .filter(
            Workflow.id == workflow_id,
            Workflow.organization_id == organization_id,
            App.organization_id == organization_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return row[1]


def _serialize_workflow_budget(
    db: Session,
    budget: WorkflowBudget,
    workflow_name: str,
    *,
    include_usage: bool = False,
) -> WorkflowBudgetResponse:
    current_month_cost = None
    usage_ratio = None
    status = None
    usage_data_complete = None
    unresolved_provider_call_count = None
    if include_usage:
        current_usage = WorkflowBudgetService.get_current_month_usage(
            db,
            workflow_id=budget.workflow_id,
            now=datetime.now(KST),
            organization_id=budget.organization_id,
        )
        current_cost = current_usage.total_cost
        usage_data_complete = current_usage.usage_data_complete
        unresolved_provider_call_count = (
            current_usage.unresolved_provider_call_count
        )
        status = WorkflowBudgetService.classify_budget_usage(
            current_cost=current_cost,
            monthly_budget_usd=budget.monthly_budget_usd,
            is_enabled=budget.is_enabled,
        )
        current_month_cost = float(current_cost)
        if status is not None:
            usage_ratio = float(current_cost / budget.monthly_budget_usd)

    return WorkflowBudgetResponse(
        workflow_id=budget.workflow_id,
        workflow_name=workflow_name,
        monthly_budget_usd=float(budget.monthly_budget_usd),
        is_enabled=budget.is_enabled,
        created_by=budget.created_by,
        updated_by=budget.updated_by,
        created_at=budget.created_at,
        updated_at=budget.updated_at,
        current_month_cost=current_month_cost,
        usage_ratio=usage_ratio,
        status=status,
        usage_data_complete=usage_data_complete,
        unresolved_provider_call_count=unresolved_provider_call_count,
    )


@router.get("/audit-logs", response_model=AdminAuditLogListResponse)
def list_audit_logs(
    request: Request,
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=20, ge=1, le=100),
    actor_id: Annotated[UUID | None, Query(alias="actorId")] = None,
    action: str | None = None,
    target_type: Annotated[str | None, Query(alias="targetType")] = None,
    target_id: Annotated[str | None, Query(alias="targetId")] = None,
    status: Annotated[AuditStatus | None, Query()] = None,
    start_at: Annotated[datetime | None, Query(alias="startAt")] = None,
    end_at: Annotated[datetime | None, Query(alias="endAt")] = None,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_active_organization(
        db, request, x_organization_id, current_user
    )
    period = AdminAuditLogService.resolve_period(start_at, end_at)
    return AdminAuditLogService.list_audit_logs(
        db,
        current_user=current_user,
        organization_id=organization_id,
        filters=AdminAuditLogFilters(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            status=status,
            start_at=period.start_at,
            end_at=period.end_at,
        ),
        cursor=cursor,
        limit=limit,
    )


@router.get("/audit-logs/{audit_log_id}", response_model=AuditLogDetailResponse)
def get_audit_log_detail(
    request: Request,
    audit_log_id: UUID,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_active_organization(
        db, request, x_organization_id, current_user
    )
    return AdminAuditLogService.get_audit_log_detail(
        db,
        current_user=current_user,
        organization_id=organization_id,
        audit_log_id=audit_log_id,
    )


@router.get("/usage/workflows", response_model=AdminWorkflowUsageResponse)
def list_workflow_usage(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    start_at: Annotated[datetime | None, Query(alias="startAt")] = None,
    end_at: Annotated[datetime | None, Query(alias="endAt")] = None,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_managed_organization(
        db, request, x_organization_id, current_user
    )
    period = AdminUsageService.resolve_period(start_at, end_at)
    return AdminUsageService.aggregate_workflow_usage(
        db,
        organization_id=organization_id,
        period=period,
        page=page,
        limit=limit,
    )


@router.get("/summary", response_model=AdminOrganizationSummaryResponse)
def get_organization_summary(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """조직 월간 비용/예산 요약 (FR-015)."""
    organization_id = _resolve_managed_organization(
        db, request, x_organization_id, current_user
    )
    return AdminUsageService.get_organization_summary(
        db, organization_id=organization_id
    )


@router.get(
    "/workflow-budgets",
    response_model=WorkflowBudgetListResponse,
)
def list_workflow_budgets(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_managed_organization(
        db, request, x_organization_id, current_user
    )
    query = (
        db.query(WorkflowBudget, App.name)
        .join(Workflow, WorkflowBudget.workflow_id == Workflow.id)
        .join(App, Workflow.app_id == App.id)
        .filter(
            WorkflowBudget.organization_id == organization_id,
            Workflow.organization_id == organization_id,
            App.organization_id == organization_id,
        )
    )
    total = query.count()
    rows = (
        query.order_by(WorkflowBudget.updated_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return WorkflowBudgetListResponse(
        total=total,
        items=[
            _serialize_workflow_budget(db, budget, workflow_name)
            for budget, workflow_name in rows
        ],
    )


@router.get(
    "/workflow-budgets/{workflow_id}",
    response_model=WorkflowBudgetResponse,
)
def get_workflow_budget(
    request: Request,
    workflow_id: UUID,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_managed_organization(
        db, request, x_organization_id, current_user
    )
    workflow_name = _workflow_name_in_scope(db, organization_id, workflow_id)
    budget = (
        db.query(WorkflowBudget)
        .filter(
            WorkflowBudget.organization_id == organization_id,
            WorkflowBudget.workflow_id == workflow_id,
        )
        .first()
    )
    if budget is None:
        raise HTTPException(status_code=404, detail="Workflow budget not found")
    return _serialize_workflow_budget(
        db, budget, workflow_name, include_usage=True
    )


@router.put(
    "/workflow-budgets/{workflow_id}",
    response_model=WorkflowBudgetResponse,
)
def upsert_workflow_budget(
    request: Request,
    workflow_id: UUID,
    body: WorkflowBudgetUpsertRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_managed_organization(
        db, request, x_organization_id, current_user
    )
    workflow_name = _workflow_name_in_scope(db, organization_id, workflow_id)
    try:
        budget = WorkflowBudgetService.upsert_budget(
            db,
            organization_id=organization_id,
            workflow_id=workflow_id,
            actor_id=current_user.id,
            monthly_budget_usd=body.monthly_budget_usd,
            is_enabled=body.is_enabled,
        )
    except AppPrimaryChangedDuringMutationError:
        db.rollback()
        raise_api_error(
            request,
            409,
            "workflow.primary_changed",
            "The App primary Workflow changed. Refresh and try again.",
        )
    except WorkflowBudgetScopeUnavailableError:
        db.rollback()
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Workflow not found.",
        )
    return _serialize_workflow_budget(
        db, budget, workflow_name, include_usage=True
    )


@router.get(
    "/permission-requests",
    response_model=PermissionRequestListResponse,
)
def list_permission_requests(
    request: Request,
    status: Literal["pending", "approved", "rejected"] = Query(default="pending"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """권한 신청 목록 조회 (FR-014)."""
    organization_id = _resolve_managed_organization(
        db, request, x_organization_id, current_user
    )
    total, items = PermissionRequestService.list_requests(
        db, organization_id, status=status, page=page, limit=limit
    )
    return PermissionRequestListResponse(
        total=total,
        items=_serialize_requests(db, items),
    )


@router.post(
    "/permission-requests/{request_id}/approve",
    response_model=PermissionRequestResponse,
)
def approve_permission_request(
    request: Request,
    request_id: UUID,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """권한 신청 승인 (FR-014, ADR-0016)."""
    organization_id = _resolve_managed_organization(
        db, request, x_organization_id, current_user
    )
    approved = PermissionRequestService.approve_request(
        db,
        request_id=request_id,
        organization_id=organization_id,
        decided_by=current_user.id,
    )
    return _serialize_requests(db, [approved])[0]


@router.post(
    "/permission-requests/{request_id}/reject",
    response_model=PermissionRequestResponse,
)
def reject_permission_request(
    request: Request,
    request_id: UUID,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """권한 신청 거절 (FR-014, ADR-0016)."""
    organization_id = _resolve_managed_organization(
        db, request, x_organization_id, current_user
    )
    rejected = PermissionRequestService.reject_request(
        db,
        request_id=request_id,
        organization_id=organization_id,
        decided_by=current_user.id,
    )
    return _serialize_requests(db, [rejected])[0]


@router.get(
    "/app-creation-permissions",
    response_model=AppCreationPermissionListResponse,
)
def list_app_creation_permissions(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """App 생성 권한 보유 목록 조회 (FR-014 회수 확장, ADR-0016)."""
    organization_id = _resolve_managed_organization(
        db, request, x_organization_id, current_user
    )
    total, items = AppCreationPermissionService.list_permissions(
        db, organization_id, page=page, limit=limit
    )
    return AppCreationPermissionListResponse(
        total=total,
        items=_serialize_app_creation_permissions(db, items),
    )


@router.delete(
    "/app-creation-permissions/{permission_id}",
    response_model=AppCreationPermissionRevokeResponse,
)
def revoke_app_creation_permission(
    request: Request,
    permission_id: UUID,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """App 생성 권한 회수 (FR-014 회수 확장, ADR-0016)."""
    organization_id = _resolve_managed_organization(
        db, request, x_organization_id, current_user
    )
    revoked = AppCreationPermissionService.revoke_permission(
        db,
        permission_id=permission_id,
        organization_id=organization_id,
        revoked_by=current_user.id,
    )
    return AppCreationPermissionRevokeResponse(
        id=revoked.id,
        user_id=revoked.user_id,
    )


@router.get(
    "/security-alerts",
    response_model=SecurityAlertListResponse,
)
def list_security_alerts(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    severity: SecurityAlertSeverity | None = None,
    status: SecurityAlertStatus | None = None,
    rule_id: Annotated[
        SecurityAlertRuleId | None, Query(alias="ruleId")
    ] = None,
    actor_id: Annotated[UUID | None, Query(alias="actorId")] = None,
    start_at: Annotated[datetime | None, Query(alias="startAt")] = None,
    end_at: Annotated[datetime | None, Query(alias="endAt")] = None,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_security_alert_organization(
        db, request, x_organization_id, current_user, "security_alert.list"
    )
    period = SecurityAlertService.resolve_period(request, start_at, end_at)
    return SecurityAlertService.list_alerts(
        db,
        organization_id=organization_id,
        filters=SecurityAlertFilters(
            severity=severity,
            status=status,
            rule_id=rule_id,
            actor_id=actor_id,
            start_at=period.start_at,
            end_at=period.end_at,
        ),
        page=page,
        limit=limit,
    )


# Static route는 UUID 동적 route보다 먼저 등록한다.
@router.get(
    "/security-alerts/summary",
    response_model=SecurityAlertSummaryResponse,
)
def get_security_alert_summary(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_security_alert_organization(
        db, request, x_organization_id, current_user, "security_alert.summary"
    )
    return SecurityAlertService.get_summary(db, organization_id=organization_id)


@router.get(
    "/security-alerts/{alert_id}",
    response_model=SecurityAlertDetail,
)
def get_security_alert_detail(
    request: Request,
    alert_id: UUID,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_security_alert_organization(
        db, request, x_organization_id, current_user, "security_alert.detail"
    )
    return SecurityAlertService.get_detail(
        db,
        request=request,
        organization_id=organization_id,
        alert_id=alert_id,
    )


@router.get(
    "/security-alerts/{alert_id}/audit-logs",
    response_model=SecurityAlertAuditLogListResponse,
)
def list_security_alert_audit_logs(
    request: Request,
    alert_id: UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_security_alert_organization(
        db,
        request,
        x_organization_id,
        current_user,
        "security_alert.evidence.list",
    )
    return SecurityAlertService.list_evidence(
        db,
        request=request,
        organization_id=organization_id,
        alert_id=alert_id,
        page=page,
        limit=limit,
    )


@router.post(
    "/security-alerts/{alert_id}/acknowledge",
    response_model=SecurityAlertDetail,
)
def acknowledge_security_alert_endpoint(
    request: Request,
    alert_id: UUID,
    body: SecurityAlertAcknowledgeRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_security_alert_organization(
        db,
        request,
        x_organization_id,
        current_user,
        "security_alert.acknowledge",
    )
    return _run_security_alert_mutation(
        request,
        lambda: SecurityAlertService.acknowledge(
            db,
            request=request,
            organization_id=organization_id,
            alert_id=alert_id,
            manager_id=current_user.id,
            expected_version=body.expected_version,
        ),
    )


@router.post(
    "/security-alerts/{alert_id}/resolve",
    response_model=SecurityAlertDetail,
)
def resolve_security_alert_endpoint(
    request: Request,
    alert_id: UUID,
    body: SecurityAlertResolveRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_security_alert_organization(
        db, request, x_organization_id, current_user, "security_alert.resolve"
    )
    return _run_security_alert_mutation(
        request,
        lambda: SecurityAlertService.resolve(
            db,
            request=request,
            organization_id=organization_id,
            alert_id=alert_id,
            manager_id=current_user.id,
            expected_version=body.expected_version,
            resolution_type=body.resolution_type,
            reason=body.reason,
        ),
    )


@router.post(
    "/security-alerts/{alert_id}/reopen",
    response_model=SecurityAlertDetail,
)
def reopen_security_alert_endpoint(
    request: Request,
    alert_id: UUID,
    body: SecurityAlertReopenRequest,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    organization_id = _resolve_security_alert_organization(
        db, request, x_organization_id, current_user, "security_alert.reopen"
    )
    return _run_security_alert_mutation(
        request,
        lambda: SecurityAlertService.reopen(
            db,
            request=request,
            organization_id=organization_id,
            alert_id=alert_id,
            manager_id=current_user.id,
            expected_version=body.expected_version,
        ),
    )
