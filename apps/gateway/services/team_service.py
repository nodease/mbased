import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from apps.gateway.adapters.db.access_management_locking import (
    lock_access_subject_rows,
)
from apps.gateway.auth.permissions import record_permission_denied
from apps.gateway.services.audit_records import add_data_change_audit
from apps.gateway.services.resource_permission_registry import (
    ResourceTargetNotFound,
    ResourceTypeNotRegistered,
    effective_resource_auth_state,
    permission_model_and_filters,
    resource_auth_state_allows,
    resource_organization_id,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.audit.context import get_current_metadata
from apps.shared.audit.logger import record_audit
from apps.shared.audit.manual_ownership import register_manual_audit_ownership
from apps.shared.db.models.team import (
    Team,
    TeamMembership,
)
from apps.shared.db.models.user import User
from apps.shared.permissions import (
    AUTH_STATE_BUILDER,
    AUTH_STATE_MANAGER,
    AUTH_STATE_NONE,
    AUTH_STATE_OPERATOR,
    AUTH_STATE_VIEWER,
    is_canonical_auth_state,
    normalize_auth_state,
)
from apps.shared.schemas.team import (
    ResourcePermissionGrantRequest,
    ResourcePermissionRevokeRequest,
    TeamCreateRequest,
    TeamMembershipRequest,
    TeamUpdateRequest,
)
from apps.shared.services.permissions import (
    has_active_organization_membership,
    has_organization_manager_permission,
    has_organization_scope_access,
)

RESOURCE_AUTH_STATES = {
    AUTH_STATE_NONE,
    AUTH_STATE_VIEWER,
    AUTH_STATE_OPERATOR,
    AUTH_STATE_BUILDER,
    AUTH_STATE_MANAGER,
}


@dataclass(frozen=True)
class TeamManagerScope:
    user_id: Any
    organization_id: Any


def _ensure_organization_manager(
    db: Session,
    current_user: User,
    organization_id: Any,
) -> TeamManagerScope:
    if has_organization_manager_permission(db, current_user.id, organization_id):
        return TeamManagerScope(
            user_id=current_user.id,
            organization_id=organization_id,
        )

    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise HTTPException(status_code=404, detail="Organization not found.")

    record_permission_denied(
        current_user,
        "organization",
        organization_id,
        "manage",
        AUTH_STATE_NONE,
        organization_id,
    )
    raise HTTPException(
        status_code=403,
        detail="Organization manager permission is required.",
    )


def _ensure_or_validate_manager_scope(
    db: Session,
    current_user: User,
    organization_id: Any,
    manager_scope: TeamManagerScope | None,
) -> TeamManagerScope:
    if manager_scope is None:
        return _ensure_organization_manager(db, current_user, organization_id)

    if (
        manager_scope.user_id != current_user.id
        or manager_scope.organization_id != organization_id
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    return manager_scope


def _get_team(
    db: Session,
    team_id: Any,
    organization_id: Any | None = None,
    *,
    active_only: bool = False,
) -> Team:
    # 변경 API는 X-Organization-Id에서 온 organization_id를 넘겨
    # route team_id가 active organization scope 밖으로 나가지 못하게 한다.
    filters = [Team.id == team_id]
    if organization_id is not None:
        filters.append(Team.organization_id == organization_id)
    if active_only:
        filters.append(Team.is_active.is_(True))

    team = db.query(Team).filter(*filters).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


def _get_active_user(db: Session, user_id: Any) -> User:
    # Team/direct permission subject는 비활성화되지 않은 user만 허용한다.
    user = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.deactivated_at.is_(None),
        )
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _get_team_by_name(
    db: Session,
    organization_id: Any,
    name: str,
) -> Team | None:
    return (
        db.query(Team)
        .filter(
            Team.organization_id == organization_id,
            Team.name == name,
        )
        .first()
    )


def _ensure_team_name_available(
    db: Session,
    organization_id: Any,
    name: str,
    exclude_team_id: Any | None = None,
) -> None:
    existing = _get_team_by_name(db, organization_id, name)
    if existing is not None and existing.id != exclude_team_id:
        raise HTTPException(status_code=409, detail="Team name already exists")


def _commit_or_conflict(
    db: Session,
    message: str,
) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=message) from exc


