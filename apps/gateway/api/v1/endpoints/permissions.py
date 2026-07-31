from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import func, inspect, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, joinedload

from apps.gateway.adapters.db.access_management_locking import (
    lock_access_subject_rows,
)
from apps.gateway.application.resource_permissions.mutation import (
    GranteeType,
    MutationOperation,
    PermissionMutationCommand,
    PermissionMutationNotFound,
    PermissionMutationResult,
    ResourceType,
)
from apps.gateway.composition.resource_permission_mutations import (
    build_resource_permission_mutation_use_case,
)
from apps.gateway.services.auth_service import AuthService
from apps.gateway.services.audit_records import add_data_change_audit
from apps.gateway.services.app_lifecycle_lock import (
    AppPrimaryChangedDuringMutationError,
    lock_app_for_workflow_mutation,
)
from apps.gateway.services.mail_credential_service import (
    MailCredentialService,
    MailCredentialServiceError,
)
from apps.gateway.services.resource_permission_registry import resource_permission_spec
from apps.gateway.utils.api_errors import (
    auth_error_code,
    auth_error_message,
    parse_organization_id,
    raise_api_error,
)
from apps.shared.audit.context import get_current_metadata
from apps.shared.audit.logger import record_audit
from apps.shared.audit.manual_ownership import register_manual_audit_ownership
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.team import Team, TeamMembership
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.services.permissions import (
    has_active_organization_membership,
    has_knowledge_domain_permission,
    has_organization_manager_permission,
    has_organization_scope_access,
)
from apps.shared.schemas.mail_credential import MailCredentialPermissionGrant
from apps.shared.schemas.permission import (
    BulkPermissionGrantRequest,
    BulkPermissionGrantResponse,
    KnowledgeDirectPermissionGrantRequest,
    LLMPermissionGrantRequest,
    PermissionGrantRequest,
    TeamKnowledgePermissionResponse,
    TeamLLMPermissionResponse,
    TeamWorkflowPermissionResponse,
    UserKnowledgePermissionResponse,
    UserLLMPermissionResponse,
    UserWorkflowPermissionResponse,
)
from apps.shared.schemas.team import ResourcePermissionListResponse
from apps.shared.services.permission_enforcement import PermissionEnforcementService

router = APIRouter()

_WORKFLOW_PERMISSION_SPEC = resource_permission_spec("workflow")
_KNOWLEDGE_PERMISSION_SPEC = resource_permission_spec("knowledge_base")
_LLM_PERMISSION_SPEC = resource_permission_spec("llm_credential")

# Production permission endpoints resolve every target/team/user table through
# the same fail-closed registry used by generic service paths. Resource-specific
# HTTP/audit contracts remain in this adapter during the incremental migration.
Workflow = _WORKFLOW_PERMISSION_SPEC.target_model
TeamWorkflowPermission = _WORKFLOW_PERMISSION_SPEC.team_route.model
UserWorkflowPermission = _WORKFLOW_PERMISSION_SPEC.user_route.model
KnowledgeBase = _KNOWLEDGE_PERMISSION_SPEC.target_model
TeamKnowledgePermission = _KNOWLEDGE_PERMISSION_SPEC.team_route.model
UserKnowledgePermission = _KNOWLEDGE_PERMISSION_SPEC.user_route.model
LLMCredential = _LLM_PERMISSION_SPEC.target_model
TeamLLMPermission = _LLM_PERMISSION_SPEC.team_route.model
UserLLMPermission = _LLM_PERMISSION_SPEC.user_route.model


def _authenticate(
    request: Request,
    db: Session,
    auth_token: str | None,
) -> User:
    """쿠키 토큰으로 사용자를 인증하고 오류 형식을 맞춘다."""
    # 기존 AuthService 예외를 신규 API error envelope으로 변환한다.
    try:
        return AuthService.get_user_from_token(db, auth_token)
    except HTTPException as exc:
        raise_api_error(
            request,
            exc.status_code,
            auth_error_code(exc, auth_token),
            auth_error_message(exc),
        )


def _lock_workflow_mutation_app_scope(
    request: Request,
    db: Session,
    *,
    organization_id: UUID,
    workflow: Workflow,
) -> None:
    """Hold the App lifecycle lock while mutating a Workflow permission."""
    try:
        app = lock_app_for_workflow_mutation(
            db,
            app_id=workflow.app_id,
            workflow_id=workflow.id,
            organization_id=organization_id,
        )
    except AppPrimaryChangedDuringMutationError:
        db.rollback()
        raise_api_error(
            request,
            409,
            "workflow.primary_changed",
            "The App primary Workflow changed. Refresh and try again.",
        )
    if app is None:
        db.rollback()
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Workflow not found.",
        )


def _has_active_membership(
    db: Session,
    organization_id: UUID,
    user_id: UUID,
) -> bool:
    """사용자의 active organization membership 보유 여부를 확인한다."""
    return has_active_organization_membership(db, user_id, organization_id)


def _has_knowledge_permission_delegate(
    db: Session,
    user_id: UUID,
    organization_id: UUID,
) -> bool:
    return has_knowledge_domain_permission(
        db,
        user_id,
        organization_id,
        "permission_delegate",
    )


def _knowledge_permission_change_authority(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
    knowledge_base_id: UUID,
    is_organization_manager: bool,
) -> str | None:
    if is_organization_manager:
        return "organization_manager"
    if PermissionEnforcementService.has_knowledge_base_manage_permission(
        db,
        organization_id,
        knowledge_base_id,
        user_id,
    ):
        return "resource_manager"
    if _has_knowledge_permission_delegate(db, user_id, organization_id):
        return "domain_delegate"
    return None


def _actor_is_active_member_of_team(
    db: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
    team_id: UUID,
) -> bool:
    return (
        db.query(TeamMembership)
        .join(Team, Team.id == TeamMembership.team_id)
        .filter(
            TeamMembership.grantee_organization_id == organization_id,
            TeamMembership.user_id == user_id,
            TeamMembership.team_id == team_id,
            Team.organization_id == organization_id,
            Team.is_active.is_(True),
        )
        .first()
        is not None
    )


def _block_domain_delegate_self_escalation(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    knowledge_base_id: UUID,
    *,
    authority: str,
    target_user_id: UUID | None = None,
    target_team_id: UUID | None = None,
) -> None:
    if authority != "domain_delegate":
        return
    targets_actor = target_user_id == current_user.id
    if target_team_id is not None:
        targets_actor = targets_actor or _actor_is_active_member_of_team(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
            team_id=target_team_id,
        )
    if not targets_actor:
        return

    record_audit(
        action="knowledge.permission_grant.blocked",
        category="security",
        actor_id=str(current_user.id),
        actor_type="user",
        target_type="knowledge_base",
        target_id=knowledge_base_id,
        metadata={
            "organization_id": str(organization_id),
            "policy_reason": "knowledge.self_escalation",
        },
    )
    raise_api_error(
        request,
        409,
        "policy.blocked",
        "Knowledge permission grant is blocked by policy.",
        {"policy_reason": "knowledge.self_escalation"},
    )


