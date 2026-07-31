import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.gateway.adapters.db.access_management_locking import (
    lock_access_subject_rows,
)
from apps.gateway.services.audit_records import add_action_audit
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.permission_request import (
    PERMISSION_REQUEST_APPROVED,
    PERMISSION_REQUEST_PENDING,
    PERMISSION_REQUEST_REJECTED,
    REQUESTED_PERMISSION_APP_CREATE,
    PermissionRequest,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.user_app_creation_permission import (
    UserAppCreationPermission,
)
from apps.shared.services import permissions as shared_permissions


def _mark_decided(
    request: PermissionRequest,
    status: str,
    decided_by: Any,
) -> None:
    request.status = status
    request.decided_by = decided_by
    request.decided_at = datetime.now(timezone.utc)


class PermissionRequestService:
    """권한 신청 제출/조회/승인/거절 (FR-041/FR-014, ADR-0016)."""

    @staticmethod
    def _get_request(db: Session, request_id: Any) -> Optional[PermissionRequest]:
        return (
            db.query(PermissionRequest)
            .filter(PermissionRequest.id == request_id)
            .with_for_update()
            .first()
        )

    @staticmethod
    def _load_processable_request(
        db: Session,
        request_id: Any,
        organization_id: Any,
    ) -> PermissionRequest:
        request = PermissionRequestService._get_request(db, request_id)
        PermissionRequestService.ensure_request_processable(request, organization_id)
        return request

    @staticmethod
    def ensure_request_processable(
        request: Optional[PermissionRequest],
        organization_id: Any,
    ) -> None:
        # scope 밖 신청은 존재를 숨긴다 (ADR-0010).
        if request is None or str(request.organization_id) != str(organization_id):
            raise HTTPException(
                status_code=404, detail="Permission request not found"
            )
        if request.status != PERMISSION_REQUEST_PENDING:
            raise HTTPException(
                status_code=409, detail="Permission request already processed"
            )

    @staticmethod
    def ensure_requester_is_active_member(
        db: Session,
        request: PermissionRequest,
    ) -> None:
        membership = (
            db.query(OrganizationMembership)
            .filter(
                OrganizationMembership.organization_id == request.organization_id,
                OrganizationMembership.user_id == request.user_id,
            )
            .first()
        )
        if (
            membership is None
            or membership.membership_state != ORGANIZATION_MEMBERSHIP_ACTIVE
        ):
            raise HTTPException(
                status_code=409, detail="Requester is not an active member"
            )
        user = db.query(User).filter(User.id == request.user_id).first()
        if user is None or user.deactivated_at is not None:
            raise HTTPException(
                status_code=409, detail="Requester is not an active member"
            )

    @staticmethod
    def grant_app_creation_permission(
        db: Session,
        request: PermissionRequest,
        decided_by: Any,
    ) -> UserAppCreationPermission:
        if request.requested_permission != REQUESTED_PERMISSION_APP_CREATE:
            raise HTTPException(
                status_code=400, detail="Unsupported requested permission"
            )
        locked_subject = lock_access_subject_rows(
            db,
            request.organization_id,
            request.user_id,
            manager_reduction=False,
        )
        if (
            locked_subject is None
            or locked_subject.membership.membership_state
            != ORGANIZATION_MEMBERSHIP_ACTIVE
            or locked_subject.user.deactivated_at is not None
        ):
            raise HTTPException(
                status_code=409,
                detail="Requester is not an active member",
            )
        existing = (
            db.query(UserAppCreationPermission)
            .filter(
                UserAppCreationPermission.grantee_organization_id
                == request.organization_id,
                UserAppCreationPermission.user_id == request.user_id,
            )
            .with_for_update()
            .first()
        )
        if existing is not None:
            raise HTTPException(
                status_code=409, detail="App creation permission already granted"
            )
        permission = UserAppCreationPermission(
            id=uuid.uuid4(),
            grantee_organization_id=request.organization_id,
            user_id=request.user_id,
            assigned_by=decided_by,
            assigned_at=datetime.now(timezone.utc),
        )
        db.add(permission)
        return permission

    @staticmethod
    def approve_request(
        db: Session,
        request_id: Any,
        organization_id: Any,
        decided_by: Any,
    ) -> PermissionRequest:
        request = PermissionRequestService._load_processable_request(
            db, request_id, organization_id
        )
        PermissionRequestService.ensure_requester_is_active_member(db, request)
        permission = PermissionRequestService.grant_app_creation_permission(
            db, request, decided_by=decided_by
        )
        _mark_decided(request, PERMISSION_REQUEST_APPROVED, decided_by)
        try:
            add_action_audit(
                db,
                AuditAction.PERMISSION_REQUEST_APPROVED,
                decided_by,
                "permission_request",
                request.id,
                organization_id=request.organization_id,
            )
            add_action_audit(
                db,
                AuditAction.USER_APP_CREATION_PERMISSION_CREATED,
                decided_by,
                "user_app_creation_permission",
                permission.id,
                organization_id=request.organization_id,
                after={
                    "grantee_organization_id": permission.grantee_organization_id,
                    "user_id": permission.user_id,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return request

    @staticmethod
    def reject_request(
        db: Session,
        request_id: Any,
        organization_id: Any,
        decided_by: Any,
    ) -> PermissionRequest:
        request = PermissionRequestService._load_processable_request(
            db, request_id, organization_id
        )
        _mark_decided(request, PERMISSION_REQUEST_REJECTED, decided_by)
        add_action_audit(
            db,
            AuditAction.PERMISSION_REQUEST_REJECTED,
            decided_by,
            "permission_request",
            request.id,
            organization_id=request.organization_id,
        )
        db.commit()
        return request

    @staticmethod
    def submit_request(
        db: Session,
        user: User,
        organization_id: Any,
        requested_permission: str,
        reason: str,
    ) -> PermissionRequest:
        if shared_permissions.has_app_creation_permission(
            db, user.id, organization_id
        ):
            raise HTTPException(
                status_code=409, detail="App creation permission already granted"
            )
        pending = (
            db.query(PermissionRequest)
            .filter(
                PermissionRequest.organization_id == organization_id,
                PermissionRequest.user_id == user.id,
                PermissionRequest.status == PERMISSION_REQUEST_PENDING,
            )
            .first()
        )
        if pending is not None:
            raise HTTPException(
                status_code=409, detail="Pending permission request already exists"
            )
        request = PermissionRequest(
            id=uuid.uuid4(),
            organization_id=organization_id,
            user_id=user.id,
            requested_permission=requested_permission,
            reason=reason,
            status=PERMISSION_REQUEST_PENDING,
            created_at=datetime.now(timezone.utc),
        )
        db.add(request)
        add_action_audit(
            db,
            AuditAction.PERMISSION_REQUEST_CREATED,
            user.id,
            "permission_request",
            request.id,
            organization_id=request.organization_id,
        )
        db.commit()
        db.refresh(request)
        return request

    @staticmethod
    def list_requests(
        db: Session,
        organization_id: Any,
        status: str = PERMISSION_REQUEST_PENDING,
        page: int = 1,
        limit: int = 20,
    ):
        query = db.query(PermissionRequest).filter(
            PermissionRequest.organization_id == organization_id,
            PermissionRequest.status == status,
        )
        total = query.count()
        items = (
            query.order_by(PermissionRequest.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return total, items
