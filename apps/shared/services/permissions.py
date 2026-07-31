import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Optional

from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.llm import LLMCredential
from apps.shared.db.models.mail_credential import (
    MAIL_CREDENTIAL_ACTIVE,
    MailCredential,
)
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MANAGER,
    ORGANIZATION_AUTH_MEMBER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.team import (
    Team,
    TeamKnowledgeDomainPermission,
    TeamKnowledgePermission,
    TeamLLMPermission,
    TeamMailCredentialPermission,
    TeamMembership,
    TeamWorkflowPermission,
    UserKnowledgeDomainPermission,
    UserKnowledgePermission,
    UserLLMPermission,
    UserMailCredentialPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.user_app_creation_permission import (
    UserAppCreationPermission,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.permissions import (
    AUTH_STATE_MANAGER,
    AUTH_STATE_NONE,
    AUTH_STATE_RANK,
    KNOWLEDGE_DOMAIN_ACTIONS,
    knowledge_base_auth_state_allows,
    llm_credential_auth_state_allows,
    mail_credential_auth_state_allows,
    normalize_resource_auth_state,
    stronger_resource_auth_state,
    workflow_auth_state_allows,
)
from apps.shared.schemas.permission import WorkflowPermissionSource
from sqlalchemy.orm import Session


def coerce_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _same_uuid(left: Any, right: Any) -> bool:
    left_uuid = coerce_uuid(left)
    right_uuid = coerce_uuid(right)
    return left_uuid is not None and left_uuid == right_uuid


def _is_active_user(
    db: Session,
    user_id: uuid.UUID,
) -> bool:
    return (
        db.query(User)
        .filter(
            User.id == user_id,
            User.deactivated_at.is_(None),
        )
        .first()
        is not None
    )


def get_organization_membership(
    db: Session,
    user_id: Any,
    organization_id: Any,
) -> Optional[OrganizationMembership]:
    user_uuid = coerce_uuid(user_id)
    organization_uuid = coerce_uuid(organization_id)
    if user_uuid is None or organization_uuid is None:
        return None
    return (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == user_uuid,
            OrganizationMembership.organization_id == organization_uuid,
        )
        .first()
    )


def has_active_organization_membership(
    db: Session,
    user_id: Any,
    organization_id: Any,
) -> bool:
    user_uuid = coerce_uuid(user_id)
    organization_uuid = coerce_uuid(organization_id)
    if user_uuid is None or organization_uuid is None:
        return False
    if not _is_active_user(db, user_uuid):
        return False
    organization = (
        db.query(Organization).filter(Organization.id == organization_uuid).first()
    )
    if not organization or not organization.is_active:
        return False
    return (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == user_uuid,
            OrganizationMembership.organization_id == organization_uuid,
            OrganizationMembership.membership_state == ORGANIZATION_MEMBERSHIP_ACTIVE,
        )
        .first()
        is not None
    )


def get_organization_auth_state(
    db: Session,
    user_id: Any,
    organization_id: Any,
) -> str:
    user_uuid = coerce_uuid(user_id)
    organization_uuid = coerce_uuid(organization_id)
    if user_uuid is None or organization_uuid is None:
        return AUTH_STATE_NONE
    if not _is_active_user(db, user_uuid):
        return AUTH_STATE_NONE

    organization = (
        db.query(Organization).filter(Organization.id == organization_uuid).first()
    )
    if not organization or not organization.is_active:
        return AUTH_STATE_NONE

    membership = get_organization_membership(db, user_uuid, organization_uuid)
    if membership is not None:
        if membership.membership_state != ORGANIZATION_MEMBERSHIP_ACTIVE:
            return AUTH_STATE_NONE
        if membership.organization_auth_state == ORGANIZATION_AUTH_MANAGER:
            return AUTH_STATE_MANAGER
        if membership.organization_auth_state == ORGANIZATION_AUTH_MEMBER:
            return ORGANIZATION_AUTH_MEMBER
        return AUTH_STATE_NONE

    if _same_uuid(organization.created_by, user_uuid) or _same_uuid(
        organization.managed_by, user_uuid
    ):
        return AUTH_STATE_MANAGER
    return AUTH_STATE_NONE