def _lock_permission_key(db: Session, namespace: str, *ids: UUID) -> None:
    """권한 natural key 단위로 pre-read와 upsert 사이의 감사 레이스를 막는다."""
    lock_key = ":".join(str(i) for i in ids)
    db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtext(namespace),
                func.hashtext(lock_key),
            )
        )
    )


def _permission_audit_columns(permission: TeamWorkflowPermission) -> dict:
    """감사 로그에 기록할 permission column 값을 dict로 추출한다."""
    return {
        attr.key: getattr(permission, attr.key, None)
        for attr in inspect(permission).mapper.column_attrs
    }


def _changed_permission_columns(before: dict, after: dict) -> tuple[dict, dict]:
    """권한 row의 변경된 column만 before/after dict로 분리한다."""
    before_changes = {}
    after_changes = {}
    for key, after_value in after.items():
        before_value = before.get(key)
        if before_value == after_value:
            continue
        before_changes[key] = before_value
        after_changes[key] = after_value
    return before_changes, after_changes


def _user_permission_audit_snapshot(
    permission,
    resource_field: str,
) -> dict:
    return {
        "grantee_organization_id": permission.grantee_organization_id,
        "user_id": permission.user_id,
        resource_field: getattr(permission, resource_field),
        "auth_state": permission.auth_state,
    }


def _lock_active_direct_permission_subject(
    db: Session,
    organization_id: UUID,
    user_id: UUID,
) -> None:
    locked = lock_access_subject_rows(
        db,
        organization_id,
        user_id,
        manager_reduction=False,
    )
    if (
        locked is None
        or locked.membership.membership_state != "active"
        or locked.user.deactivated_at is not None
    ):
        raise HTTPException(status_code=404, detail="User not found.")


def _lock_direct_permission_cleanup_subject(
    db: Session,
    organization_id: UUID,
    user_id: UUID,
) -> None:
    # Active/suspended actor targets share the exact membership/User lock order.
    # Removed legacy subjects have no actor-management surface; their child row
    # advisory lock remains the cleanup serialization boundary.
    lock_access_subject_rows(
        db,
        organization_id,
        user_id,
        manager_reduction=False,
    )


def _commit_audited_permission_mutation(
    db: Session,
    record: Callable[[], None],
) -> None:
    try:
        record()
        db.commit()
    except Exception:
        db.rollback()
        raise


def _execute_resource_permission_mutation(
    db: Session,
    current_user: User,
    *,
    organization_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID,
    grantee_type: GranteeType,
    grantee_id: UUID,
    operation: MutationOperation,
    auth_state: str | None,
    assigned_at: datetime,
) -> PermissionMutationResult:
    command = PermissionMutationCommand(
        actor_id=current_user.id,
        organization_id=organization_id,
        resource_type=resource_type,
        resource_id=resource_id,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        operation=operation,
        auth_state=auth_state,
        assigned_at=assigned_at,
    )
    return build_resource_permission_mutation_use_case(
        db,
        actor=current_user,
    ).execute(command)


def _bulk_permission_pairs(
    payload: BulkPermissionGrantRequest,
) -> list[tuple[UUID, UUID]]:
    return [
        (resource_id, grantee_id)
        for resource_id in sorted(payload.resource_ids, key=str)
        for grantee_id in sorted(payload.grantee_ids, key=str)
    ]


def _bulk_permission_response(
    payload: BulkPermissionGrantRequest,
) -> BulkPermissionGrantResponse:
    return BulkPermissionGrantResponse(
        resource_type=payload.resource_type,
        grantee_type=payload.grantee_type,
        resource_count=len(payload.resource_ids),
        grantee_count=len(payload.grantee_ids),
        grant_count=len(payload.resource_ids) * len(payload.grantee_ids),
    )


def _record_team_knowledge_permission_audit(
    db: Session,
    current_user: User,
    permission: TeamKnowledgePermission,
    before: dict | None,
    after: dict,
) -> None:
    """Core upsert가 우회한 KB team 권한 변경 감사를 직접 남긴다."""
    metadata = get_current_metadata()
    metadata["actor"] = {
        "id": str(current_user.id),
        "email": getattr(current_user, "email", None),
        "name": getattr(current_user, "name", None),
    }

    if before is None:
        action = "team_knowledge_permission.created"
        audit_before = None
        audit_after = after
    else:
        audit_before, audit_after = _changed_permission_columns(before, after)
        if not audit_before and not audit_after:
            return
        action = "team_knowledge_permission.updated"

    add_data_change_audit(
        db,
        action=action,
        actor_id=current_user.id,
        target_type="team_knowledge_permission",
        target_id=permission.id,
        before=audit_before,
        after=audit_after,
        organization_id=permission.grantee_organization_id,
        metadata=metadata,
    )


def _record_team_knowledge_permission_delete_audit(
    db: Session,
    current_user: User,
    permission: TeamKnowledgePermission,
    before: dict,
) -> None:
    """team-KB 권한 회수 감사를 직접 남긴다."""
    metadata = get_current_metadata()
    metadata["actor"] = {
        "id": str(current_user.id),
        "email": getattr(current_user, "email", None),
        "name": getattr(current_user, "name", None),
    }

    add_data_change_audit(
        db,
        action="team_knowledge_permission.deleted",
        actor_id=current_user.id,
        target_type="team_knowledge_permission",
        target_id=permission.id,
        before=before,
        after=None,
        organization_id=permission.grantee_organization_id,
        metadata=metadata,
    )


def _record_user_knowledge_permission_audit(
    db: Session,
    current_user: User,
    permission: UserKnowledgePermission,
    before: dict | None,
    after: dict,
) -> None:
    """Core upsert가 우회한 user-KB 권한 변경 감사를 직접 남긴다."""
    metadata = get_current_metadata()
    metadata["actor"] = {
        "id": str(current_user.id),
        "email": getattr(current_user, "email", None),
        "name": getattr(current_user, "name", None),
    }

    if before is None:
        action = "user_knowledge_permission.created"
        audit_before = None
        audit_after = after
    else:
        if before == after:
            return
        audit_before, audit_after = before, after
        action = "user_knowledge_permission.updated"

    add_data_change_audit(
        db,
        action=action,
        actor_id=current_user.id,
        target_type="user_knowledge_permission",
        target_id=permission.id,
        before=audit_before,
        after=audit_after,
        organization_id=permission.grantee_organization_id,
        metadata=metadata,
    )


def _record_user_knowledge_permission_delete_audit(
    db: Session,
    current_user: User,
    permission: UserKnowledgePermission,
    before: dict,
) -> None:
    """user-KB 직접 권한 회수 감사를 직접 남긴다."""
    metadata = get_current_metadata()
    metadata["actor"] = {
        "id": str(current_user.id),
        "email": getattr(current_user, "email", None),
        "name": getattr(current_user, "name", None),
    }

    add_data_change_audit(
        db,
        action="user_knowledge_permission.deleted",
        actor_id=current_user.id,
        target_type="user_knowledge_permission",
        target_id=permission.id,
        before=before,
        after=None,
        organization_id=permission.grantee_organization_id,
        metadata=metadata,
    )


