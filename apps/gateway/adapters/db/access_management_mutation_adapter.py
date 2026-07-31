from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from apps.gateway.adapters.db.access_management_locking import (
    lock_access_subject_rows,
)
from apps.gateway.application.access_management.errors import WorkflowPrimaryChanged
from apps.gateway.services.app_lifecycle_lock import (
    AppPrimaryChangedDuringMutationError,
    lock_app_for_workflow_mutation,
)
from apps.gateway.application.access_management.models import (
    AppCreationPermissionSnapshot,
    AppliedMutationDescriptor,
    DirectPermissionSnapshot,
    LockedAppCreationPermission,
    LockedDirectPermission,
    LockedMember,
    LockedTeamMembership,
    MemberSnapshot,
    ResourceDescriptor,
    ResourceType,
)
from apps.gateway.application.access_management.policies import strongest_auth_state
from apps.gateway.services.resource_permission_registry import resource_permission_spec
from apps.gateway.services.workflow_permission_lock import (
    lock_workflow_permission_scope,
)
from apps.shared.audit.manual_ownership import register_manual_audit_ownership
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.llm import LLMCredential
from apps.shared.db.models.mail_credential import MailCredential
from apps.shared.db.models.organization_membership import (
    OrganizationMembership,
)
from apps.shared.db.models.team import (
    Team,
    TeamKnowledgePermission,
    TeamLLMPermission,
    TeamMailCredentialPermission,
    TeamMembership,
    TeamWorkflowPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.user_app_creation_permission import UserAppCreationPermission
from apps.shared.db.models.workflow import Workflow

_OPERATIONAL_STATES = ("viewer", "operator", "builder", "manager")


class SqlAlchemyAccessManagementMutationAdapter:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._membership: OrganizationMembership | None = None
        self._user: User | None = None
        self._member_snapshot_value: MemberSnapshot | None = None
        self._team: Team | None = None
        self._team_membership: TeamMembership | None = None
        self._team_source_count = 0
        self._resource: ResourceDescriptor | None = None
        self._direct_permission = None
        self._direct_model = None
        self._direct_route = None
        self._strongest_team_state = "none"
        self._app_permission: UserAppCreationPermission | None = None

    def lock_member(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        manager_reduction: bool,
    ) -> LockedMember | None:
        locked = lock_access_subject_rows(
            self.db,
            organization_id,
            user_id,
            manager_reduction=manager_reduction,
        )
        if locked is None:
            return None

        membership = locked.membership
        user = locked.user

        self._membership = membership
        self._user = user
        self._member_snapshot_value = _member_snapshot(membership, user)
        return LockedMember(
            member=self._member_snapshot_value,
            active_manager_count=locked.active_manager_count,
        )

    def set_membership_state(self, state: str) -> AppliedMutationDescriptor:
        membership, _, before = self._required_member_state()
        before_safe = _membership_audit_snapshot(before)
        membership.membership_state = state
        membership.updated_at = _now()
        after = _member_snapshot(membership, self._user)
        self._member_snapshot_value = after
        return AppliedMutationDescriptor(
            action="organization.member.update",
            category="action",
            target_type="organization_membership",
            target_id=membership.id,
            before=before_safe,
            after=_membership_audit_snapshot(after),
            effective_access_changed=(
                before.effective_access_enabled != after.effective_access_enabled
            ),
        )

    def set_organization_role(self, role: str) -> AppliedMutationDescriptor:
        membership, _, before = self._required_member_state()
        before_safe = _membership_audit_snapshot(before)
        membership.organization_auth_state = role
        membership.updated_at = _now()
        after = _member_snapshot(membership, self._user)
        self._member_snapshot_value = after
        return AppliedMutationDescriptor(
            action="organization.member.update",
            category="action",
            target_type="organization_membership",
            target_id=membership.id,
            before=before_safe,
            after=_membership_audit_snapshot(after),
            effective_access_changed=(
                before.organization_auth_state != after.organization_auth_state
            ),
        )

    def lock_team_membership(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        team_id: uuid.UUID,
        *,
        require_active_team: bool,
    ) -> LockedTeamMembership | None:
        query = self.db.query(Team).filter(
            Team.id == team_id,
            Team.organization_id == organization_id,
        )
        if require_active_team:
            query = query.filter(Team.is_active.is_(True))
        team = query.with_for_update().first()
        if team is None:
            return None
        membership = (
            self.db.query(TeamMembership)
            .filter(
                TeamMembership.grantee_organization_id == organization_id,
                TeamMembership.user_id == user_id,
                TeamMembership.team_id == team_id,
            )
            .with_for_update()
            .first()
        )
        self._team = team
        self._team_membership = membership
        self._team_source_count = (
            self._count_team_sources(organization_id, team_id) if team.is_active else 0
        )
        return LockedTeamMembership(
            team_id=team.id,
            team_active=team.is_active,
            membership_id=membership.id if membership is not None else None,
            affected_resource_source_count=self._team_source_count,
        )

    def add_team_membership(
        self,
        actor_id: uuid.UUID,
    ) -> AppliedMutationDescriptor:
        member = self._required_member_snapshot()
        if self._team is None:
            raise RuntimeError("Team lock is required before mutation")
        row = TeamMembership(
            id=uuid.uuid4(),
            grantee_organization_id=member.organization_id,
            user_id=member.user_id,
            team_id=self._team.id,
            assigned_by=actor_id,
            assigned_at=_now(),
        )
        register_manual_audit_ownership(self.db, row, "created")
        self.db.add(row)
        self._team_membership = row
        return AppliedMutationDescriptor(
            action="team_membership.created",
            category="data_change",
            target_type="team_membership",
            target_id=row.id,
            before=None,
            after=_team_membership_audit_snapshot(row),
            effective_access_changed=None,
            affected_resource_source_count=self._team_source_count,
        )

    def remove_team_membership(self) -> AppliedMutationDescriptor:
        row = self._team_membership
        if row is None:
            raise RuntimeError("Team membership lock is required before mutation")
        before = _team_membership_audit_snapshot(row)
        register_manual_audit_ownership(self.db, row, "deleted")
        self.db.delete(row)
        return AppliedMutationDescriptor(
            action="team_membership.deleted",
            category="data_change",
            target_type="team_membership",
            target_id=row.id,
            before=before,
            after=None,
            effective_access_changed=None,
            affected_resource_source_count=self._team_source_count,
        )

    def lock_resource(
        self,
        organization_id: uuid.UUID,
        resource_type: ResourceType,
        resource_id: uuid.UUID,
    ) -> ResourceDescriptor | None:
        spec = resource_permission_spec(resource_type)
        query = self.db.query(spec.target_model).filter(
            spec.target_model.id == resource_id,
            spec.target_model.organization_id == organization_id,
        )
        if spec.active_filter is not None:
            query = query.filter(spec.active_filter(spec.target_model))
        if resource_type == "llm_credential":
            query = query.filter(LLMCredential.is_valid.is_(True))
        resource = query.with_for_update().first()
        if resource is None:
            return None

        if resource_type == "workflow":
            try:
                app = lock_app_for_workflow_mutation(
                    self.db,
                    app_id=resource.app_id,
                    workflow_id=resource.id,
                    organization_id=organization_id,
                )
            except AppPrimaryChangedDuringMutationError as exc:
                raise WorkflowPrimaryChanged() from exc
            if app is None:
                return None
            lock_workflow_permission_scope(
                self.db,
                organization_id=organization_id,
                workflow_id=resource.id,
            )
            resource_name = app.name
        elif resource_type == "knowledge_base":
            resource_name = resource.name
        else:
            resource_name = resource.credential_name
        self._resource = ResourceDescriptor(
            resource_type=resource_type,
            resource_id=resource.id,
            resource_name=resource_name,
        )
        return self._resource

    def lock_direct_permission(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        resource: ResourceDescriptor,
    ) -> LockedDirectPermission:
        spec = resource_permission_spec(resource.resource_type)
        route = spec.user_route
        filters = route.filters(
            resource_id=resource.resource_id,
            grantee_id=user_id,
        )
        permission = (
            self.db.query(route.model)
            .filter(
                route.model.grantee_organization_id == organization_id,
                *[getattr(route.model, key) == value for key, value in filters.items()],
            )
            .with_for_update()
            .first()
        )
        self._direct_permission = permission
        self._direct_model = route.model
        self._direct_route = route
        self._strongest_team_state = self._strongest_team_permission(
            organization_id,
            user_id,
            resource,
        )
        return LockedDirectPermission(
            resource=resource,
            permission=(
                _direct_permission_snapshot(permission)
                if permission is not None
                else None
            ),
            strongest_team_auth_state=self._strongest_team_state,
        )

    def grant_direct_permission(
        self,
        actor_id: uuid.UUID,
        auth_state: str,
    ) -> AppliedMutationDescriptor:
        member = self._required_member_snapshot()
        resource, model, route = self._required_direct_state()
        now = _now()
        row = self._direct_permission
        before = _direct_audit_snapshot(row, resource, member) if row else None
        before_effective = self._effective_direct_state(
            row.auth_state if row is not None else "none"
        )
        operation = "updated"
        if row is None:
            values = {
                "id": uuid.uuid4(),
                "grantee_organization_id": member.organization_id,
                "user_id": member.user_id,
                route.resource_column: resource.resource_id,
                "auth_state": auth_state,
                "assigned_by": actor_id,
                "assigned_at": now,
            }
            row = model(**values)
            register_manual_audit_ownership(self.db, row, "created")
            self.db.add(row)
            operation = "created"
        else:
            register_manual_audit_ownership(self.db, row, "updated")
            row.auth_state = auth_state
            row.assigned_by = actor_id
            row.assigned_at = now
        self._direct_permission = row
        after = _direct_audit_snapshot(row, resource, member)
        return AppliedMutationDescriptor(
            action=f"{_direct_target_type(resource.resource_type)}.{operation}",
            category="data_change",
            target_type=_direct_target_type(resource.resource_type),
            target_id=row.id,
            before=before,
            after=after,
            effective_access_changed=(
                before_effective != self._effective_direct_state(auth_state)
            ),
        )

    def revoke_direct_permission(self) -> AppliedMutationDescriptor:
        member = self._required_member_snapshot()
        resource, _, _ = self._required_direct_state()
        row = self._direct_permission
        if row is None:
            raise RuntimeError("Direct permission lock is required before mutation")
        before = _direct_audit_snapshot(row, resource, member)
        before_effective = self._effective_direct_state(row.auth_state)
        register_manual_audit_ownership(self.db, row, "deleted")
        self.db.delete(row)
        return AppliedMutationDescriptor(
            action=f"{_direct_target_type(resource.resource_type)}.deleted",
            category="data_change",
            target_type=_direct_target_type(resource.resource_type),
            target_id=row.id,
            before=before,
            after=None,
            effective_access_changed=(
                before_effective != self._effective_direct_state("none")
            ),
        )

    def lock_app_creation_permission(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> LockedAppCreationPermission:
        row = (
            self.db.query(UserAppCreationPermission)
            .filter(
                UserAppCreationPermission.grantee_organization_id == organization_id,
                UserAppCreationPermission.user_id == user_id,
            )
            .with_for_update()
            .first()
        )
        self._app_permission = row
        return LockedAppCreationPermission(
            permission=(
                AppCreationPermissionSnapshot(row.id, row.assigned_at)
                if row is not None
                else None
            )
        )

    def grant_app_creation_permission(
        self,
        actor_id: uuid.UUID,
    ) -> AppliedMutationDescriptor:
        member = self._required_member_snapshot()
        row = UserAppCreationPermission(
            id=uuid.uuid4(),
            grantee_organization_id=member.organization_id,
            user_id=member.user_id,
            assigned_by=actor_id,
            assigned_at=_now(),
        )
        self.db.add(row)
        self._app_permission = row
        return AppliedMutationDescriptor(
            action="user_app_creation_permission.created",
            category="action",
            target_type="user_app_creation_permission",
            target_id=row.id,
            before=None,
            after=_app_audit_snapshot(row),
            effective_access_changed=member.effective_access_enabled,
        )

    def revoke_app_creation_permission(self) -> AppliedMutationDescriptor:
        member = self._required_member_snapshot()
        row = self._app_permission
        if row is None:
            raise RuntimeError(
                "App creation permission lock is required before mutation"
            )
        before = _app_audit_snapshot(row)
        self.db.delete(row)
        return AppliedMutationDescriptor(
            action="user_app_creation_permission.deleted",
            category="action",
            target_type="user_app_creation_permission",
            target_id=row.id,
            before=before,
            after=None,
            effective_access_changed=member.effective_access_enabled,
        )

    def _required_member_state(
        self,
    ) -> tuple[OrganizationMembership, User, MemberSnapshot]:
        if (
            self._membership is None
            or self._user is None
            or self._member_snapshot_value is None
        ):
            raise RuntimeError("Member lock is required before mutation")
        return self._membership, self._user, self._member_snapshot_value

    def _required_member_snapshot(self) -> MemberSnapshot:
        return self._required_member_state()[2]

    def _required_direct_state(self):
        if (
            self._resource is None
            or self._direct_model is None
            or self._direct_route is None
        ):
            raise RuntimeError("Resource and direct permission locks are required")
        return self._resource, self._direct_model, self._direct_route

    def _effective_direct_state(self, direct_state: str) -> str:
        member = self._required_member_snapshot()
        if not member.effective_access_enabled:
            return "none"
        if member.manager_override:
            return "manager"
        return strongest_auth_state((self._strongest_team_state, direct_state))

    def _strongest_team_permission(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        resource: ResourceDescriptor,
    ) -> str:
        spec = resource_permission_spec(resource.resource_type)
        route = spec.team_route
        resource_column = getattr(route.model, route.resource_column)
        rows = (
            self.db.query(route.model)
            .join(
                TeamMembership,
                (TeamMembership.team_id == route.model.team_id)
                & (
                    TeamMembership.grantee_organization_id
                    == route.model.grantee_organization_id
                ),
            )
            .join(Team, Team.id == route.model.team_id)
            .filter(
                route.model.grantee_organization_id == organization_id,
                TeamMembership.user_id == user_id,
                Team.is_active.is_(True),
                resource_column == resource.resource_id,
                route.model.auth_state.in_(_OPERATIONAL_STATES),
            )
            .all()
        )
        return strongest_auth_state(row.auth_state for row in rows)

    def _count_team_sources(
        self,
        organization_id: uuid.UUID,
        team_id: uuid.UUID,
    ) -> int:
        return sum(
            self._count_operational_team_permission(
                organization_id,
                team_id,
                model,
                resource_model,
                resource_column,
            )
            for model, resource_model, resource_column in (
                (TeamWorkflowPermission, Workflow, "workflow_id"),
                (TeamKnowledgePermission, KnowledgeBase, "knowledge_base_id"),
                (TeamLLMPermission, LLMCredential, "llm_credential_id"),
                (
                    TeamMailCredentialPermission,
                    MailCredential,
                    "mail_credential_id",
                ),
            )
        )

    def _count_operational_team_permission(
        self,
        organization_id,
        team_id,
        permission_model,
        resource_model,
        resource_column,
    ) -> int:
        query = self.db.query(permission_model).join(
            resource_model,
            (resource_model.id == getattr(permission_model, resource_column))
            & (
                resource_model.organization_id
                == permission_model.grantee_organization_id
            ),
        )
        if resource_model is KnowledgeBase:
            query = query.filter(KnowledgeBase.lifecycle_state == "active")
        if resource_model is LLMCredential:
            query = query.filter(LLMCredential.is_valid.is_(True))
        if resource_model is MailCredential:
            query = query.filter(MailCredential.status == "active")
        return query.filter(
            permission_model.grantee_organization_id == organization_id,
            permission_model.team_id == team_id,
            permission_model.auth_state.in_(_OPERATIONAL_STATES),
        ).count()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _member_snapshot(
    membership: OrganizationMembership,
    user: User,
) -> MemberSnapshot:
    return MemberSnapshot(
        membership_id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        name=user.name or "",
        email=user.email,
        user_active=user.deactivated_at is None,
        membership_state=membership.membership_state,
        organization_auth_state=membership.organization_auth_state,
        updated_at=membership.updated_at,
    )


def _membership_audit_snapshot(member: MemberSnapshot) -> dict[str, object]:
    return {
        "organization_id": member.organization_id,
        "user_id": member.user_id,
        "membership_state": member.membership_state,
        "organization_auth_state": member.organization_auth_state,
    }


def _team_membership_audit_snapshot(row: TeamMembership) -> dict[str, object]:
    return {
        "grantee_organization_id": row.grantee_organization_id,
        "team_id": row.team_id,
        "user_id": row.user_id,
    }


def _direct_permission_snapshot(row) -> DirectPermissionSnapshot:
    return DirectPermissionSnapshot(
        permission_id=row.id,
        auth_state=row.auth_state,
        assigned_at=row.assigned_at,
    )


def _direct_audit_snapshot(
    row,
    resource: ResourceDescriptor,
    member: MemberSnapshot,
) -> dict[str, object]:
    resource_field = {
        "workflow": "workflow_id",
        "knowledge_base": "knowledge_base_id",
        "llm_credential": "llm_credential_id",
        "mail_credential": "mail_credential_id",
    }[resource.resource_type]
    return {
        "grantee_organization_id": member.organization_id,
        "user_id": member.user_id,
        resource_field: resource.resource_id,
        "auth_state": row.auth_state,
    }


def _direct_target_type(resource_type: ResourceType) -> str:
    return {
        "workflow": "user_workflow_permission",
        "knowledge_base": "user_knowledge_permission",
        "llm_credential": "user_llm_permission",
        "mail_credential": "user_mail_credential_permission",
    }[resource_type]


def _app_audit_snapshot(row: UserAppCreationPermission) -> dict[str, object]:
    return {
        "grantee_organization_id": row.grantee_organization_id,
        "user_id": row.user_id,
    }
