from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.gateway.adapters.db.access_management_locking import (
    lock_access_subject_rows,
)
from apps.gateway.services.audit_records import add_action_audit
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.user_app_creation_permission import (
    UserAppCreationPermission,
)


class AppCreationPermissionService:
    """App 생성 권한 보유 목록 조회/회수 (FR-014 회수 확장, ADR-0016)."""

    @staticmethod
    def list_permissions(
        db: Session,
        organization_id: Any,
        page: int = 1,
        limit: int = 20,
    ):
        query = db.query(UserAppCreationPermission).filter(
            UserAppCreationPermission.grantee_organization_id == organization_id
        )
        total = query.count()
        items = (
            query.order_by(UserAppCreationPermission.assigned_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return total, items

    @staticmethod
    def revoke_permission(
        db: Session,
        permission_id: Any,
        organization_id: Any,
        revoked_by: Any,
    ) -> UserAppCreationPermission:
        permission = (
            db.query(UserAppCreationPermission)
            .filter(UserAppCreationPermission.id == permission_id)
            .first()
        )
        # 없는 row와 scope 밖 row는 존재를 숨긴다 (ADR-0010).
        if permission is None or str(permission.grantee_organization_id) != str(
            organization_id
        ):
            raise HTTPException(
                status_code=404, detail="App creation permission not found"
            )
        lock_access_subject_rows(
            db,
            permission.grantee_organization_id,
            permission.user_id,
            manager_reduction=False,
        )
        permission = (
            db.query(UserAppCreationPermission)
            .filter(
                UserAppCreationPermission.id == permission_id,
                UserAppCreationPermission.grantee_organization_id == organization_id,
            )
            .with_for_update()
            .first()
        )
        if permission is None:
            raise HTTPException(
                status_code=404, detail="App creation permission not found"
            )
        before = {
            "grantee_organization_id": permission.grantee_organization_id,
            "user_id": permission.user_id,
        }
        db.delete(permission)
        try:
            add_action_audit(
                db,
                AuditAction.USER_APP_CREATION_PERMISSION_DELETED,
                actor_id=revoked_by,
                target_type="user_app_creation_permission",
                target_id=permission.id,
                organization_id=permission.grantee_organization_id,
                before=before,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return permission