def _authorize_team_workflow_permission_change(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    workflow_id: UUID,
    team_id: UUID,
) -> tuple[Organization, Workflow, Team]:
    """team workflow permission 변경 공통 scope와 manage 권한을 검증한다."""
    # 먼저 organization scope를 확정한다. scope 밖이면 리소스 존재 여부를 숨긴다.
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    is_organization_manager = has_organization_manager_permission(
        db,
        current_user.id,
        organization_id,
    )
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    # 대상 workflow는 요청 organization 안에 있어야 한다.
    workflow = (
        db.query(Workflow)
        .filter(
            Workflow.id == workflow_id,
            Workflow.organization_id == organization_id,
        )
        .first()
    )
    if workflow is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Workflow not found.",
        )

    # inactive team에는 workflow 권한을 부여하거나 회수하지 않는다.
    team = (
        db.query(Team)
        .filter(
            Team.id == team_id,
            Team.organization_id == organization_id,
            Team.is_active.is_(True),
        )
        .first()
    )
    if team is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Team not found.",
        )

    # 최종 허용 주체는 organization manager 또는 workflow manager다.
    if (
        not is_organization_manager
        and not PermissionEnforcementService.has_workflow_manage_permission(
            db,
            organization_id,
            workflow_id,
            current_user.id,
        )
    ):
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Workflow manage or organization manager permission is required.",
        )

    return organization, workflow, team


def _authorize_team_llm_permission_change(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    credential_id: UUID,
    team_id: UUID,
) -> tuple[Organization, LLMCredential, Team]:
    """team LLM credential permission 변경 공통 scope와 manage 권한을 검증한다."""
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    is_organization_manager = has_organization_manager_permission(
        db,
        current_user.id,
        organization_id,
    )
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    credential = (
        db.query(LLMCredential)
        .filter(
            LLMCredential.id == credential_id,
            LLMCredential.organization_id == organization_id,
            LLMCredential.is_valid.is_(True),
        )
        .first()
    )
    if credential is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "LLM credential not found.",
        )

    team = (
        db.query(Team)
        .filter(
            Team.id == team_id,
            Team.organization_id == organization_id,
            Team.is_active.is_(True),
        )
        .first()
    )
    if team is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Team not found.",
        )

    if (
        not is_organization_manager
        and not PermissionEnforcementService.has_llm_credential_manage_permission(
            db,
            organization_id,
            credential_id,
            current_user.id,
        )
    ):
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Credential manage or organization manager permission is required.",
        )

    return organization, credential, team


def _authorize_team_knowledge_permission_change(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    knowledge_base_id: UUID,
    team_id: UUID,
) -> tuple[Organization, KnowledgeBase, Team, str]:
    """team KB permission 변경 공통 scope와 manage 권한을 검증한다."""
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    is_organization_manager = has_organization_manager_permission(
        db,
        current_user.id,
        organization_id,
    )
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    knowledge_base = (
        db.query(KnowledgeBase)
        .filter(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.organization_id == organization_id,
            KnowledgeBase.lifecycle_state == "active",
        )
        .first()
    )
    if (
        knowledge_base is None
        or getattr(knowledge_base, "lifecycle_state", "active") != "active"
    ):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Knowledge Base not found.",
        )

    team = (
        db.query(Team)
        .filter(
            Team.id == team_id,
            Team.organization_id == organization_id,
            Team.is_active.is_(True),
        )
        .first()
    )
    if team is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Team not found.",
        )

    authority = _knowledge_permission_change_authority(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        is_organization_manager=is_organization_manager,
    )
    if authority is None:
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Knowledge permission delegation is required.",
        )

    return organization, knowledge_base, team, authority


def _authorize_user_workflow_permission_change(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    workflow_id: UUID,
    user_id: UUID,
    *,
    require_active_target: bool = True,
) -> tuple[Organization, Workflow, User | None]:
    """user workflow permission 변경 공통 scope와 manage 권한을 검증한다."""
    # 먼저 organization scope를 확정한다. scope 밖이면 리소스 존재 여부를 숨긴다.
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    is_organization_manager = has_organization_manager_permission(
        db,
        current_user.id,
        organization_id,
    )
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    # 대상 workflow는 요청 organization 안에 있어야 한다.
    workflow = (
        db.query(Workflow)
        .filter(
            Workflow.id == workflow_id,
            Workflow.organization_id == organization_id,
        )
        .first()
    )
    if workflow is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Workflow not found.",
        )

    target_user = None
    if require_active_target:
        # 비활성화된 user에는 direct workflow permission을 새로 부여하지 않는다.
        target_user = (
            db.query(User)
            .filter(
                User.id == user_id,
                User.deactivated_at.is_(None),
            )
            .first()
        )
        if target_user is None:
            raise_api_error(
                request,
                404,
                "resource.not_found",
                "User not found.",
            )

        # 대상 user는 실제 active organization membership이 있어야 direct grant 대상이 된다.
        if not _has_active_membership(
            db,
            organization_id,
            user_id,
        ):
            raise_api_error(
                request,
                404,
                "resource.not_found",
                "User not found.",
            )

    # 최종 허용 주체는 organization manager 또는 workflow manager다.
    if (
        not is_organization_manager
        and not PermissionEnforcementService.has_workflow_manage_permission(
            db,
            organization_id,
            workflow_id,
            current_user.id,
        )
    ):
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Workflow manage or organization manager permission is required.",
        )

    return organization, workflow, target_user


def _authorize_user_llm_permission_change(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    credential_id: UUID,
    user_id: UUID,
    *,
    require_active_target: bool = True,
) -> tuple[Organization, LLMCredential, User | None]:
    """user LLM credential permission 변경 공통 scope와 manage 권한을 검증한다."""
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    is_organization_manager = has_organization_manager_permission(
        db,
        current_user.id,
        organization_id,
    )
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    credential = (
        db.query(LLMCredential)
        .filter(
            LLMCredential.id == credential_id,
            LLMCredential.organization_id == organization_id,
            LLMCredential.is_valid.is_(True),
        )
        .first()
    )
    if credential is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "LLM credential not found.",
        )

    target_user = None
    if require_active_target:
        target_user = (
            db.query(User)
            .filter(
                User.id == user_id,
                User.deactivated_at.is_(None),
            )
            .first()
        )
        if target_user is None:
            raise_api_error(
                request,
                404,
                "resource.not_found",
                "User not found.",
            )

        if not _has_active_membership(
            db,
            organization_id,
            user_id,
        ):
            raise_api_error(
                request,
                404,
                "resource.not_found",
                "User not found.",
            )

    if (
        not is_organization_manager
        and not PermissionEnforcementService.has_llm_credential_manage_permission(
            db,
            organization_id,
            credential_id,
            current_user.id,
        )
    ):
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Credential manage or organization manager permission is required.",
        )

    return organization, credential, target_user