def _resource_organization_id(
    db: Session,
    resource_type: str,
    resource_id: Any,
) -> Any:
    try:
        return resource_organization_id(db, resource_type, resource_id)
    except ResourceTargetNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except ResourceTypeNotRegistered as exc:
        raise HTTPException(status_code=400, detail="Invalid resource_type") from exc


def _ensure_resource_permission_manager(
    db: Session,
    current_user: User,
    resource_type: str,
    resource_id: Any,
    organization_id: Any,
) -> None:
    if has_organization_manager_permission(db, current_user.id, organization_id):
        return

    try:
        effective_auth_state = effective_resource_auth_state(
            db,
            resource_type=resource_type,
            user_id=current_user.id,
            resource_id=resource_id,
            organization_id=organization_id,
        )
        if resource_auth_state_allows(resource_type, effective_auth_state, "manage"):
            return
    except ResourceTypeNotRegistered as exc:
        raise HTTPException(status_code=400, detail="Invalid resource_type") from exc

    record_permission_denied(
        current_user,
        resource_type,
        resource_id,
        "manage",
        effective_auth_state,
        organization_id,
    )
    raise HTTPException(status_code=403, detail="Forbidden")


def _validate_grant_request(
    db: Session,
    request: ResourcePermissionGrantRequest,
) -> str:
    auth_state = normalize_auth_state(request.auth_state)
    if (
        not is_canonical_auth_state(request.auth_state)
        or auth_state not in RESOURCE_AUTH_STATES
    ):
        raise HTTPException(status_code=400, detail="Invalid auth_state")

    resource_organization_id = _resource_organization_id(
        db, request.resource_type, request.resource_id
    )
    if resource_organization_id != request.organization_id:
        raise HTTPException(status_code=400, detail="Resource organization mismatch")
    return auth_state


def _ensure_grantee_user_membership(
    db: Session,
    user_id: Any,
    organization_id: Any,
) -> None:
    _get_active_user(db, user_id)
    if not has_active_organization_membership(db, user_id, organization_id):
        raise HTTPException(
            status_code=400,
            detail="Grantee user is not a member of the organization",
        )


def _ensure_user_organization_scope(
    db: Session,
    user_id: Any,
    organization_id: Any,
) -> None:
    if not has_organization_scope_access(db, user_id, organization_id):
        raise HTTPException(
            status_code=400,
            detail="Managed user is not in the organization",
        )


def _record_permission_mutation(
    action: str,
    current_user: User,
    target_id: Any,
    request: ResourcePermissionGrantRequest | ResourcePermissionRevokeRequest,
    auth_state: str | None = None,
) -> None:
    metadata = {
        "resource_type": request.resource_type,
        "resource_id": str(request.resource_id),
        "grant_subject_type": request.grantee_type,
        "grant_subject_id": str(request.grantee_id),
        "grantee_type": request.grantee_type,
        "grantee_id": str(request.grantee_id),
        "organization_id": str(request.organization_id),
    }
    if auth_state is not None:
        metadata["auth_state"] = auth_state

    record_audit(
        action=action,
        category="action",
        actor_id=current_user.id,
        actor_type="user",
        target_type="permission",
        target_id=target_id,
        metadata=metadata,
    )


def _access_mutation_audit_metadata(current_user: User) -> dict[str, Any]:
    metadata = get_current_metadata()
    metadata["actor"] = {
        "id": str(current_user.id),
        "email": getattr(current_user, "email", None),
        "name": getattr(current_user, "name", None),
    }
    return metadata


