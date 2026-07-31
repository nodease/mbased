from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Literal, TypeVar

MembershipState = Literal["active", "suspended"]
OrganizationAuthState = Literal["member", "manager"]
ResourceType = Literal[
    "workflow",
    "knowledge_base",
    "llm_credential",
    "mail_credential",
]
ResourceAuthState = Literal["none", "viewer", "operator", "builder", "manager"]
AccessActionStatus = Literal["applied", "unchanged"]


@dataclass(frozen=True)
class MemberSnapshot:
    membership_id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    email: str
    user_active: bool
    membership_state: MembershipState
    organization_auth_state: OrganizationAuthState
    updated_at: datetime

    @property
    def effective_access_enabled(self) -> bool:
        return self.user_active and self.membership_state == "active"

    @property
    def manager_override(self) -> bool:
        return (
            self.effective_access_enabled and self.organization_auth_state == "manager"
        )


@dataclass(frozen=True)
class AccessControl:
    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class OrganizationRoleControls:
    member: AccessControl
    manager: AccessControl


@dataclass(frozen=True)
class MemberAccessControls:
    membership_suspend: AccessControl
    membership_reactivate: AccessControl
    organization_role_set: OrganizationRoleControls
    team_membership_add: AccessControl
    team_membership_remove: AccessControl
    direct_permission_grant: AccessControl
    direct_permission_revoke: AccessControl
    app_creation_grant: AccessControl
    app_creation_revoke: AccessControl


@dataclass(frozen=True)
class MemberAccessControlSummary:
    is_self: bool
    is_last_active_manager: bool
    manager_override: bool
    actions: MemberAccessControls


@dataclass(frozen=True)
class AppCreationPermissionSnapshot:
    permission_id: uuid.UUID
    assigned_at: datetime


@dataclass(frozen=True)
class AppCreationAccess:
    effective: bool
    effective_source: Literal["manager_override", "direct", "none"]
    direct_permission: AppCreationPermissionSnapshot | None


@dataclass(frozen=True)
class PermissionCounts:
    direct: int
    team_inherited: int


@dataclass(frozen=True)
class MemberAccessProfile:
    member: MemberSnapshot
    control: MemberAccessControlSummary
    effective_access_enabled: bool
    team_membership_count: int
    app_creation: AppCreationAccess
    permission_counts: PermissionCounts


@dataclass(frozen=True)
class InheritedResourceCounts:
    workflow: int
    knowledge_base: int
    llm_credential: int
    mail_credential: int = 0

    @property
    def total(self) -> int:
        return (
            self.workflow
            + self.knowledge_base
            + self.llm_credential
            + self.mail_credential
        )


@dataclass(frozen=True)
class TeamMembershipProjection:
    team_membership_id: uuid.UUID
    team_id: uuid.UUID
    name: str
    is_active: bool
    assigned_at: datetime
    inherited_resource_counts: InheritedResourceCounts


@dataclass(frozen=True)
class DirectPermissionSnapshot:
    permission_id: uuid.UUID
    auth_state: ResourceAuthState
    assigned_at: datetime


@dataclass(frozen=True)
class TeamPermissionSource:
    team_membership_id: uuid.UUID
    team_id: uuid.UUID
    team_name: str
    auth_state: ResourceAuthState


@dataclass(frozen=True)
class ResourceAccessProjection:
    resource_type: ResourceType
    resource_id: uuid.UUID
    resource_name: str
    direct_permission: DirectPermissionSnapshot | None
    team_sources: tuple[TeamPermissionSource, ...]


@dataclass(frozen=True)
class MemberResourceAccess:
    resource_type: ResourceType
    resource_id: uuid.UUID
    resource_name: str
    effective_auth_state: ResourceAuthState
    direct_permission: DirectPermissionSnapshot | None
    team_sources: tuple[TeamPermissionSource, ...]


T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    total: int
    items: tuple[T, ...]


@dataclass(frozen=True)
class LockedMember:
    member: MemberSnapshot
    active_manager_count: int | None = None


@dataclass(frozen=True)
class LockedTeamMembership:
    team_id: uuid.UUID
    team_active: bool
    membership_id: uuid.UUID | None
    affected_resource_source_count: int


@dataclass(frozen=True)
class ResourceDescriptor:
    resource_type: ResourceType
    resource_id: uuid.UUID
    resource_name: str


@dataclass(frozen=True)
class LockedDirectPermission:
    resource: ResourceDescriptor
    permission: DirectPermissionSnapshot | None
    strongest_team_auth_state: ResourceAuthState


@dataclass(frozen=True)
class LockedAppCreationPermission:
    permission: AppCreationPermissionSnapshot | None


@dataclass(frozen=True)
class AppliedMutationDescriptor:
    action: str
    category: Literal["action", "data_change"]
    target_type: str
    target_id: uuid.UUID
    before: dict[str, object] | None
    after: dict[str, object] | None
    effective_access_changed: bool | None
    affected_resource_source_count: int | None = None


@dataclass(frozen=True)
class AccessActionCommand:
    actor_id: uuid.UUID
    organization_id: uuid.UUID
    target_user_id: uuid.UUID
    action: str
    expected_membership_id: uuid.UUID
    expected_user_active: bool
    expected_membership_state: MembershipState
    expected_organization_auth_state: OrganizationAuthState
    reason: str | None = None
    role: OrganizationAuthState | None = None
    team_id: uuid.UUID | None = None
    resource_type: ResourceType | None = None
    resource_id: uuid.UUID | None = None
    auth_state: ResourceAuthState | None = None
    expected_absent: bool | None = None
    expected_team_membership_id: uuid.UUID | None = None
    expected_permission_id: uuid.UUID | None = None
    expected_auth_state: ResourceAuthState | None = None


@dataclass(frozen=True)
class AccessActionResult:
    status: AccessActionStatus
    action: str
    target_type: str
    target_id: uuid.UUID | None
    effective_access_changed: bool | None
    affected_resource_source_count: int | None