def _authorize_user_knowledge_permission_change(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    knowledge_base_id: UUID,
    user_id: UUID,
    *,
    require_active_target: bool = True,
) -> tuple[Organization, KnowledgeBase, User | None, str]:
    """user KB permission 변경 공통 scope와 manage 권한을 검증한다."""
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    is_organization_manager = has_organization_manager_permission(
        db,
        current_user.id,
        organization_id,
    )
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    knowledge_base = (
        db.query(KnowledgeBase)
        .filter(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.organization_id == organization_id,
            KnowledgeBase.lifecycle_state == "active",
        )
        .first()
    )
    if (
        knowledge_base is None
        or getattr(knowledge_base, "lifecycle_state", "active") != "active"
    ):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Knowledge Base not found.",
        )

    target_user = None
    if require_active_target:
        target_user = (
            db.query(User)
            .filter(
                User.id == user_id,
                User.deactivated_at.is_(None),
            )
            .first()
        )
        if target_user is None:
            raise_api_error(
                request,
                404,
                "resource.not_found",
                "User not found.",
            )

        if not _has_active_membership(
            db,
            organization_id,
            user_id,
        ):
            raise_api_error(
                request,
                404,
                "resource.not_found",
                "User not found.",
            )

    authority = _knowledge_permission_change_authority(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        is_organization_manager=is_organization_manager,
    )
    if authority is None:
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Knowledge permission delegation is required.",
        )

    return organization, knowledge_base, target_user, authority


def _authorize_workflow_permission_read(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    workflow_id: UUID,
) -> tuple[Organization, Workflow]:
    """workflow permission 목록 조회 scope와 manage 권한을 검증한다."""
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    is_organization_manager = has_organization_manager_permission(
        db,
        current_user.id,
        organization_id,
    )
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    workflow = (
        db.query(Workflow)
        .filter(
            Workflow.id == workflow_id,
            Workflow.organization_id == organization_id,
        )
        .first()
    )
    if workflow is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Workflow not found.",
        )

    if (
        not is_organization_manager
        and not PermissionEnforcementService.has_workflow_manage_permission(
            db,
            organization_id,
            workflow_id,
            current_user.id,
        )
    ):
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Workflow manage or organization manager permission is required.",
        )

    return organization, workflow


def _authorize_llm_permission_read(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    credential_id: UUID,
) -> tuple[Organization, LLMCredential]:
    """LLM credential permission 목록 조회 scope와 manage 권한을 검증한다."""
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    is_organization_manager = has_organization_manager_permission(
        db,
        current_user.id,
        organization_id,
    )
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    credential = (
        db.query(LLMCredential)
        .filter(
            LLMCredential.id == credential_id,
            LLMCredential.organization_id == organization_id,
            LLMCredential.is_valid.is_(True),
        )
        .first()
    )
    if credential is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "LLM credential not found.",
        )

    if (
        not is_organization_manager
        and not PermissionEnforcementService.has_llm_credential_manage_permission(
            db,
            organization_id,
            credential_id,
            current_user.id,
        )
    ):
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Credential manage or organization manager permission is required.",
        )

    return organization, credential


def _authorize_knowledge_permission_read(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    knowledge_base_id: UUID,
) -> tuple[Organization, KnowledgeBase]:
    """KB permission 목록 조회 scope와 manage 권한을 검증한다."""
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if organization is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    is_organization_manager = has_organization_manager_permission(
        db,
        current_user.id,
        organization_id,
    )
    if not has_organization_scope_access(db, current_user.id, organization_id):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    knowledge_base = (
        db.query(KnowledgeBase)
        .filter(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.organization_id == organization_id,
            KnowledgeBase.lifecycle_state == "active",
        )
        .first()
    )
    if (
        knowledge_base is None
        or getattr(knowledge_base, "lifecycle_state", "active") != "active"
    ):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Knowledge Base not found.",
        )

    authority = _knowledge_permission_change_authority(
        db,
        user_id=current_user.id,
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        is_organization_manager=is_organization_manager,
    )
    if authority is None:
        raise_api_error(
            request,
            403,
            "permission.denied",
            "Knowledge permission delegation is required.",
        )

    return organization, knowledge_base


def _upsert_team_workflow_permission(
    db: Session,
    current_user: User,
    organization_id: UUID,
    workflow_id: UUID,
    team_id: UUID,
    auth_state: str,
    assigned_at: datetime,
) -> dict[str, object]:
    """team-workflow 권한을 원자적으로 생성/수정하고 감사 로그를 남긴다."""
    result = _execute_resource_permission_mutation(
        db,
        current_user,
        organization_id=organization_id,
        resource_type="workflow",
        resource_id=workflow_id,
        grantee_type="team",
        grantee_id=team_id,
        operation="upsert",
        auth_state=auth_state,
        assigned_at=assigned_at,
    )
    return result.permission.response_payload()


def _upsert_user_workflow_permission(
    db: Session,
    current_user: User,
    organization_id: UUID,
    workflow_id: UUID,
    user_id: UUID,
    auth_state: str,
    assigned_at: datetime,
) -> dict[str, object]:
    """user-workflow 권한을 원자적으로 생성/수정하고 감사 로그를 남긴다."""
    result = _execute_resource_permission_mutation(
        db,
        current_user,
        organization_id=organization_id,
        resource_type="workflow",
        resource_id=workflow_id,
        grantee_type="user",
        grantee_id=user_id,
        operation="upsert",
        auth_state=auth_state,
        assigned_at=assigned_at,
    )
    return result.permission.response_payload()


def _upsert_team_knowledge_permission(
    db: Session,
    current_user: User,
    organization_id: UUID,
    knowledge_base_id: UUID,
    team_id: UUID,
    auth_state: str,
    assigned_by: UUID,
    assigned_at: datetime,
    *,
    commit: bool = True,
) -> TeamKnowledgePermission:
    """team-KB 권한을 원자적으로 생성/수정하고 감사 로그를 남긴다."""
    _lock_permission_key(
        db, "team_knowledge_permission", organization_id, knowledge_base_id, team_id
    )
    existing_permission = (
        db.query(TeamKnowledgePermission)
        .filter(
            TeamKnowledgePermission.grantee_organization_id == organization_id,
            TeamKnowledgePermission.knowledge_base_id == knowledge_base_id,
            TeamKnowledgePermission.team_id == team_id,
        )
        .first()
    )
    before = (
        _permission_audit_columns(existing_permission)
        if existing_permission is not None
        else None
    )

    insert_stmt = pg_insert(TeamKnowledgePermission).values(
        grantee_organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        team_id=team_id,
        auth_state=auth_state,
        assigned_by=assigned_by,
        assigned_at=assigned_at,
        options={},
        flags=0,
    )
    upsert_stmt = (
        insert_stmt.on_conflict_do_update(
            index_elements=[
                TeamKnowledgePermission.grantee_organization_id,
                TeamKnowledgePermission.knowledge_base_id,
                TeamKnowledgePermission.team_id,
            ],
            set_={
                "auth_state": insert_stmt.excluded.auth_state,
                "assigned_by": insert_stmt.excluded.assigned_by,
                "assigned_at": insert_stmt.excluded.assigned_at,
            },
            where=TeamKnowledgePermission.auth_state != insert_stmt.excluded.auth_state,
        )
        .returning(TeamKnowledgePermission)
        .execution_options(populate_existing=True)
    )

    permission = db.scalars(upsert_stmt).one_or_none()
    if permission is None:
        permission = existing_permission
    after = _permission_audit_columns(permission)

    def record_permission_audit() -> None:
        _record_team_knowledge_permission_audit(
            db,
            current_user,
            permission,
            before,
            after,
        )

    if commit:
        _commit_audited_permission_mutation(db, record_permission_audit)
    else:
        record_permission_audit()
        db.flush()
    return permission


