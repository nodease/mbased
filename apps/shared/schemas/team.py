from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TeamCreateRequest(BaseModel):
    organization_id: UUID
    name: str
    description: str | None = None
    is_auto_add: bool = False


class TeamCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    is_auto_add: bool = False


class TeamUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    managed_by: UUID | None = None
    is_auto_add: bool | None = None


class TeamMembershipRequest(BaseModel):
    user_id: UUID


class TeamMemberResponse(BaseModel):
    id: UUID
    user_id: UUID
    email: str
    name: str
    assigned_at: datetime


class ResourceAuthStateRequest(BaseModel):
    auth_state: str


class ResourcePermissionGrantRequest(BaseModel):
    organization_id: UUID
    resource_type: Literal[
        "workflow", "knowledge_base", "llm_credential", "mail_credential"
    ]
    resource_id: UUID
    grantee_type: Literal["team", "user"]
    grantee_id: UUID
    auth_state: str


class ResourcePermissionRevokeRequest(BaseModel):
    organization_id: UUID
    resource_type: Literal[
        "workflow", "knowledge_base", "llm_credential", "mail_credential"
    ]
    resource_id: UUID
    grantee_type: Literal["team", "user"]
    grantee_id: UUID


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    name: str
    description: str | None
    options: dict[str, Any]
    flags: int
    created_by: UUID
    managed_by: UUID | None
    is_active: bool
    is_auto_add: bool
    created_at: datetime
    updated_at: datetime
    deactivated_at: datetime | None


class PermissionMutationResponse(BaseModel):
    id: UUID | None = None
    status: str


class ResourcePermissionEntry(BaseModel):
    id: UUID
    grantee_type: Literal["team", "user"]
    grantee_id: UUID
    grantee_name: str
    auth_state: str
    assigned_at: datetime


class ResourcePermissionListResponse(BaseModel):
    resource_type: Literal[
        "workflow", "knowledge_base", "llm_credential", "mail_credential"
    ]
    resource_id: UUID
    organization_id: UUID
    team_permissions: list[ResourcePermissionEntry]
    user_permissions: list[ResourcePermissionEntry]
