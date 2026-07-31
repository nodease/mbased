from __future__ import annotations

import uuid
from typing import Protocol

from .models import (
    AccessActionCommand,
    AppliedMutationDescriptor,
    AppCreationPermissionSnapshot,
    LockedAppCreationPermission,
    LockedDirectPermission,
    LockedMember,
    LockedTeamMembership,
    MemberSnapshot,
    Page,
    ResourceAccessProjection,
    ResourceDescriptor,
    ResourceType,
    TeamMembershipProjection,
)


class OrganizationAuthorizationPort(Protocol):
    def is_organization_manager(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> bool: ...

    def has_organization_scope(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> bool: ...


class ActorAccessQueryPort(Protocol):
    def get_member(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> MemberSnapshot | None: ...

    def count_active_managers(self, organization_id: uuid.UUID) -> int: ...

    def count_team_memberships(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> int: ...

    def get_app_creation_permission(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> AppCreationPermissionSnapshot | None: ...

    def count_direct_permissions(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> int: ...

    def count_team_permission_sources(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> int: ...

    def list_team_memberships(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        team_id: uuid.UUID | None,
        page: int,
        limit: int,
    ) -> Page[TeamMembershipProjection]: ...

    def list_resource_access(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        resource_type: ResourceType,
        resource_id: uuid.UUID | None,
        source: str,
        page: int,
        limit: int,
    ) -> Page[ResourceAccessProjection]: ...


class MembershipPort(Protocol):
    def lock_member(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        manager_reduction: bool,
    ) -> LockedMember | None: ...

    def set_membership_state(
        self, state: str
    ) -> AppliedMutationDescriptor: ...

    def set_organization_role(self, role: str) -> AppliedMutationDescriptor: ...


class TeamMembershipPort(Protocol):
    def lock_team_membership(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        team_id: uuid.UUID,
        *,
        require_active_team: bool,
    ) -> LockedTeamMembership | None: ...

    def add_team_membership(
        self, actor_id: uuid.UUID
    ) -> AppliedMutationDescriptor: ...

    def remove_team_membership(self) -> AppliedMutationDescriptor: ...


class ResourceScopePort(Protocol):
    def lock_resource(
        self,
        organization_id: uuid.UUID,
        resource_type: ResourceType,
        resource_id: uuid.UUID,
    ) -> ResourceDescriptor | None: ...


class DirectPermissionPort(Protocol):
    def lock_direct_permission(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        resource: ResourceDescriptor,
    ) -> LockedDirectPermission: ...

    def grant_direct_permission(
        self,
        actor_id: uuid.UUID,
        auth_state: str,
    ) -> AppliedMutationDescriptor: ...

    def revoke_direct_permission(self) -> AppliedMutationDescriptor: ...


class AppCreationPermissionPort(Protocol):
    def lock_app_creation_permission(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> LockedAppCreationPermission: ...

    def grant_app_creation_permission(
        self, actor_id: uuid.UUID
    ) -> AppliedMutationDescriptor: ...

    def revoke_app_creation_permission(self) -> AppliedMutationDescriptor: ...


class AuditRecorderPort(Protocol):
    def record_mutation(
        self,
        descriptor: AppliedMutationDescriptor,
        command: AccessActionCommand,
        reason: str | None,
    ) -> None: ...

    def record_policy_block(
        self,
        command: AccessActionCommand,
        *,
        membership_id: uuid.UUID,
        policy_reason: str,
        reason: str | None,
    ) -> None: ...


class PermissionDenialPort(Protocol):
    def record_permission_denied(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> None: ...


class ReasonSanitizerPort(Protocol):
    def sanitize(self, reason: str | None) -> str | None: ...


class UnitOfWork(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