def has_organization_manager_permission(
    db: Session,
    user_id: Any,
    organization_id: Any,
) -> bool:
    user_uuid = coerce_uuid(user_id)
    organization_uuid = coerce_uuid(organization_id)
    if user_uuid is None or organization_uuid is None:
        return False
    return (
        get_organization_auth_state(db, user_uuid, organization_uuid)
        == AUTH_STATE_MANAGER
    )


def has_organization_scope_access(
    db: Session,
    user_id: Any,
    organization_id: Any,
) -> bool:
    user_uuid = coerce_uuid(user_id)
    organization_uuid = coerce_uuid(organization_id)
    if user_uuid is None or organization_uuid is None:
        return False

    return get_organization_auth_state(db, user_uuid, organization_uuid) in {
        AUTH_STATE_MANAGER,
        ORGANIZATION_AUTH_MEMBER,
    }


def has_app_creation_permission(
    db: Session,
    user_id: Any,
    organization_id: Any,
) -> bool:
    """조직 수준 App 생성 능력 판정 (ADR-0016).

    organization owner/manager는 허용하고, 그 외에는
    user_app_creation_permissions row가 있어야 허용한다. fail-closed.
    """
    user_uuid = coerce_uuid(user_id)
    organization_uuid = coerce_uuid(organization_id)
    if user_uuid is None or organization_uuid is None:
        return False
    if not _is_active_user(db, user_uuid):
        return False
    if has_organization_manager_permission(db, user_uuid, organization_uuid):
        return True
    row = (
        db.query(UserAppCreationPermission)
        .filter(
            UserAppCreationPermission.grantee_organization_id == organization_uuid,
            UserAppCreationPermission.user_id == user_uuid,
        )
        .first()
    )
    return row is not None


def _workflow_scope(
    db: Session,
    workflow_id: Any,
    organization_id: Any = None,
) -> tuple[Optional[Workflow], Optional[uuid.UUID]]:
    workflow_uuid = coerce_uuid(workflow_id)
    requested_organization_uuid = coerce_uuid(organization_id)
    if workflow_uuid is None:
        return None, None

    workflow = db.query(Workflow).filter(Workflow.id == workflow_uuid).first()
    if not workflow:
        return None, None

    workflow_organization_uuid = coerce_uuid(workflow.organization_id)
    if (
        workflow_organization_uuid is not None
        and requested_organization_uuid is not None
        and workflow_organization_uuid != requested_organization_uuid
    ):
        return None, None

    return workflow, workflow_organization_uuid or requested_organization_uuid


def _llm_credential_scope(
    db: Session,
    llm_credential_id: Any,
    organization_id: Any = None,
) -> tuple[Optional[LLMCredential], Optional[uuid.UUID]]:
    credential_uuid = coerce_uuid(llm_credential_id)
    requested_organization_uuid = coerce_uuid(organization_id)
    if credential_uuid is None:
        return None, None

    credential = (
        db.query(LLMCredential).filter(LLMCredential.id == credential_uuid).first()
    )
    if not credential:
        return None, None

    credential_organization_uuid = coerce_uuid(credential.organization_id)
    if (
        credential_organization_uuid is not None
        and requested_organization_uuid is not None
        and credential_organization_uuid != requested_organization_uuid
    ):
        return None, None

    return credential, credential_organization_uuid or requested_organization_uuid


def _mail_credential_scope(
    db: Session,
    mail_credential_id: Any,
    organization_id: Any = None,
) -> tuple[Optional[MailCredential], Optional[uuid.UUID]]:
    credential_uuid = coerce_uuid(mail_credential_id)
    requested_organization_uuid = coerce_uuid(organization_id)
    if credential_uuid is None:
        return None, None

    credential = (
        db.query(MailCredential)
        .filter(
            MailCredential.id == credential_uuid,
            MailCredential.status == MAIL_CREDENTIAL_ACTIVE,
        )
        .first()
    )
    if not credential or getattr(credential, "status", MAIL_CREDENTIAL_ACTIVE) != (
        MAIL_CREDENTIAL_ACTIVE
    ):
        return None, None

    credential_organization_uuid = coerce_uuid(credential.organization_id)
    if credential_organization_uuid is None:
        return None, None
    if (
        requested_organization_uuid is not None
        and credential_organization_uuid != requested_organization_uuid
    ):
        return None, None
    return credential, credential_organization_uuid