def _upsert_team_llm_permission(
    db: Session,
    current_user: User,
    organization_id: UUID,
    credential_id: UUID,
    team_id: UUID,
    auth_state: str,
    assigned_at: datetime,
) -> dict[str, object]:
    """team-LLM credential 권한을 원자적으로 생성/수정하고 감사 로그를 남긴다."""
    result = _execute_resource_permission_mutation(
        db,
        current_user,
        organization_id=organization_id,
        resource_type="llm_credential",
        resource_id=credential_id,
        grantee_type="team",
        grantee_id=team_id,
        operation="upsert",
        auth_state=auth_state,
        assigned_at=assigned_at,
    )
    return result.permission.response_payload()


def _upsert_user_llm_permission(
    db: Session,
    current_user: User,
    organization_id: UUID,
    credential_id: UUID,
    user_id: UUID,
    auth_state: str,
    assigned_at: datetime,
) -> dict[str, object]:
    """user-LLM credential 권한을 원자적으로 생성/수정하고 감사 로그를 남긴다."""
    result = _execute_resource_permission_mutation(
        db,
        current_user,
        organization_id=organization_id,
        resource_type="llm_credential",
        resource_id=credential_id,
        grantee_type="user",
        grantee_id=user_id,
        operation="upsert",
        auth_state=auth_state,
        assigned_at=assigned_at,
    )
    return result.permission.response_payload()


def _upsert_user_knowledge_permission(
    db: Session,
    current_user: User,
    organization_id: UUID,
    knowledge_base_id: UUID,
    user_id: UUID,
    auth_state: str,
    assigned_by: UUID,
    assigned_at: datetime,
    *,
    commit: bool = True,
    lock_subject: bool = True,
) -> UserKnowledgePermission:
    """user-KB 직접 권한을 원자적으로 생성/수정하고 감사 로그를 남긴다."""
    if lock_subject:
        _lock_active_direct_permission_subject(db, organization_id, user_id)
    _lock_permission_key(
        db, "user_knowledge_permission", organization_id, knowledge_base_id, user_id
    )
    existing_permission = (
        db.query(UserKnowledgePermission)
        .filter(
            UserKnowledgePermission.grantee_organization_id == organization_id,
            UserKnowledgePermission.knowledge_base_id == knowledge_base_id,
            UserKnowledgePermission.user_id == user_id,
        )
        .first()
    )
    before = (
        _user_permission_audit_snapshot(existing_permission, "knowledge_base_id")
        if existing_permission is not None
        else None
    )

    insert_stmt = pg_insert(UserKnowledgePermission).values(
        grantee_organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        user_id=user_id,
        auth_state=auth_state,
        assigned_by=assigned_by,
        assigned_at=assigned_at,
        options={},
        flags=0,
    )
    upsert_stmt = (
        insert_stmt.on_conflict_do_update(
            index_elements=[
                UserKnowledgePermission.grantee_organization_id,
                UserKnowledgePermission.user_id,
                UserKnowledgePermission.knowledge_base_id,
            ],
            set_={
                "auth_state": insert_stmt.excluded.auth_state,
                "assigned_by": insert_stmt.excluded.assigned_by,
                "assigned_at": insert_stmt.excluded.assigned_at,
            },
            where=UserKnowledgePermission.auth_state != insert_stmt.excluded.auth_state,
        )
        .returning(UserKnowledgePermission)
        .execution_options(populate_existing=True)
    )

    permission = db.scalars(upsert_stmt).one_or_none()
    if permission is None:
        permission = existing_permission
    after = _user_permission_audit_snapshot(permission, "knowledge_base_id")

    def record_permission_audit() -> None:
        _record_user_knowledge_permission_audit(
            db,
            current_user,
            permission,
            before,
            after,
        )

    if commit:
        _commit_audited_permission_mutation(db, record_permission_audit)
    else:
        record_permission_audit()
        db.flush()
    return permission


@router.get(
    "/workflows/{workflow_id}",
    response_model=ResourcePermissionListResponse,
)
def list_workflow_permissions(
    workflow_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """workflow에 부여된 team/user 권한 목록을 조회한다."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _authorize_workflow_permission_read(
        request,
        db,
        current_user,
        organization_id,
        workflow_id,
    )

    team_permissions = (
        db.query(TeamWorkflowPermission)
        .options(joinedload(TeamWorkflowPermission.team))
        .join(Team, Team.id == TeamWorkflowPermission.team_id)
        .filter(
            TeamWorkflowPermission.grantee_organization_id == organization_id,
            TeamWorkflowPermission.workflow_id == workflow_id,
            Team.organization_id == organization_id,
            Team.is_active.is_(True),
        )
        .order_by(Team.name.asc(), TeamWorkflowPermission.id.asc())
        .all()
    )
    user_permissions = (
        db.query(UserWorkflowPermission)
        .options(joinedload(UserWorkflowPermission.user))
        .join(User, User.id == UserWorkflowPermission.user_id)
        .filter(
            UserWorkflowPermission.grantee_organization_id == organization_id,
            UserWorkflowPermission.workflow_id == workflow_id,
            User.deactivated_at.is_(None),
        )
        .order_by(User.name.asc(), User.email.asc(), UserWorkflowPermission.id.asc())
        .all()
    )

    return {
        "resource_type": "workflow",
        "resource_id": workflow_id,
        "organization_id": organization_id,
        "team_permissions": [
            {
                "id": permission.id,
                "grantee_type": "team",
                "grantee_id": permission.team_id,
                "grantee_name": permission.team.name,
                "auth_state": permission.auth_state,
                "assigned_at": permission.assigned_at,
            }
            for permission in team_permissions
        ],
        "user_permissions": [
            {
                "id": permission.id,
                "grantee_type": "user",
                "grantee_id": permission.user_id,
                "grantee_name": permission.user.name,
                "auth_state": permission.auth_state,
                "assigned_at": permission.assigned_at,
            }
            for permission in user_permissions
        ],
    }


@router.get(
    "/knowledge-bases/{knowledge_base_id}",
    response_model=ResourcePermissionListResponse,
)
def list_knowledge_base_permissions(
    knowledge_base_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """KB에 부여된 team/user 권한 목록을 조회한다."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _authorize_knowledge_permission_read(
        request,
        db,
        current_user,
        organization_id,
        knowledge_base_id,
    )

    team_permissions = (
        db.query(TeamKnowledgePermission)
        .options(joinedload(TeamKnowledgePermission.team))
        .join(Team, Team.id == TeamKnowledgePermission.team_id)
        .filter(
            TeamKnowledgePermission.grantee_organization_id == organization_id,
            TeamKnowledgePermission.knowledge_base_id == knowledge_base_id,
            Team.organization_id == organization_id,
            Team.is_active.is_(True),
        )
        .order_by(Team.name.asc(), TeamKnowledgePermission.id.asc())
        .all()
    )
    user_permissions = (
        db.query(UserKnowledgePermission)
        .options(joinedload(UserKnowledgePermission.user))
        .join(User, User.id == UserKnowledgePermission.user_id)
        .filter(
            UserKnowledgePermission.grantee_organization_id == organization_id,
            UserKnowledgePermission.knowledge_base_id == knowledge_base_id,
            User.deactivated_at.is_(None),
        )
        .order_by(User.name.asc(), User.email.asc(), UserKnowledgePermission.id.asc())
        .all()
    )

    return {
        "resource_type": "knowledge_base",
        "resource_id": knowledge_base_id,
        "organization_id": organization_id,
        "team_permissions": [
            {
                "id": permission.id,
                "grantee_type": "team",
                "grantee_id": permission.team_id,
                "grantee_name": permission.team.name,
                "auth_state": permission.auth_state,
                "assigned_at": permission.assigned_at,
            }
            for permission in team_permissions
        ],
        "user_permissions": [
            {
                "id": permission.id,
                "grantee_type": "user",
                "grantee_id": permission.user_id,
                "grantee_name": permission.user.name or permission.user.email,
                "auth_state": permission.auth_state,
                "assigned_at": permission.assigned_at,
            }
            for permission in user_permissions
        ],
    }


