from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.shared.celery_app import celery_app
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow_run import (
    TraceRedactionPolicy,
    TraceRetentionPolicy,
    TraceVisibilityPolicy,
)
from apps.shared.db.session import get_db
from apps.shared.schemas.tracing import (
    RetentionPurgeRequest,
    RetentionPurgeResponse,
    TraceDetailSchema,
    TraceListResponse,
    TracePayloadSchema,
    TracePolicyResponse,
    TraceRedactionPolicyPatch,
    TraceRetentionPolicyPatch,
    TraceSpanSchema,
    TraceVisibilityPolicyPatch,
    ViewLevel,
)
from apps.shared.services.tracing.access import TraceAccessService
from apps.shared.services.tracing.policy import TracePolicyService
from apps.shared.services.tracing.query import TraceQueryService
from apps.shared.services.tracing.retention import TraceRetentionService


router = APIRouter()


def _raise_for_service_error(error: Exception):
    if isinstance(error, LookupError):
        raise HTTPException(status_code=404, detail=str(error))
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=403, detail=str(error))
    if isinstance(error, ValueError):
        raise HTTPException(status_code=400, detail=str(error))
    raise HTTPException(status_code=500, detail="Tracing service error")


def _require_system_admin(db: Session, current_user: User):
    # 시스템 관리자 판별은 User/RBAC 제공자 경계로 위임합니다.
    if not TraceAccessService.is_system_admin(db, current_user):
        raise HTTPException(status_code=403, detail="system_admin_required")


def _policy_response(policy, values: dict) -> TracePolicyResponse:
    return TracePolicyResponse(
        id=getattr(policy, "id", None),
        scope_type=getattr(policy, "scope_type", values.get("scope_type", "global")),
        scope_id=getattr(policy, "scope_id", values.get("scope_id")),
        is_active=getattr(policy, "is_active", values.get("is_active", True)),
        updated_by=getattr(policy, "updated_by", None),
        updated_at=getattr(policy, "updated_at", None),
        values=values,
    )


def _model_values(policy) -> dict:
    return {
        key: value
        for key, value in vars(policy).items()
        if not key.startswith("_")
        and key
        not in {
            "id",
            "scope_type",
            "scope_id",
            "updated_by",
            "created_at",
            "updated_at",
        }
    }