def _knowledge_base_scope(
    db: Session,
    knowledge_base_id: Any,
    organization_id: Any = None,
    *,
    include_archived: bool = False,
) -> tuple[Optional[KnowledgeBase], Optional[uuid.UUID]]:
    knowledge_base_uuid = coerce_uuid(knowledge_base_id)
    requested_organization_uuid = coerce_uuid(organization_id)
    if knowledge_base_uuid is None:
        return None, None

    query = db.query(KnowledgeBase).filter(KnowledgeBase.id == knowledge_base_uuid)
    query = query.filter(
        KnowledgeBase.lifecycle_state != "deleted"
        if include_archived
        else KnowledgeBase.lifecycle_state == "active"
    )
    knowledge_base = query.first()
    if not knowledge_base:
        return None, None
    lifecycle_state = getattr(knowledge_base, "lifecycle_state", "active")
    if lifecycle_state == "deleted" or (
        lifecycle_state != "active" and not include_archived
    ):
        return None, None

    knowledge_base_organization_uuid = coerce_uuid(knowledge_base.organization_id)
    if knowledge_base_organization_uuid is None:
        return None, None
    if (
        requested_organization_uuid is not None
        and knowledge_base_organization_uuid != requested_organization_uuid
    ):
        return None, None

    return knowledge_base, knowledge_base_organization_uuid


def _auth_state_from_row(row: Any) -> Any:
    row_mapping = getattr(row, "_mapping", None)
    if row_mapping is not None:
        if "auth_state" in row_mapping:
            return row_mapping["auth_state"]
        return next(iter(row_mapping.values()), row)
    if isinstance(row, tuple):
        return row[0]
    return row


def _strongest_auth_state(rows: list[Any], current: str) -> str:
    result = current
    for row in rows:
        auth_state = _auth_state_from_row(row)
        result = stronger_resource_auth_state(result, auth_state)
    return result


def get_effective_workflow_auth_state(
    db: Session,
    user_id: Any,
    workflow_id: Any,
    organization_id: Any = None,
) -> str:
    user_uuid = coerce_uuid(user_id)
    workflow, organization_uuid = _workflow_scope(db, workflow_id, organization_id)
    if user_uuid is None or workflow is None:
        return AUTH_STATE_NONE

    if organization_uuid is None:
        if not _is_active_user(db, user_uuid):
            return AUTH_STATE_NONE
        if _same_uuid(workflow.created_by, user_uuid):
            return AUTH_STATE_MANAGER
        return AUTH_STATE_NONE

    organization_auth_state = get_organization_auth_state(
        db,
        user_uuid,
        organization_uuid,
    )
    if organization_auth_state == AUTH_STATE_MANAGER:
        return AUTH_STATE_MANAGER
    if organization_auth_state != ORGANIZATION_AUTH_MEMBER:
        return AUTH_STATE_NONE

    team_rows = (
        db.query(TeamWorkflowPermission.auth_state)
        .join(TeamMembership, TeamMembership.team_id == TeamWorkflowPermission.team_id)
        .join(Team, Team.id == TeamWorkflowPermission.team_id)
        .filter(
            TeamMembership.user_id == user_uuid,
            TeamWorkflowPermission.workflow_id == workflow.id,
            Team.is_active.is_(True),
            TeamMembership.grantee_organization_id == organization_uuid,
            TeamWorkflowPermission.grantee_organization_id == organization_uuid,
            TeamMembership.grantee_organization_id
            == TeamWorkflowPermission.grantee_organization_id,
            Team.organization_id == organization_uuid,
        )
        .all()
    )
    direct_rows = (
        db.query(UserWorkflowPermission.auth_state)
        .filter(
            UserWorkflowPermission.user_id == user_uuid,
            UserWorkflowPermission.workflow_id == workflow.id,
            UserWorkflowPermission.grantee_organization_id == organization_uuid,
        )
        .all()
    )

    effective_state = _strongest_auth_state(team_rows, AUTH_STATE_NONE)
    return _strongest_auth_state(direct_rows, effective_state)


