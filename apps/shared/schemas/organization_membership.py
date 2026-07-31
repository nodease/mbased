from datetime import datetime
from uuid import UUID

from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MANAGER,
    ORGANIZATION_AUTH_MEMBER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    ORGANIZATION_MEMBERSHIP_INVITED,
    ORGANIZATION_MEMBERSHIP_REMOVED,
    ORGANIZATION_MEMBERSHIP_SUSPENDED,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

ORGANIZATION_MEMBERSHIP_STATES = {
    ORGANIZATION_MEMBERSHIP_INVITED,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    ORGANIZATION_MEMBERSHIP_SUSPENDED,
    ORGANIZATION_MEMBERSHIP_REMOVED,
}
ORGANIZATION_PATCH_MEMBERSHIP_STATES = {
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    ORGANIZATION_MEMBERSHIP_SUSPENDED,
}
ORGANIZATION_AUTH_STATES = {
    ORGANIZATION_AUTH_MEMBER,
    ORGANIZATION_AUTH_MANAGER,
}


def _normalize_state(
    value: str | None,
    allowed_states: set[str],
    field_name: str,
) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in allowed_states:
        allowed = ", ".join(sorted(allowed_states))
        raise ValueError(f"{field_name} must be one of: {allowed}")
    return normalized


def _validate_membership_state(value: str | None) -> str | None:
    return _normalize_state(value, ORGANIZATION_MEMBERSHIP_STATES, "membership_state")


def _validate_patch_membership_state(value: str | None) -> str | None:
    return _normalize_state(
        value, ORGANIZATION_PATCH_MEMBERSHIP_STATES, "membership_state"
    )


def _validate_organization_auth_state(value: str | None) -> str | None:
    return _normalize_state(value, ORGANIZATION_AUTH_STATES, "organization_auth_state")


class OrganizationMemberInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    user_id: UUID
    organization_auth_state: str = ORGANIZATION_AUTH_MEMBER

    @field_validator("organization_auth_state")
    @classmethod
    def validate_organization_auth_state(cls, value: str) -> str:
        return _validate_organization_auth_state(value) or ORGANIZATION_AUTH_MEMBER


class OrganizationMemberUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_state: str | None = None
    organization_auth_state: str | None = None

    @field_validator("membership_state")
    @classmethod
    def validate_membership_state(cls, value: str | None) -> str | None:
        return _validate_patch_membership_state(value)

    @field_validator("organization_auth_state")
    @classmethod
    def validate_organization_auth_state(cls, value: str | None) -> str | None:
        return _validate_organization_auth_state(value)


class OrganizationMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    user_id: UUID
    user_email: str
    user_name: str
    membership_state: str
    organization_auth_state: str
    invited_by: UUID | None
    invited_at: datetime | None
    accepted_at: datetime | None
    removed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("membership_state")
    @classmethod
    def validate_membership_state(cls, value: str) -> str:
        return _validate_membership_state(value) or value

    @field_validator("organization_auth_state")
    @classmethod
    def validate_organization_auth_state(cls, value: str) -> str:
        return _validate_organization_auth_state(value) or value


class MemberCurrentMonthUsage(BaseModel):
    total_cost: float = Field(default=0.0, ge=0)
    workflow_execution_cost: float = Field(default=0.0, ge=0)
    agent_builder_cost: float = Field(default=0.0, ge=0)
    usage_data_complete: bool = True
    unresolved_provider_call_count: int = Field(default=0, ge=0)


class OrganizationMemberListItemResponse(OrganizationMemberResponse):
    current_month_usage: MemberCurrentMonthUsage


class OrganizationSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    membership_state: str
    organization_auth_state: str
    is_active: bool

    @field_validator("membership_state")
    @classmethod
    def validate_membership_state(cls, value: str) -> str:
        return _validate_membership_state(value) or value

    @field_validator("organization_auth_state")
    @classmethod
    def validate_organization_auth_state(cls, value: str) -> str:
        return _validate_organization_auth_state(value) or value


class OrganizationCurrentResponse(OrganizationSummaryResponse):
    pass


class RevokedUserPermissionCounts(BaseModel):
    workflow: int = Field(default=0, ge=0)
    llm_credential: int = Field(default=0, ge=0)
    mail_credential: int = Field(default=0, ge=0)
    app_creation: int = Field(default=0, ge=0)
    knowledge_base: int = Field(default=0, ge=0)
    audit: int = Field(default=0, ge=0)


class OrganizationMemberRemoveResponse(BaseModel):
    status: str
    removed_team_memberships: int = Field(default=0, ge=0)
    revoked_user_permissions: RevokedUserPermissionCounts