@router.get(
    "/llm-credentials/{credential_id}",
    response_model=ResourcePermissionListResponse,
)
def list_llm_credential_permissions(
    credential_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """LLM credential에 부여된 team/user 권한 목록을 조회한다."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _authorize_llm_permission_read(
        request,
        db,
        current_user,
        organization_id,
        credential_id,
    )

    team_permissions = (
        db.query(TeamLLMPermission)
        .options(joinedload(TeamLLMPermission.team))
        .join(Team, Team.id == TeamLLMPermission.team_id)
        .filter(
            TeamLLMPermission.grantee_organization_id == organization_id,
            TeamLLMPermission.llm_credential_id == credential_id,
            Team.organization_id == organization_id,
            Team.is_active.is_(True),
        )
        .order_by(Team.name.asc(), TeamLLMPermission.id.asc())
        .all()
    )
    user_permissions = (
        db.query(UserLLMPermission)
        .options(joinedload(UserLLMPermission.user))
        .join(User, User.id == UserLLMPermission.user_id)
        .filter(
            UserLLMPermission.grantee_organization_id == organization_id,
            UserLLMPermission.llm_credential_id == credential_id,
            User.deactivated_at.is_(None),
        )
        .order_by(User.name.asc(), User.email.asc(), UserLLMPermission.id.asc())
        .all()
    )

    return {
        "resource_type": "llm_credential",
        "resource_id": credential_id,
        "organization_id": organization_id,
        "team_permissions": [
            {
                "id": permission.id,
                "grantee_type": "team",
                "grantee_id": permission.team_id,
                "grantee_name": permission.team.name,
                "auth_state": permission.auth_state,
                "assigned_at": permission.assigned_at,
            }
            for permission in team_permissions
        ],
        "user_permissions": [
            {
                "id": permission.id,
                "grantee_type": "user",
                "grantee_id": permission.user_id,
                "grantee_name": permission.user.name,
                "auth_state": permission.auth_state,
                "assigned_at": permission.assigned_at,
            }
            for permission in user_permissions
        ],
    }


def _grant_bulk_workflow_permissions(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    payload: BulkPermissionGrantRequest,
) -> None:
    pairs = _bulk_permission_pairs(payload)
    workflows: dict[UUID, Workflow] = {}
    try:
        for workflow_id, grantee_id in pairs:
            if payload.grantee_type == "team":
                _, workflow, _ = _authorize_team_workflow_permission_change(
                    request,
                    db,
                    current_user,
                    organization_id,
                    workflow_id,
                    grantee_id,
                )
            else:
                _, workflow, _ = _authorize_user_workflow_permission_change(
                    request,
                    db,
                    current_user,
                    organization_id,
                    workflow_id,
                    grantee_id,
                )
            workflows[workflow_id] = workflow

        if payload.grantee_type == "user":
            for user_id in sorted(payload.grantee_ids, key=str):
                _lock_active_direct_permission_subject(db, organization_id, user_id)
        for workflow_id in sorted(payload.resource_ids, key=str):
            _lock_workflow_mutation_app_scope(
                request,
                db,
                organization_id=organization_id,
                workflow=workflows[workflow_id],
            )

        assigned_at = datetime.now(timezone.utc)
        commands = [
            PermissionMutationCommand(
                actor_id=current_user.id,
                organization_id=organization_id,
                resource_type="workflow",
                resource_id=workflow_id,
                grantee_type=payload.grantee_type,
                grantee_id=grantee_id,
                operation="upsert",
                auth_state=payload.auth_state,
                assigned_at=assigned_at,
            )
            for workflow_id, grantee_id in pairs
        ]
        build_resource_permission_mutation_use_case(
            db,
            actor=current_user,
        ).execute_many(commands)
    except Exception:
        db.rollback()
        raise


def _grant_bulk_llm_permissions(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    payload: BulkPermissionGrantRequest,
) -> None:
    pairs = _bulk_permission_pairs(payload)
    try:
        for credential_id, grantee_id in pairs:
            if payload.grantee_type == "team":
                _authorize_team_llm_permission_change(
                    request,
                    db,
                    current_user,
                    organization_id,
                    credential_id,
                    grantee_id,
                )
            else:
                _authorize_user_llm_permission_change(
                    request,
                    db,
                    current_user,
                    organization_id,
                    credential_id,
                    grantee_id,
                )

        if payload.grantee_type == "user":
            for user_id in sorted(payload.grantee_ids, key=str):
                _lock_active_direct_permission_subject(db, organization_id, user_id)

        assigned_at = datetime.now(timezone.utc)
        commands = [
            PermissionMutationCommand(
                actor_id=current_user.id,
                organization_id=organization_id,
                resource_type="llm_credential",
                resource_id=credential_id,
                grantee_type=payload.grantee_type,
                grantee_id=grantee_id,
                operation="upsert",
                auth_state=payload.auth_state,
                assigned_at=assigned_at,
            )
            for credential_id, grantee_id in pairs
        ]
        build_resource_permission_mutation_use_case(
            db,
            actor=current_user,
        ).execute_many(commands)
    except Exception:
        db.rollback()
        raise


def _grant_bulk_knowledge_permissions(
    request: Request,
    db: Session,
    current_user: User,
    organization_id: UUID,
    payload: BulkPermissionGrantRequest,
) -> None:
    pairs = _bulk_permission_pairs(payload)
    try:
        for knowledge_base_id, grantee_id in pairs:
            if payload.grantee_type == "team":
                _, _, _, authority = _authorize_team_knowledge_permission_change(
                    request,
                    db,
                    current_user,
                    organization_id,
                    knowledge_base_id,
                    grantee_id,
                )
                _block_domain_delegate_self_escalation(
                    request,
                    db,
                    current_user,
                    organization_id,
                    knowledge_base_id,
                    authority=authority,
                    target_team_id=grantee_id,
                )
            else:
                _, _, _, authority = _authorize_user_knowledge_permission_change(
                    request,
                    db,
                    current_user,
                    organization_id,
                    knowledge_base_id,
                    grantee_id,
                )
                _block_domain_delegate_self_escalation(
                    request,
                    db,
                    current_user,
                    organization_id,
                    knowledge_base_id,
                    authority=authority,
                    target_user_id=grantee_id,
                )

        if payload.grantee_type == "user":
            for user_id in sorted(payload.grantee_ids, key=str):
                _lock_active_direct_permission_subject(db, organization_id, user_id)

        assigned_at = datetime.now(timezone.utc)
        for knowledge_base_id, grantee_id in pairs:
            if payload.grantee_type == "team":
                _upsert_team_knowledge_permission(
                    db,
                    current_user,
                    organization_id,
                    knowledge_base_id,
                    grantee_id,
                    payload.auth_state,
                    current_user.id,
                    assigned_at,
                    commit=False,
                )
            else:
                _upsert_user_knowledge_permission(
                    db,
                    current_user,
                    organization_id,
                    knowledge_base_id,
                    grantee_id,
                    payload.auth_state,
                    current_user.id,
                    assigned_at,
                    commit=False,
                    lock_subject=False,
                )
        db.commit()
    except Exception:
        db.rollback()
        raise


@router.post("/bulk-grants", response_model=BulkPermissionGrantResponse)
def grant_permissions_bulk(
    payload: BulkPermissionGrantRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)

    if payload.resource_type == "workflow":
        _grant_bulk_workflow_permissions(
            request, db, current_user, organization_id, payload
        )
    elif payload.resource_type == "knowledge_base":
        _grant_bulk_knowledge_permissions(
            request, db, current_user, organization_id, payload
        )
    elif payload.resource_type == "llm_credential":
        _grant_bulk_llm_permissions(
            request, db, current_user, organization_id, payload
        )
    else:
        try:
            MailCredentialService(db).grant_permissions_bulk(
                current_user.id,
                organization_id,
                payload.resource_ids,
                payload.grantee_type,
                payload.grantee_ids,
                MailCredentialPermissionGrant(auth_state=payload.auth_state),
            )
        except MailCredentialServiceError as exc:
            db.rollback()
            raise_api_error(request, exc.status_code, exc.code, exc.detail)

    return _bulk_permission_response(payload)


@router.put(
    "/workflows/{workflow_id}/teams/{team_id}",
    response_model=TeamWorkflowPermissionResponse,
)
def put_team_workflow_permission(
    workflow_id: UUID,
    team_id: UUID,
    payload: PermissionGrantRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """team에 workflow 권한을 부여하거나 갱신하는 PUT endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _, workflow, _ = _authorize_team_workflow_permission_change(
        request,
        db,
        current_user,
        organization_id,
        workflow_id,
        team_id,
    )
    _lock_workflow_mutation_app_scope(
        request,
        db,
        organization_id=organization_id,
        workflow=workflow,
    )

    now = datetime.now(timezone.utc)
    return _upsert_team_workflow_permission(
        db,
        current_user,
        organization_id,
        workflow_id,
        team_id,
        payload.auth_state,
        now,
    )


@router.put(
    "/knowledge-bases/{knowledge_base_id}/teams/{team_id}",
    response_model=TeamKnowledgePermissionResponse,
)
def put_team_knowledge_permission(
    knowledge_base_id: UUID,
    team_id: UUID,
    payload: KnowledgeDirectPermissionGrantRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """team에 KB 권한을 부여하거나 갱신하는 PUT endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _, _, _, authority = _authorize_team_knowledge_permission_change(
        request,
        db,
        current_user,
        organization_id,
        knowledge_base_id,
        team_id,
    )
    _block_domain_delegate_self_escalation(
        request,
        db,
        current_user,
        organization_id,
        knowledge_base_id,
        authority=authority,
        target_team_id=team_id,
    )

    now = datetime.now(timezone.utc)
    return _upsert_team_knowledge_permission(
        db,
        current_user,
        organization_id,
        knowledge_base_id,
        team_id,
        payload.auth_state,
        current_user.id,
        now,
    )


@router.put(
    "/llm-credentials/{credential_id}/teams/{team_id}",
    response_model=TeamLLMPermissionResponse,
)
def put_team_llm_permission(
    credential_id: UUID,
    team_id: UUID,
    payload: LLMPermissionGrantRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """team에 LLM credential 권한을 부여하거나 갱신하는 PUT endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _authorize_team_llm_permission_change(
        request,
        db,
        current_user,
        organization_id,
        credential_id,
        team_id,
    )

    now = datetime.now(timezone.utc)
    return _upsert_team_llm_permission(
        db,
        current_user,
        organization_id,
        credential_id,
        team_id,
        payload.auth_state,
        now,
    )


@router.put(
    "/workflows/{workflow_id}/users/{user_id}",
    response_model=UserWorkflowPermissionResponse,
)
def put_user_workflow_permission(
    workflow_id: UUID,
    user_id: UUID,
    payload: PermissionGrantRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """user에 workflow 직접 권한을 부여하거나 갱신하는 PUT endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    # scope, 대상 user 상태, 부여자 권한을 모두 통과해야 permission row를 만든다.
    _, workflow, _ = _authorize_user_workflow_permission_change(
        request,
        db,
        current_user,
        organization_id,
        workflow_id,
        user_id,
    )
    _lock_active_direct_permission_subject(db, organization_id, user_id)
    _lock_workflow_mutation_app_scope(
        request,
        db,
        organization_id=organization_id,
        workflow=workflow,
    )

    now = datetime.now(timezone.utc)
    return _upsert_user_workflow_permission(
        db,
        current_user,
        organization_id,
        workflow_id,
        user_id,
        payload.auth_state,
        now,
    )


@router.put(
    "/knowledge-bases/{knowledge_base_id}/users/{user_id}",
    response_model=UserKnowledgePermissionResponse,
)
def put_user_knowledge_permission(
    knowledge_base_id: UUID,
    user_id: UUID,
    payload: KnowledgeDirectPermissionGrantRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """user에 KB 직접 권한을 부여하거나 갱신하는 PUT endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _, _, _, authority = _authorize_user_knowledge_permission_change(
        request,
        db,
        current_user,
        organization_id,
        knowledge_base_id,
        user_id,
    )
    _block_domain_delegate_self_escalation(
        request,
        db,
        current_user,
        organization_id,
        knowledge_base_id,
        authority=authority,
        target_user_id=user_id,
    )

    now = datetime.now(timezone.utc)
    return _upsert_user_knowledge_permission(
        db,
        current_user,
        organization_id,
        knowledge_base_id,
        user_id,
        payload.auth_state,
        current_user.id,
        now,
    )


@router.put(
    "/llm-credentials/{credential_id}/users/{user_id}",
    response_model=UserLLMPermissionResponse,
)
def put_user_llm_permission(
    credential_id: UUID,
    user_id: UUID,
    payload: LLMPermissionGrantRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """user에 LLM credential 직접 권한을 부여하거나 갱신하는 PUT endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _authorize_user_llm_permission_change(
        request,
        db,
        current_user,
        organization_id,
        credential_id,
        user_id,
    )
    _lock_active_direct_permission_subject(db, organization_id, user_id)

    now = datetime.now(timezone.utc)
    return _upsert_user_llm_permission(
        db,
        current_user,
        organization_id,
        credential_id,
        user_id,
        payload.auth_state,
        now,
    )


@router.delete("/workflows/{workflow_id}/teams/{team_id}")
def delete_team_workflow_permission(
    workflow_id: UUID,
    team_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """team의 workflow 권한을 회수하는 DELETE endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _, workflow, _ = _authorize_team_workflow_permission_change(
        request,
        db,
        current_user,
        organization_id,
        workflow_id,
        team_id,
    )
    _lock_workflow_mutation_app_scope(
        request,
        db,
        organization_id=organization_id,
        workflow=workflow,
    )

    try:
        result = _execute_resource_permission_mutation(
            db,
            current_user,
            organization_id=organization_id,
            resource_type="workflow",
            resource_id=workflow_id,
            grantee_type="team",
            grantee_id=team_id,
            operation="delete",
            auth_state=None,
            assigned_at=datetime.now(timezone.utc),
        )
    except PermissionMutationNotFound:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Team workflow permission not found.",
        )
    return {
        "message": "Team workflow permission deleted",
        "id": str(result.permission.permission_id),
    }


@router.delete("/knowledge-bases/{knowledge_base_id}/teams/{team_id}")
def delete_team_knowledge_permission(
    knowledge_base_id: UUID,
    team_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """team의 KB 권한을 회수하는 DELETE endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _authorize_team_knowledge_permission_change(
        request,
        db,
        current_user,
        organization_id,
        knowledge_base_id,
        team_id,
    )

    _lock_permission_key(
        db, "team_knowledge_permission", organization_id, knowledge_base_id, team_id
    )
    permission = (
        db.query(TeamKnowledgePermission)
        .filter(
            TeamKnowledgePermission.grantee_organization_id == organization_id,
            TeamKnowledgePermission.knowledge_base_id == knowledge_base_id,
            TeamKnowledgePermission.team_id == team_id,
        )
        .first()
    )
    if permission is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Team knowledge permission not found.",
        )

    permission_id = permission.id
    before = _permission_audit_columns(permission)
    (
        db.query(TeamKnowledgePermission)
        .filter(
            TeamKnowledgePermission.grantee_organization_id == organization_id,
            TeamKnowledgePermission.knowledge_base_id == knowledge_base_id,
            TeamKnowledgePermission.team_id == team_id,
        )
        .delete(synchronize_session=False)
    )
    _record_team_knowledge_permission_delete_audit(
        db,
        current_user,
        permission,
        before,
    )
    db.commit()
    return {"message": "Team knowledge permission deleted", "id": str(permission_id)}


@router.delete("/llm-credentials/{credential_id}/teams/{team_id}")
def delete_team_llm_permission(
    credential_id: UUID,
    team_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """team의 LLM credential 권한을 회수하는 DELETE endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _authorize_team_llm_permission_change(
        request,
        db,
        current_user,
        organization_id,
        credential_id,
        team_id,
    )

    try:
        result = _execute_resource_permission_mutation(
            db,
            current_user,
            organization_id=organization_id,
            resource_type="llm_credential",
            resource_id=credential_id,
            grantee_type="team",
            grantee_id=team_id,
            operation="delete",
            auth_state=None,
            assigned_at=datetime.now(timezone.utc),
        )
    except PermissionMutationNotFound:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Team LLM credential permission not found.",
        )
    return {
        "message": "Team LLM credential permission deleted",
        "id": str(result.permission.permission_id),
    }


@router.delete("/workflows/{workflow_id}/users/{user_id}")
def delete_user_workflow_permission(
    workflow_id: UUID,
    user_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """user의 workflow 직접 권한을 회수하는 DELETE endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _, workflow, _ = _authorize_user_workflow_permission_change(
        request,
        db,
        current_user,
        organization_id,
        workflow_id,
        user_id,
        require_active_target=False,
    )
    _lock_direct_permission_cleanup_subject(db, organization_id, user_id)
    _lock_workflow_mutation_app_scope(
        request,
        db,
        organization_id=organization_id,
        workflow=workflow,
    )

    try:
        result = _execute_resource_permission_mutation(
            db,
            current_user,
            organization_id=organization_id,
            resource_type="workflow",
            resource_id=workflow_id,
            grantee_type="user",
            grantee_id=user_id,
            operation="delete",
            auth_state=None,
            assigned_at=datetime.now(timezone.utc),
        )
    except PermissionMutationNotFound:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "User workflow permission not found.",
        )
    return {
        "message": "User workflow permission deleted",
        "id": str(result.permission.permission_id),
    }


@router.delete("/knowledge-bases/{knowledge_base_id}/users/{user_id}")
def delete_user_knowledge_permission(
    knowledge_base_id: UUID,
    user_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """user의 KB 직접 권한을 회수하는 DELETE endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _authorize_user_knowledge_permission_change(
        request,
        db,
        current_user,
        organization_id,
        knowledge_base_id,
        user_id,
        require_active_target=False,
    )

    _lock_direct_permission_cleanup_subject(db, organization_id, user_id)
    _lock_permission_key(
        db, "user_knowledge_permission", organization_id, knowledge_base_id, user_id
    )
    permission = (
        db.query(UserKnowledgePermission)
        .filter(
            UserKnowledgePermission.grantee_organization_id == organization_id,
            UserKnowledgePermission.knowledge_base_id == knowledge_base_id,
            UserKnowledgePermission.user_id == user_id,
        )
        .first()
    )
    if permission is None:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "User knowledge permission not found.",
        )

    permission_id = permission.id
    before = _user_permission_audit_snapshot(permission, "knowledge_base_id")
    register_manual_audit_ownership(db, permission, "deleted")
    db.delete(permission)
    _commit_audited_permission_mutation(
        db,
        lambda: _record_user_knowledge_permission_delete_audit(
            db,
            current_user,
            permission,
            before,
        ),
    )
    return {"message": "User knowledge permission deleted", "id": str(permission_id)}


@router.delete("/llm-credentials/{credential_id}/users/{user_id}")
def delete_user_llm_permission(
    credential_id: UUID,
    user_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    auth_token: str | None = Cookie(default=None),
):
    """user의 LLM credential 직접 권한을 회수하는 DELETE endpoint."""
    current_user = _authenticate(request, db, auth_token)
    organization_id = parse_organization_id(request, x_organization_id)
    _authorize_user_llm_permission_change(
        request,
        db,
        current_user,
        organization_id,
        credential_id,
        user_id,
        require_active_target=False,
    )

    _lock_direct_permission_cleanup_subject(db, organization_id, user_id)
    try:
        result = _execute_resource_permission_mutation(
            db,
            current_user,
            organization_id=organization_id,
            resource_type="llm_credential",
            resource_id=credential_id,
            grantee_type="user",
            grantee_id=user_id,
            operation="delete",
            auth_state=None,
            assigned_at=datetime.now(timezone.utc),
        )
    except PermissionMutationNotFound:
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "User LLM credential permission not found.",
        )
    return {
        "message": "User LLM credential permission deleted",
        "id": str(result.permission.permission_id),
    }