def get_workflow_permission_sources(
    db: Session,
    user_id: Any,
    workflow_id: Any,
    organization_id: Any = None,
) -> list[WorkflowPermissionSource]:
    """현재 user의 workflow 권한 출처를 team/user direct source로 반환한다."""
    user_uuid = coerce_uuid(user_id)
    workflow, organization_uuid = _workflow_scope(db, workflow_id, organization_id)
    if user_uuid is None or workflow is None or organization_uuid is None:
        return []
    if get_organization_auth_state(db, user_uuid, organization_uuid) == AUTH_STATE_NONE:
        return []

    sources_by_workflow_id = _workflow_permission_sources_for_workflow_ids(
        db,
        user_uuid,
        [workflow.id],
        organization_uuid,
    )
    return sources_by_workflow_id.get(workflow.id, [])


def get_workflow_permission_sources_by_workflow_ids(
    db: Session,
    user_id: Any,
    workflow_ids: list[Any],
    organization_id: Any,
) -> dict[uuid.UUID, list[WorkflowPermissionSource]]:
    """여러 workflow의 권한 출처를 한 번에 조회한다."""
    user_uuid = coerce_uuid(user_id)
    organization_uuid = coerce_uuid(organization_id)
    workflow_uuid_list = [
        workflow_uuid
        for workflow_id in workflow_ids
        if (workflow_uuid := coerce_uuid(workflow_id)) is not None
    ]
    if not workflow_uuid_list or user_uuid is None or organization_uuid is None:
        return {}
    if get_organization_auth_state(db, user_uuid, organization_uuid) == AUTH_STATE_NONE:
        return {workflow_id: [] for workflow_id in workflow_uuid_list}

    return _workflow_permission_sources_for_workflow_ids(
        db,
        user_uuid,
        workflow_uuid_list,
        organization_uuid,
    )


def _workflow_permission_sources_for_workflow_ids(
    db: Session,
    user_uuid: uuid.UUID,
    workflow_ids: list[uuid.UUID],
    organization_uuid: uuid.UUID,
) -> dict[uuid.UUID, list[WorkflowPermissionSource]]:
    workflow_id_set = set(workflow_ids)
    sources_by_workflow_id: dict[uuid.UUID, list[WorkflowPermissionSource]] = {
        workflow_id: [] for workflow_id in workflow_id_set
    }
    team_rows = (
        db.query(TeamWorkflowPermission, Team)
        .join(Team, Team.id == TeamWorkflowPermission.team_id)
        .join(TeamMembership, TeamMembership.team_id == TeamWorkflowPermission.team_id)
        .join(Workflow, Workflow.id == TeamWorkflowPermission.workflow_id)
        .filter(
            TeamMembership.user_id == user_uuid,
            TeamWorkflowPermission.workflow_id.in_(workflow_id_set),
            Workflow.organization_id == organization_uuid,
            Team.is_active.is_(True),
            TeamMembership.grantee_organization_id == organization_uuid,
            TeamWorkflowPermission.grantee_organization_id == organization_uuid,
            TeamMembership.grantee_organization_id
            == TeamWorkflowPermission.grantee_organization_id,
            Team.organization_id == organization_uuid,
        )
        .order_by(Team.name.asc(), TeamWorkflowPermission.id.asc())
        .all()
    )
    direct_rows = (
        db.query(UserWorkflowPermission, User)
        .join(User, User.id == UserWorkflowPermission.user_id)
        .join(Workflow, Workflow.id == UserWorkflowPermission.workflow_id)
        .filter(
            UserWorkflowPermission.user_id == user_uuid,
            UserWorkflowPermission.workflow_id.in_(workflow_id_set),
            Workflow.organization_id == organization_uuid,
            UserWorkflowPermission.grantee_organization_id == organization_uuid,
            User.deactivated_at.is_(None),
        )
        .order_by(User.name.asc(), User.email.asc(), UserWorkflowPermission.id.asc())
        .all()
    )

    for permission, team in team_rows:
        auth_state = normalize_resource_auth_state(permission.auth_state)
        if auth_state == AUTH_STATE_NONE:
            continue
        sources_by_workflow_id.setdefault(permission.workflow_id, []).append(
            WorkflowPermissionSource(
                type="team",
                team_id=team.id,
                team_name=team.name,
                auth_state=auth_state,
            )
        )

    for permission, user in direct_rows:
        auth_state = normalize_resource_auth_state(permission.auth_state)
        if auth_state == AUTH_STATE_NONE:
            continue
        sources_by_workflow_id.setdefault(permission.workflow_id, []).append(
            WorkflowPermissionSource(
                type="user",
                user_id=user.id,
                user_name=user.name or user.email,
                auth_state=auth_state,
            )
        )

    return {
        workflow_id: _sort_workflow_permission_sources(sources)
        for workflow_id, sources in sources_by_workflow_id.items()
    }