@router.get("/traces", response_model=TraceListResponse)
def list_traces(
    status: Optional[str] = None,
    trigger_mode: Optional[str] = None,
    from_datetime: Optional[datetime] = Query(default=None, alias="from"),
    to_datetime: Optional[datetime] = Query(default=None, alias="to"),
    app_id: Optional[UUID] = None,
    workflow_id: Optional[UUID] = None,
    deployment_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    correlation_id: Optional[str] = None,
    page: int = 1,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return TraceQueryService.list_traces(
            db,
            current_user,
            status=status,
            trigger_mode=trigger_mode,
            app_id=str(app_id) if app_id else None,
            workflow_id=str(workflow_id) if workflow_id else None,
            deployment_id=str(deployment_id) if deployment_id else None,
            user_id=str(user_id) if user_id else None,
            correlation_id=correlation_id,
            from_datetime=from_datetime,
            to_datetime=to_datetime,
            page=page,
            limit=limit,
        )
    except Exception as error:
        _raise_for_service_error(error)


@router.get("/traces/{trace_id}", response_model=TraceDetailSchema)
def get_trace(
    trace_id: UUID,
    view: ViewLevel = "metadata",
    include_spans: bool = False,
    include_payloads: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return TraceQueryService.get_trace(
            db,
            str(trace_id),
            current_user,
            view_level=view,
            include_spans=include_spans,
            include_payloads=include_payloads,
        )
    except Exception as error:
        _raise_for_service_error(error)


@router.get("/traces/{trace_id}/spans", response_model=list[TraceSpanSchema])
def list_trace_spans(
    trace_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return TraceQueryService.list_spans(db, str(trace_id), current_user)
    except Exception as error:
        _raise_for_service_error(error)


@router.get("/traces/{trace_id}/payloads", response_model=list[TracePayloadSchema])
def list_trace_payloads(
    trace_id: UUID,
    view: ViewLevel = "redacted",
    payload_kind: Optional[str] = None,
    node_run_id: Optional[UUID] = None,
    history: bool = False,
    page: int = 1,
    limit: int = Query(default=1000, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return TraceQueryService.list_payloads(
            db,
            str(trace_id),
            current_user,
            view_level=view,
            payload_kind=payload_kind,
            node_run_id=str(node_run_id) if node_run_id else None,
            history=history,
            page=page,
            limit=limit,
        )
    except Exception as error:
        _raise_for_service_error(error)


@router.get(
    "/traces/{trace_id}/payloads/{payload_id}", response_model=TracePayloadSchema
)
def get_trace_payload(
    trace_id: UUID,
    payload_id: UUID,
    view: ViewLevel = "redacted",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return TraceQueryService.get_payload(
            db,
            str(trace_id),
            str(payload_id),
            current_user,
            view_level=view,
        )
    except Exception as error:
        _raise_for_service_error(error)


@router.post("/tracing/retention/purge", response_model=RetentionPurgeResponse)
def purge_trace_retention(
    request: RetentionPurgeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_system_admin(db, current_user)
    if request.dry_run:
        try:
            result = TraceRetentionService.purge(
                db,
                scope_type=request.scope_type,
                scope_id=str(request.scope_id) if request.scope_id else None,
                dry_run=True,
                limit=request.limit,
            )
            return RetentionPurgeResponse(
                dry_run=True, status="completed", result=result
            )
        except Exception as error:
            _raise_for_service_error(error)

    # 실제 purge는 요청 스레드를 점유하지 않도록 Celery task로 위임합니다.
    task = celery_app.send_task(
        "log.trace_retention_purge",
        args=[
            {
                "scope_type": request.scope_type,
                "scope_id": str(request.scope_id) if request.scope_id else None,
                "dry_run": False,
                "limit": request.limit,
            }
        ],
    )
    return RetentionPurgeResponse(task_id=task.id, dry_run=False, status="queued")


@router.get("/tracing/policies/redaction", response_model=TracePolicyResponse)
def get_redaction_policy(
    scope_type: str = "global",
    scope_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_system_admin(db, current_user)
    policy = TracePolicyService.get_policy(
        db, TraceRedactionPolicy, scope_type, scope_id
    )
    if policy:
        return _policy_response(policy, _model_values(policy))
    fallback = TracePolicyService.bootstrap_redaction_policy()
    return _policy_response(fallback, fallback.model_dump())


@router.patch("/tracing/policies/redaction", response_model=TracePolicyResponse)
def patch_redaction_policy(
    request: TraceRedactionPolicyPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_system_admin(db, current_user)
    values = request.model_dump(exclude_unset=True)
    scope_type = values.pop("scope_type", "global")
    scope_id = values.pop("scope_id", None)
    policy = TracePolicyService.upsert_policy(
        db, TraceRedactionPolicy, scope_type, scope_id, values, current_user.id
    )
    return _policy_response(policy, _model_values(policy))


@router.get("/tracing/policies/retention", response_model=TracePolicyResponse)
def get_retention_policy(
    scope_type: str = "global",
    scope_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_system_admin(db, current_user)
    policy = TracePolicyService.get_policy(
        db, TraceRetentionPolicy, scope_type, scope_id
    )
    if policy:
        return _policy_response(policy, _model_values(policy))
    fallback = TracePolicyService.bootstrap_retention_policy()
    return _policy_response(fallback, fallback.model_dump())


@router.patch("/tracing/policies/retention", response_model=TracePolicyResponse)
def patch_retention_policy(
    request: TraceRetentionPolicyPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_system_admin(db, current_user)
    values = request.model_dump(exclude_unset=True)
    scope_type = values.pop("scope_type", "global")
    scope_id = values.pop("scope_id", None)
    policy = TracePolicyService.upsert_policy(
        db, TraceRetentionPolicy, scope_type, scope_id, values, current_user.id
    )
    return _policy_response(policy, _model_values(policy))


@router.get("/tracing/policies/visibility", response_model=TracePolicyResponse)
def get_visibility_policy(
    scope_type: str = "global",
    scope_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_system_admin(db, current_user)
    policy = TracePolicyService.get_policy(
        db, TraceVisibilityPolicy, scope_type, scope_id
    )
    if policy:
        return _policy_response(policy, _model_values(policy))
    fallback = TracePolicyService.bootstrap_visibility_policy()
    return _policy_response(fallback, fallback.model_dump())


@router.patch("/tracing/policies/visibility", response_model=TracePolicyResponse)
def patch_visibility_policy(
    request: TraceVisibilityPolicyPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_system_admin(db, current_user)
    values = request.model_dump(exclude_unset=True)
    scope_type = values.pop("scope_type", "global")
    scope_id = values.pop("scope_id", None)
    policy = TracePolicyService.upsert_policy(
        db, TraceVisibilityPolicy, scope_type, scope_id, values, current_user.id
    )
    return _policy_response(policy, _model_values(policy))
