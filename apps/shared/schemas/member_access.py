from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MembershipState = Literal["active", "suspended"]
OrganizationAuthState = Literal["member", "manager"]
ResourceType = Literal[
    "workflow",
    "knowledge_base",
    "llm_credential",
    "mail_credential",
]
ResourceAuthState = Literal["none", "viewer", "operator", "builder", "manager"]
GrantAuthState = Literal["viewer", "operator", "builder", "manager"]


def normalize_management_reason(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return None
    if len(normalized) > 500:
        raise ValueError("reason must be at most 500 Unicode code points")
    for character in normalized:
        code_point = ord(character)
        if (
            (code_point < 32 and character not in {"\t", "\n"})
            or 127 <= code_point <= 159
            or 0x202A <= code_point <= 0x202E
            or 0x2066 <= code_point <= 0x2069
        ):
            raise ValueError("reason contains a forbidden control character")
    return normalized


class AccessControl(BaseModel):
    allowed: bool
    reason: str | None = None


class OrganizationRoleControls(BaseModel):
    member: AccessControl
    manager: AccessControl


class MemberAccessControls(BaseModel):
    membership_suspend: AccessControl
    membership_reactivate: AccessControl
    organization_role_set: OrganizationRoleControls
    team_membership_add: AccessControl
    team_membership_remove: AccessControl
    direct_permission_grant: AccessControl
    direct_permission_revoke: AccessControl
    app_creation_grant: AccessControl
    app_creation_revoke: AccessControl


class MemberAccessControlSummary(BaseModel):
    is_self: bool
    is_last_active_manager: bool
    manager_override: bool
    actions: MemberAccessControls


class MemberAccessIdentity(BaseModel):
    membership_id: UUID
    user_id: UUID
    name: str
    email: str
    user_active: bool
    membership_state: MembershipState
    organization_auth_state: OrganizationAuthState
    updated_at: datetime


class AppCreationDirectPermission(BaseModel):
    permission_id: UUID
    assigned_at: datetime


class AppCreationAccess(BaseModel):
    effective: bool
    effective_source: Literal["manager_override", "direct", "none"]
    direct_permission: AppCreationDirectPermission | None = None


class PermissionCounts(BaseModel):
    direct: int = Field(ge=0)
    team_inherited: int = Field(ge=0)


class MemberAccessProfileResponse(BaseModel):
    member: MemberAccessIdentity
    control: MemberAccessControlSummary
    effective_access_enabled: bool
    team_membership_count: int = Field(ge=0)
    app_creation: AppCreationAccess
    permission_counts: PermissionCounts


class InheritedResourceCounts(BaseModel):
    workflow: int = Field(ge=0)
    knowledge_base: int = Field(ge=0)
    llm_credential: int = Field(ge=0)
    mail_credential: int = Field(default=0, ge=0)
    total: int = Field(ge=0)


class MemberTeamMembershipItem(BaseModel):
    team_membership_id: UUID
    team_id: UUID
    name: str
    is_active: bool
    assigned_at: datetime
    inherited_resource_counts: InheritedResourceCounts


class MemberTeamMembershipListResponse(BaseModel):
    total: int = Field(ge=0)
    items: list[MemberTeamMembershipItem]


class DirectPermissionSource(BaseModel):
    permission_id: UUID
    auth_state: ResourceAuthState
    assigned_at: datetime


class TeamPermissionSource(BaseModel):
    team_membership_id: UUID
    team_id: UUID
    team_name: str
    auth_state: GrantAuthState


class MemberResourceAccessItem(BaseModel):
    resource_type: ResourceType
    resource_id: UUID
    resource_name: str
    effective_auth_state: ResourceAuthState
    direct_permission: DirectPermissionSource | None = None
    team_sources: list[TeamPermissionSource]


class MemberResourceAccessListResponse(BaseModel):
    total: int = Field(ge=0)
    items: list[MemberResourceAccessItem]


class _AccessActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    expected_membership_id: UUID
    expected_user_active: bool
    expected_membership_state: MembershipState
    expected_organization_auth_state: OrganizationAuthState
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        return normalize_management_reason(value)


class MembershipSuspendAction(_AccessActionBase):
    action: Literal["membership.suspend"]
    expected_membership_state: Literal["active"]


class MembershipReactivateAction(_AccessActionBase):
    action: Literal["membership.reactivate"]
    expected_membership_state: Literal["suspended"]


class OrganizationRoleSetAction(_AccessActionBase):
    action: Literal["organization_role.set"]
    role: OrganizationAuthState


class TeamMembershipAddAction(_AccessActionBase):
    action: Literal["team_membership.add"]
    team_id: UUID
    expected_absent: Literal[True]


class TeamMembershipRemoveAction(_AccessActionBase):
    action: Literal["team_membership.remove"]
    team_id: UUID
    expected_team_membership_id: UUID


class DirectPermissionGrantAction(_AccessActionBase):
    action: Literal["direct_permission.grant"]
    resource_type: ResourceType
    resource_id: UUID
    auth_state: GrantAuthState
    expected_absent: Literal[True] | None = None
    expected_permission_id: UUID | None = None
    expected_auth_state: ResourceAuthState | None = None

    @model_validator(mode="after")
    def validate_precondition(self):
        create = self.expected_absent is True
        update_fields = (
            self.expected_permission_id is not None,
            self.expected_auth_state is not None,
        )
        update = all(update_fields)
        if create == update or any(update_fields) != update:
            raise ValueError(
                "direct grant requires expected_absent=true or both "
                "expected_permission_id and expected_auth_state"
            )
        return self


class DirectPermissionRevokeAction(_AccessActionBase):
    action: Literal["direct_permission.revoke"]
    resource_type: ResourceType
    resource_id: UUID
    expected_permission_id: UUID
    expected_auth_state: ResourceAuthState


class AppCreationGrantAction(_AccessActionBase):
    action: Literal["app_creation.grant"]
    expected_absent: Literal[True]


class AppCreationRevokeAction(_AccessActionBase):
    action: Literal["app_creation.revoke"]
    expected_permission_id: UUID


MemberAccessActionRequest = Annotated[
    MembershipSuspendAction
    | MembershipReactivateAction
    | OrganizationRoleSetAction
    | TeamMembershipAddAction
    | TeamMembershipRemoveAction
    | DirectPermissionGrantAction
    | DirectPermissionRevokeAction
    | AppCreationGrantAction
    | AppCreationRevokeAction,
    Field(discriminator="action"),
]


class MemberAccessActionResponse(BaseModel):
    status: Literal["applied", "unchanged"]
    action: str
    target_type: Literal[
        "organization_membership",
        "team_membership",
        "user_workflow_permission",
        "user_knowledge_permission",
        "user_llm_permission",
        "user_app_creation_permission",
    ]
    target_id: UUID | None
    effective_access_changed: bool | None
    affected_resource_source_count: int | None = Field(default=None, ge=0)