def _sort_workflow_permission_sources(
    sources: list[WorkflowPermissionSource],
) -> list[WorkflowPermissionSource]:
    return sorted(
        sources,
        key=lambda source: (
            -AUTH_STATE_RANK.get(source.auth_state, 0),
            0 if source.type == "team" else 1,
            source.team_name or source.user_name or "",
            str(source.team_id or source.user_id or ""),
        ),
    )


def has_workflow_permission(
    db: Session,
    user_id: Any,
    workflow_id: Any,
    action: str,
    organization_id: Any = None,
) -> bool:
    auth_state = get_effective_workflow_auth_state(
        db, user_id, workflow_id, organization_id=organization_id
    )
    return workflow_auth_state_allows(auth_state, action)


def get_effective_llm_credential_auth_state(
    db: Session,
    user_id: Any,
    llm_credential_id: Any,
    organization_id: Any = None,
) -> str:
    user_uuid = coerce_uuid(user_id)
    credential, organization_uuid = _llm_credential_scope(
        db, llm_credential_id, organization_id
    )
    if user_uuid is None or credential is None:
        return AUTH_STATE_NONE

    if organization_uuid is None:
        if not _is_active_user(db, user_uuid):
            return AUTH_STATE_NONE
        if _same_uuid(credential.user_id, user_uuid):
            return AUTH_STATE_MANAGER
        return AUTH_STATE_NONE

    organization_auth_state = get_organization_auth_state(
        db,
        user_uuid,
        organization_uuid,
    )
    if organization_auth_state == AUTH_STATE_MANAGER:
        return AUTH_STATE_MANAGER
    if organization_auth_state != ORGANIZATION_AUTH_MEMBER:
        return AUTH_STATE_NONE

    team_rows = (
        db.query(TeamLLMPermission.auth_state)
        .join(TeamMembership, TeamMembership.team_id == TeamLLMPermission.team_id)
        .join(Team, Team.id == TeamLLMPermission.team_id)
        .filter(
            TeamMembership.user_id == user_uuid,
            TeamLLMPermission.llm_credential_id == credential.id,
            Team.is_active.is_(True),
            TeamMembership.grantee_organization_id == organization_uuid,
            TeamLLMPermission.grantee_organization_id == organization_uuid,
            TeamMembership.grantee_organization_id
            == TeamLLMPermission.grantee_organization_id,
            Team.organization_id == organization_uuid,
        )
        .all()
    )
    direct_rows = (
        db.query(UserLLMPermission.auth_state)
        .filter(
            UserLLMPermission.user_id == user_uuid,
            UserLLMPermission.llm_credential_id == credential.id,
            UserLLMPermission.grantee_organization_id == organization_uuid,
        )
        .all()
    )

    effective_state = _strongest_auth_state(team_rows, AUTH_STATE_NONE)
    return _strongest_auth_state(direct_rows, effective_state)


