from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.services.organization_context import get_user_primary_organization_id
from apps.shared.db.models.audit_log import ActorType, AuditLog, AuditStatus
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.schemas.audit import AuditLogListResponse
from apps.shared.schemas.auth import UserResponse
from apps.shared.services.permissions import (
    has_organization_manager_permission,
    has_organization_scope_access,
)

router = APIRouter()


@router.get("", response_model=list[UserResponse])
def list_users(
    organization_id: UUID | None = None,
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if organization_id is None:
        organization_id = get_user_primary_organization_id(db, current_user.id)
        if organization_id is None:
            raise HTTPException(status_code=404, detail="Organization not found")
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    if not has_organization_manager_permission(db, current_user.id, organization_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    query = (
        db.query(User)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .filter(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.membership_state == ORGANIZATION_MEMBERSHIP_ACTIVE,
            User.deactivated_at.is_(None),
        )
        .distinct()
        .order_by(User.name.asc(), User.email.asc())
    )
    if q:
        pattern = f"%{q}%"
        query = query.filter((User.name.ilike(pattern)) | (User.email.ilike(pattern)))

    users = query.limit(limit).all()
    return [
        {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "created_at": user.created_at,
        }
        for user in users
    ]


@router.get("/me/audit-logs", response_model=AuditLogListResponse)
def list_my_audit_logs(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    status: Annotated[AuditStatus | None, Query()] = None,
    startAt: Annotated[datetime | None, Query()] = None,
    endAt: Annotated[datetime | None, Query()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(AuditLog).filter(
        AuditLog.actor_id == current_user.id,
        AuditLog.actor_type == ActorType.USER,
    )
    if status is not None:
        query = query.filter(AuditLog.status == status)
    if startAt is not None:
        query = query.filter(AuditLog.occurred_at >= startAt)
    if endAt is not None:
        query = query.filter(AuditLog.occurred_at < endAt)
    items = (
        query.order_by(desc(AuditLog.occurred_at), desc(AuditLog.id))
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": query.count(),
        "items": [
            {
                "id": item.id,
                "occurred_at": item.occurred_at,
                "actor_id": item.actor_id,
                "actor_type": item.actor_type,
                "category": item.category,
                "action": item.action,
                "target_type": item.target_type,
                "target_id": item.target_id,
                "status": item.status,
                "request_id": (item.audit_metadata or {}).get("request_id"),
            }
            for item in items
        ],
    }