def _team_membership_audit_snapshot(row: TeamMembership) -> dict[str, Any]:
    return {
        "grantee_organization_id": row.grantee_organization_id,
        "team_id": row.team_id,
        "user_id": row.user_id,
    }


class TeamService:
    @staticmethod
    def ensure_organization_manager_scope(
        db: Session,
        current_user: User,
        organization_id: Any,
    ) -> TeamManagerScope:
        return _ensure_organization_manager(db, current_user, organization_id)

    @staticmethod
    def list_teams(
        db: Session,
        current_user: User,
        organization_id: Any,
        limit: int | None = None,
        manager_scope: TeamManagerScope | None = None,
    ) -> list[Team]:
        _ensure_or_validate_manager_scope(
            db,
            current_user,
            organization_id,
            manager_scope,
        )
        query = (
            db.query(Team)
            .filter(Team.organization_id == organization_id)
            .order_by(Team.name.asc(), Team.id.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        return query.all()

    @staticmethod
    def list_members(
        db: Session,
        current_user: User,
        team_id: Any,
        organization_id: Any,
        manager_scope: TeamManagerScope | None = None,
    ) -> list[TeamMembership]:
        team = _get_team(db, team_id, organization_id)
        _ensure_or_validate_manager_scope(
            db,
            current_user,
            team.organization_id,
            manager_scope,
        )
        return (
            db.query(TeamMembership)
            .options(joinedload(TeamMembership.user))
            .join(User, User.id == TeamMembership.user_id)
            .filter(
                TeamMembership.grantee_organization_id == team.organization_id,
                TeamMembership.team_id == team.id,
                User.deactivated_at.is_(None),
            )
            .order_by(User.name.asc(), User.email.asc(), TeamMembership.id.asc())
            .all()
        )

    @staticmethod
    def create_team(
        db: Session,
        current_user: User,
        request: TeamCreateRequest,
        manager_scope: TeamManagerScope | None = None,
    ) -> Team:
        _ensure_or_validate_manager_scope(
            db,
            current_user,
            request.organization_id,
            manager_scope,
        )
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Team name is required")
        _ensure_team_name_available(db, request.organization_id, name)

        team = Team(
            organization_id=request.organization_id,
            name=name,
            description=request.description,
            created_by=current_user.id,
            managed_by=current_user.id,
            is_auto_add=request.is_auto_add,
        )
        db.add(team)
        _commit_or_conflict(db, "Team name already exists")
        db.refresh(team)
        return team

    @staticmethod
    def update_team(
        db: Session,
        current_user: User,
        team_id: Any,
        request: TeamUpdateRequest,
        organization_id: Any | None = None,
        manager_scope: TeamManagerScope | None = None,
    ) -> Team:
        team = _get_team(db, team_id, organization_id, active_only=True)
        _ensure_or_validate_manager_scope(
            db,
            current_user,
            team.organization_id,
            manager_scope,
        )

        # PATCH는 description, managed_by 같은 nullable column에서
        # 생략된 field와 명시적 null 값을 구분해야 한다.
        fields = request.model_fields_set
        if "name" in fields:
            if request.name is None or request.name.strip() == "":
                raise HTTPException(status_code=400, detail="Team name is required")
            name = request.name.strip()
            _ensure_team_name_available(
                db,
                team.organization_id,
                name,
                exclude_team_id=team.id,
            )
            team.name = name
        if "description" in fields:
            team.description = request.description
        if "managed_by" in fields:
            if request.managed_by is not None:
                _get_active_user(db, request.managed_by)
                # 정책 참고: 현재 RBAC 문서는 organization owner/manager도
                # organization scope 안 manager로 본다. 향후 managed_by 대상을
                # 반드시 active organization membership row 보유자로 제한하기로 바뀌면
                # _ensure_user_organization_scope 조건을 membership-only로 좁힌다.
                _ensure_user_organization_scope(
                    db,
                    request.managed_by,
                    team.organization_id,
                )
            team.managed_by = request.managed_by
        if "is_auto_add" in fields:
            team.is_auto_add = request.is_auto_add
        _commit_or_conflict(db, "Team name already exists")
        db.refresh(team)
        return team

    @staticmethod
    def deactivate_team(
        db: Session,
        current_user: User,
        team_id: Any,
        organization_id: Any | None = None,
        manager_scope: TeamManagerScope | None = None,
    ) -> dict:
        team = _get_team(db, team_id, organization_id)
        _ensure_or_validate_manager_scope(
            db,
            current_user,
            team.organization_id,
            manager_scope,
        )
        if not team.is_active:
            # 비활성 team은 일반 권한 판정에서는 거부하지만, deactivate는
            # admin UI 재시도 안정성을 위해 scope 안 요청이면 idempotent하게 성공한다.
            return {"status": "deactivated"}
        team.is_active = False
        team.deactivated_at = datetime.now(timezone.utc)
        db.commit()
        return {"status": "deactivated"}

    @staticmethod
    def add_membership(
        db: Session,
        current_user: User,
        team_id: Any,
        request: TeamMembershipRequest,
        organization_id: Any | None = None,
        manager_scope: TeamManagerScope | None = None,
    ) -> TeamMembership:
        team = _get_team(db, team_id, organization_id, active_only=True)
        _ensure_or_validate_manager_scope(
            db,
            current_user,
            team.organization_id,
            manager_scope,
        )
        _ensure_grantee_user_membership(db, request.user_id, team.organization_id)
        locked_subject = lock_access_subject_rows(
            db,
            team.organization_id,
            request.user_id,
            manager_reduction=False,
        )
        if (
            locked_subject is None
            or locked_subject.membership.membership_state != "active"
            or locked_subject.user.deactivated_at is not None
        ):
            raise HTTPException(
                status_code=400,
                detail="Grantee user is not a member of the organization",
            )
        team = (
            db.query(Team)
            .filter(
                Team.id == team.id,
                Team.organization_id == team.organization_id,
                Team.is_active.is_(True),
            )
            .with_for_update()
            .first()
        )
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found")
        membership = (
            db.query(TeamMembership)
            .filter(
                TeamMembership.grantee_organization_id == team.organization_id,
                TeamMembership.team_id == team.id,
                TeamMembership.user_id == request.user_id,
            )
            .with_for_update()
            .first()
        )
        if membership:
            return membership

        membership = TeamMembership(
            id=uuid.uuid4(),
            grantee_organization_id=team.organization_id,
            team_id=team.id,
            user_id=request.user_id,
            assigned_by=current_user.id,
        )
        register_manual_audit_ownership(db, membership, "created")
        db.add(membership)
        try:
            add_data_change_audit(
                db,
                action="team_membership.created",
                actor_id=current_user.id,
                target_type="team_membership",
                target_id=membership.id,
                before=None,
                after=_team_membership_audit_snapshot(membership),
                organization_id=team.organization_id,
                metadata=_access_mutation_audit_metadata(current_user),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            membership = (
                db.query(TeamMembership)
                .filter(
                    TeamMembership.grantee_organization_id == team.organization_id,
                    TeamMembership.team_id == team.id,
                    TeamMembership.user_id == request.user_id,
                )
                .first()
            )
            if membership:
                return membership
            raise HTTPException(
                status_code=409,
                detail="Team membership already exists",
            ) from exc
        except Exception:
            db.rollback()
            raise
        db.refresh(membership)
        return membership

    @staticmethod
    def remove_membership(
        db: Session,
        current_user: User,
        team_id: Any,
        user_id: Any,
        organization_id: Any | None = None,
        manager_scope: TeamManagerScope | None = None,
    ) -> dict:
        team = _get_team(db, team_id, organization_id, active_only=True)
        _ensure_or_validate_manager_scope(
            db,
            current_user,
            team.organization_id,
            manager_scope,
        )
        lock_access_subject_rows(
            db,
            team.organization_id,
            user_id,
            manager_reduction=False,
        )
        team = (
            db.query(Team)
            .filter(
                Team.id == team.id,
                Team.organization_id == team.organization_id,
                Team.is_active.is_(True),
            )
            .with_for_update()
            .first()
        )
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found")
        # 없는 member 제거는 admin UI 재시도 안정성을 위해 idempotent하게 처리한다.
        membership = (
            db.query(TeamMembership)
            .filter(
                TeamMembership.grantee_organization_id == team.organization_id,
                TeamMembership.team_id == team.id,
                TeamMembership.user_id == user_id,
            )
            .with_for_update()
            .first()
        )
        if membership:
            before = _team_membership_audit_snapshot(membership)
            register_manual_audit_ownership(db, membership, "deleted")
            db.delete(membership)
            try:
                add_data_change_audit(
                    db,
                    action="team_membership.deleted",
                    actor_id=current_user.id,
                    target_type="team_membership",
                    target_id=membership.id,
                    before=before,
                    after=None,
                    organization_id=team.organization_id,
                    metadata=_access_mutation_audit_metadata(current_user),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"status": "removed"}

    @staticmethod
    def grant_resource_permission(
        db: Session,
        current_user: User,
        request: ResourcePermissionGrantRequest,
    ) -> Any:
        auth_state = _validate_grant_request(db, request)
        _ensure_resource_permission_manager(
            db,
            current_user,
            request.resource_type,
            request.resource_id,
            request.organization_id,
        )

        if request.grantee_type == "team":
            team = _get_team(db, request.grantee_id)
            if team.organization_id != request.organization_id:
                raise HTTPException(
                    status_code=400, detail="Team organization mismatch"
                )
            if not getattr(team, "is_active", True):
                raise HTTPException(status_code=400, detail="Team is inactive")
        else:
            _ensure_grantee_user_membership(
                db,
                request.grantee_id,
                request.organization_id,
            )

        model, filters = permission_model_and_filters(
            resource_type=request.resource_type,
            grantee_type=request.grantee_type,
            resource_id=request.resource_id,
            grantee_id=request.grantee_id,
        )

        row = (
            db.query(model)
            .filter(
                model.grantee_organization_id == request.organization_id,
                *(getattr(model, key) == value for key, value in filters.items()),
            )
            .first()
        )
        if not row:
            row = model(
                grantee_organization_id=request.organization_id,
                assigned_by=current_user.id,
                **filters,
            )
            db.add(row)
        row.auth_state = auth_state
        db.commit()
        db.refresh(row)
        _record_permission_mutation(
            AuditAction.PERMISSION_GRANT,
            current_user,
            row.id,
            request,
            auth_state,
        )
        return row

    @staticmethod
    def revoke_resource_permission(
        db: Session,
        current_user: User,
        request: ResourcePermissionRevokeRequest,
    ) -> dict:
        resource_organization_id = _resource_organization_id(
            db, request.resource_type, request.resource_id
        )
        if resource_organization_id != request.organization_id:
            raise HTTPException(
                status_code=400, detail="Resource organization mismatch"
            )
        _ensure_resource_permission_manager(
            db,
            current_user,
            request.resource_type,
            request.resource_id,
            request.organization_id,
        )

        model, filters = permission_model_and_filters(
            resource_type=request.resource_type,
            grantee_type=request.grantee_type,
            resource_id=request.resource_id,
            grantee_id=request.grantee_id,
        )

        row = (
            db.query(model)
            .filter(
                model.grantee_organization_id == request.organization_id,
                *(getattr(model, key) == value for key, value in filters.items()),
            )
            .first()
        )
        target_id = row.id if row else None
        if row:
            db.delete(row)
            db.commit()
        _record_permission_mutation(
            AuditAction.PERMISSION_REVOKE,
            current_user,
            target_id,
            request,
        )
        return {"status": "revoked"}