def has_llm_credential_permission(
    db: Session,
    user_id: Any,
    llm_credential_id: Any,
    action: str,
    organization_id: Any = None,
) -> bool:
    auth_state = get_effective_llm_credential_auth_state(
        db,
        user_id,
        llm_credential_id,
        organization_id=organization_id,
    )
    return llm_credential_auth_state_allows(auth_state, action)


def get_effective_mail_credential_auth_state(
    db: Session,
    user_id: Any,
    mail_credential_id: Any,
    organization_id: Any = None,
) -> str:
    user_uuid = coerce_uuid(user_id)
    credential, organization_uuid = _mail_credential_scope(
        db, mail_credential_id, organization_id
    )
    if user_uuid is None or credential is None or organization_uuid is None:
        return AUTH_STATE_NONE

    organization_auth_state = get_organization_auth_state(
        db, user_uuid, organization_uuid
    )
    if organization_auth_state == AUTH_STATE_MANAGER:
        return AUTH_STATE_MANAGER
    if organization_auth_state != ORGANIZATION_AUTH_MEMBER:
        return AUTH_STATE_NONE

    team_rows = (
        db.query(TeamMailCredentialPermission.auth_state)
        .join(
            TeamMembership,
            TeamMembership.team_id == TeamMailCredentialPermission.team_id,
        )
        .join(Team, Team.id == TeamMailCredentialPermission.team_id)
        .filter(
            TeamMembership.user_id == user_uuid,
            TeamMailCredentialPermission.mail_credential_id == credential.id,
            Team.is_active.is_(True),
            TeamMembership.grantee_organization_id == organization_uuid,
            TeamMailCredentialPermission.grantee_organization_id == organization_uuid,
            TeamMembership.grantee_organization_id
            == TeamMailCredentialPermission.grantee_organization_id,
            Team.organization_id == organization_uuid,
        )
        .all()
    )
    direct_rows = (
        db.query(UserMailCredentialPermission.auth_state)
        .filter(
            UserMailCredentialPermission.user_id == user_uuid,
            UserMailCredentialPermission.mail_credential_id == credential.id,
            UserMailCredentialPermission.grantee_organization_id == organization_uuid,
        )
        .all()
    )

    effective_state = _strongest_auth_state(team_rows, AUTH_STATE_NONE)
    return _strongest_auth_state(direct_rows, effective_state)


def get_effective_mail_credential_auth_states(
    db: Session,
    user_id: Any,
    mail_credential_ids: Iterable[Any],
    organization_id: Any,
) -> dict[uuid.UUID, str]:
    """Resolve Mail credential auth states with a fixed number of queries."""
    user_uuid = coerce_uuid(user_id)
    organization_uuid = coerce_uuid(organization_id)
    requested_ids = {
        credential_id
        for raw_id in mail_credential_ids
        if (credential_id := coerce_uuid(raw_id)) is not None
    }
    if user_uuid is None or organization_uuid is None or not requested_ids:
        return {}

    scoped_rows = (
        db.query(MailCredential.id, MailCredential.status)
        .filter(
            MailCredential.id.in_(requested_ids),
            MailCredential.organization_id == organization_uuid,
            MailCredential.status == MAIL_CREDENTIAL_ACTIVE,
        )
        .all()
    )
    scoped_ids: set[uuid.UUID] = set()
    for row in scoped_rows:
        raw_id, status = _mail_credential_scope_values(row)
        credential_id = coerce_uuid(raw_id)
        if status == MAIL_CREDENTIAL_ACTIVE and credential_id is not None:
            scoped_ids.add(credential_id)
    if not scoped_ids:
        return {}

    organization_auth_state = get_organization_auth_state(
        db,
        user_uuid,
        organization_uuid,
    )
    if organization_auth_state == AUTH_STATE_MANAGER:
        return {credential_id: AUTH_STATE_MANAGER for credential_id in scoped_ids}
    if organization_auth_state != ORGANIZATION_AUTH_MEMBER:
        return {credential_id: AUTH_STATE_NONE for credential_id in scoped_ids}

    team_rows = (
        db.query(
            TeamMailCredentialPermission.mail_credential_id,
            TeamMailCredentialPermission.auth_state,
        )
        .join(
            TeamMembership,
            TeamMembership.team_id == TeamMailCredentialPermission.team_id,
        )
        .join(Team, Team.id == TeamMailCredentialPermission.team_id)
        .filter(
            TeamMembership.user_id == user_uuid,
            TeamMailCredentialPermission.mail_credential_id.in_(scoped_ids),
            Team.is_active.is_(True),
            TeamMembership.grantee_organization_id == organization_uuid,
            TeamMailCredentialPermission.grantee_organization_id
            == organization_uuid,
            TeamMembership.grantee_organization_id
            == TeamMailCredentialPermission.grantee_organization_id,
            Team.organization_id == organization_uuid,
        )
        .all()
    )
    direct_rows = (
        db.query(
            UserMailCredentialPermission.mail_credential_id,
            UserMailCredentialPermission.auth_state,
        )
        .filter(
            UserMailCredentialPermission.user_id == user_uuid,
            UserMailCredentialPermission.mail_credential_id.in_(scoped_ids),
            UserMailCredentialPermission.grantee_organization_id
            == organization_uuid,
        )
        .all()
    )

    result = {credential_id: AUTH_STATE_NONE for credential_id in scoped_ids}
    for row in (*team_rows, *direct_rows):
        credential_id, auth_state = _resource_auth_state_values(row)
        credential_uuid = coerce_uuid(credential_id)
        if credential_uuid not in result:
            continue
        result[credential_uuid] = stronger_resource_auth_state(
            result[credential_uuid],
            auth_state,
        )
    return result


def has_mail_credential_permission(
    db: Session,
    user_id: Any,
    mail_credential_id: Any,
    action: str,
    organization_id: Any = None,
) -> bool:
    auth_state = get_effective_mail_credential_auth_state(
        db,
        user_id,
        mail_credential_id,
        organization_id=organization_id,
    )
    return mail_credential_auth_state_allows(auth_state, action)


def _mail_credential_scope_values(row: Any) -> tuple[Any, str]:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return (
            mapping.get("id"),
            mapping.get("status", MAIL_CREDENTIAL_ACTIVE),
        )
    if isinstance(row, tuple):
        return (
            row[0] if row else None,
            row[1] if len(row) >= 2 else MAIL_CREDENTIAL_ACTIVE,
        )
    return getattr(row, "id", row), getattr(
        row,
        "status",
        MAIL_CREDENTIAL_ACTIVE,
    )


def _resource_auth_state_values(row: Any) -> tuple[Any, Any]:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return (
            mapping.get("mail_credential_id"),
            mapping.get("auth_state", AUTH_STATE_NONE),
        )
    if isinstance(row, tuple) and len(row) >= 2:
        return row[0], row[1]
    return None, AUTH_STATE_NONE


def get_effective_knowledge_base_auth_state(
    db: Session,
    user_id: Any,
    knowledge_base_id: Any,
    organization_id: Any = None,
    *,
    include_archived: bool = False,
) -> str:
    user_uuid = coerce_uuid(user_id)
    knowledge_base, organization_uuid = _knowledge_base_scope(
        db,
        knowledge_base_id,
        organization_id,
        include_archived=include_archived,
    )
    if user_uuid is None or knowledge_base is None or organization_uuid is None:
        return AUTH_STATE_NONE

    organization_auth_state = get_organization_auth_state(
        db,
        user_uuid,
        organization_uuid,
    )
    if organization_auth_state == AUTH_STATE_MANAGER:
        return AUTH_STATE_MANAGER
    if organization_auth_state != ORGANIZATION_AUTH_MEMBER:
        return AUTH_STATE_NONE

    team_rows = (
        db.query(TeamKnowledgePermission.auth_state)
        .join(TeamMembership, TeamMembership.team_id == TeamKnowledgePermission.team_id)
        .join(Team, Team.id == TeamKnowledgePermission.team_id)
        .filter(
            TeamMembership.user_id == user_uuid,
            TeamKnowledgePermission.knowledge_base_id == knowledge_base.id,
            Team.is_active.is_(True),
            TeamMembership.grantee_organization_id == organization_uuid,
            TeamKnowledgePermission.grantee_organization_id == organization_uuid,
            TeamMembership.grantee_organization_id
            == TeamKnowledgePermission.grantee_organization_id,
            Team.organization_id == organization_uuid,
        )
        .all()
    )

    direct_rows = (
        db.query(UserKnowledgePermission.auth_state)
        .filter(
            UserKnowledgePermission.user_id == user_uuid,
            UserKnowledgePermission.knowledge_base_id == knowledge_base.id,
            UserKnowledgePermission.grantee_organization_id == organization_uuid,
        )
        .all()
    )

    effective_state = _strongest_auth_state(team_rows, AUTH_STATE_NONE)
    return _strongest_auth_state(direct_rows, effective_state)


def has_knowledge_base_permission(
    db: Session,
    user_id: Any,
    knowledge_base_id: Any,
    action: str,
    organization_id: Any = None,
    *,
    include_archived: bool = False,
) -> bool:
    auth_state = get_effective_knowledge_base_auth_state(
        db,
        user_id,
        knowledge_base_id,
        organization_id=organization_id,
        include_archived=include_archived,
    )
    return knowledge_base_auth_state_allows(auth_state, action)


def get_effective_knowledge_domain_actions(
    db: Session,
    user_id: Any,
    organization_id: Any,
    *,
    now: datetime | None = None,
) -> set[str]:
    """Return active additive Knowledge management actions for one organization."""

    user_uuid = coerce_uuid(user_id)
    organization_uuid = coerce_uuid(organization_id)
    if user_uuid is None or organization_uuid is None:
        return set()

    organization_auth_state = get_organization_auth_state(
        db, user_uuid, organization_uuid
    )
    if organization_auth_state == AUTH_STATE_MANAGER:
        return set(KNOWLEDGE_DOMAIN_ACTIONS)
    if organization_auth_state != ORGANIZATION_AUTH_MEMBER:
        return set()

    evaluated_at = now or datetime.now(timezone.utc)
    team_rows = (
        db.query(
            TeamKnowledgeDomainPermission.permission_action,
            TeamKnowledgeDomainPermission.expires_at,
        )
        .join(
            TeamMembership,
            TeamMembership.team_id == TeamKnowledgeDomainPermission.team_id,
        )
        .join(Team, Team.id == TeamKnowledgeDomainPermission.team_id)
        .filter(
            TeamMembership.user_id == user_uuid,
            TeamMembership.grantee_organization_id == organization_uuid,
            TeamKnowledgeDomainPermission.organization_id == organization_uuid,
            Team.organization_id == organization_uuid,
            Team.is_active.is_(True),
        )
        .all()
    )
    user_rows = (
        db.query(
            UserKnowledgeDomainPermission.permission_action,
            UserKnowledgeDomainPermission.expires_at,
        )
        .filter(
            UserKnowledgeDomainPermission.user_id == user_uuid,
            UserKnowledgeDomainPermission.organization_id == organization_uuid,
        )
        .all()
    )

    actions: set[str] = set()
    for row in [*team_rows, *user_rows]:
        action, expires_at = _knowledge_domain_row_values(row)
        if action not in KNOWLEDGE_DOMAIN_ACTIONS:
            continue
        if expires_at is not None and _as_aware_utc(expires_at) <= evaluated_at:
            continue
        actions.add(action)
    return actions


def has_knowledge_domain_permission(
    db: Session,
    user_id: Any,
    organization_id: Any,
    action: str,
) -> bool:
    if action not in KNOWLEDGE_DOMAIN_ACTIONS:
        return False
    return action in get_effective_knowledge_domain_actions(
        db, user_id, organization_id
    )


def _knowledge_domain_row_values(row: Any) -> tuple[str, datetime | None]:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return str(mapping["permission_action"]), mapping["expires_at"]
    if isinstance(row, tuple):
        return str(row[0]), row[1]
    return str(row.permission_action), row.expires_at


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def canonical_effective_auth_state(auth_state: Any) -> str:
    return normalize_resource_auth_state(auth_state)
