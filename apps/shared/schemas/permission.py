from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RESOURCE_AUTH_STATE_RANKS = {
    "workflow": {
        "none": 0,
        "viewer": 1,
        "operator": 2,
        "builder": 3,
        "manager": 4,
    },
    "knowledge_base": {
        "none": 0,
        "viewer": 1,
        "operator": 2,
        "builder": 3,
        "manager": 4,
    },
    "llm_credential": {
        "none": 0,
        "viewer": 1,
        "operator": 2,
        "builder": 3,
        "manager": 4,
    },
    "mail_credential": {
        "none": 0,
        "viewer": 1,
        "operator": 2,
        "builder": 3,
        "manager": 4,
    },
    "audit": {
        "none": 0,
        "auditor": 1,
        "raw_auditor": 2,
        "manager": 3,
    },
}

# auth_state ranks follow docs/data-model/rbac-permission-policy.md resource matrices.
WORKFLOW_AUTH_STATE_RANK = RESOURCE_AUTH_STATE_RANKS["workflow"]
KNOWLEDGE_AUTH_STATE_RANK = RESOURCE_AUTH_STATE_RANKS["knowledge_base"]
LLM_AUTH_STATE_RANK = RESOURCE_AUTH_STATE_RANKS["llm_credential"]
MAIL_AUTH_STATE_RANK = RESOURCE_AUTH_STATE_RANKS["mail_credential"]
AUDIT_AUTH_STATE_RANK = RESOURCE_AUTH_STATE_RANKS["audit"]

WORKFLOW_AUTH_STATES = set(WORKFLOW_AUTH_STATE_RANK)
KNOWLEDGE_AUTH_STATES = set(KNOWLEDGE_AUTH_STATE_RANK)
KNOWLEDGE_DIRECT_GRANT_AUTH_STATES = KNOWLEDGE_AUTH_STATES - {"none"}
LLM_AUTH_STATES = set(LLM_AUTH_STATE_RANK)
MAIL_AUTH_STATES = set(MAIL_AUTH_STATE_RANK)
AUDIT_AUTH_STATES = set(AUDIT_AUTH_STATE_RANK)
MAX_BULK_PERMISSION_GRANTS = 50


def _normalize_auth_state(value: str, allowed_states: set[str]) -> str:
    """Normalize and validate an auth_state value against one resource matrix."""
    normalized = value.strip().lower()
    if normalized not in allowed_states:
        allowed = ", ".join(sorted(allowed_states))
        raise ValueError(f"auth_state must be one of: {allowed}")
    return normalized


class WorkflowPermissionGrantRequest(BaseModel):
    auth_state: str

    @field_validator("auth_state")
    @classmethod
    def validate_workflow_auth_state(cls, value: str) -> str:
        return _normalize_auth_state(value, WORKFLOW_AUTH_STATES)


class KnowledgePermissionGrantRequest(BaseModel):
    auth_state: str

    @field_validator("auth_state")
    @classmethod
    def validate_knowledge_auth_state(cls, value: str) -> str:
        return _normalize_auth_state(value, KNOWLEDGE_AUTH_STATES)


class KnowledgeDirectPermissionGrantRequest(BaseModel):
    auth_state: str

    @field_validator("auth_state")
    @classmethod
    def validate_knowledge_direct_auth_state(cls, value: str) -> str:
        return _normalize_auth_state(value, KNOWLEDGE_DIRECT_GRANT_AUTH_STATES)


class LLMPermissionGrantRequest(BaseModel):
    auth_state: str

    @field_validator("auth_state")
    @classmethod
    def validate_llm_auth_state(cls, value: str) -> str:
        return _normalize_auth_state(value, LLM_AUTH_STATES)


class AuditPermissionGrantRequest(BaseModel):
    auth_state: str

    @field_validator("auth_state")
    @classmethod
    def validate_audit_auth_state(cls, value: str) -> str:
        return _normalize_auth_state(value, AUDIT_AUTH_STATES)


class PermissionGrantRequest(WorkflowPermissionGrantRequest):
    """Backward-compatible alias for workflow permission grant requests."""


class BulkPermissionGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: Literal[
        "workflow", "knowledge_base", "llm_credential", "mail_credential"
    ]
    resource_ids: list[UUID] = Field(..., min_length=1, max_length=50)
    grantee_type: Literal["team", "user"]
    grantee_ids: list[UUID] = Field(..., min_length=1, max_length=50)
    auth_state: str

    @field_validator("resource_ids", "grantee_ids")
    @classmethod
    def validate_unique_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("bulk permission ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_grant_matrix(self) -> "BulkPermissionGrantRequest":
        if len(self.resource_ids) * len(self.grantee_ids) > MAX_BULK_PERMISSION_GRANTS:
            raise ValueError(
                f"bulk permission grant supports at most "
                f"{MAX_BULK_PERMISSION_GRANTS} resource-grantee pairs"
            )
        allowed_states = set(RESOURCE_AUTH_STATE_RANKS[self.resource_type]) - {"none"}
        self.auth_state = _normalize_auth_state(self.auth_state, allowed_states)
        return self


class BulkPermissionGrantResponse(BaseModel):
    resource_type: Literal[
        "workflow", "knowledge_base", "llm_credential", "mail_credential"
    ]
    grantee_type: Literal["team", "user"]
    resource_count: int = Field(ge=1, le=50)
    grantee_count: int = Field(ge=1, le=50)
    grant_count: int = Field(ge=1, le=MAX_BULK_PERMISSION_GRANTS)


class WorkflowPermissionSource(BaseModel):
    """현재 user의 workflow effective permission 출처."""

    type: Literal["team", "user"]
    auth_state: str
    team_id: UUID | None = None
    team_name: str | None = None
    user_id: UUID | None = None
    user_name: str | None = None

    @model_validator(mode="after")
    def validate_source_identity(self) -> "WorkflowPermissionSource":
        if self.type == "team" and (self.team_id is None or not self.team_name):
            raise ValueError("team source requires team_id and team_name")
        if self.type == "user" and self.user_id is None:
            raise ValueError("user source requires user_id")
        return self


class WorkflowPermissionResponse(BaseModel):
    workflow_id: UUID
    organization_id: UUID | None = None
    auth_state: str
    can_read: bool
    can_write: bool
    can_execute: bool
    can_deploy: bool
    can_manage: bool
    sources: list[WorkflowPermissionSource] = Field(default_factory=list)


class TeamWorkflowPermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    grantee_organization_id: UUID
    workflow_id: UUID
    team_id: UUID
    auth_state: str
    assigned_by: UUID
    assigned_at: datetime
    options: dict[str, Any]
    flags: int


class TeamLLMPermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    grantee_organization_id: UUID
    llm_credential_id: UUID
    team_id: UUID
    auth_state: str
    assigned_by: UUID
    assigned_at: datetime
    options: dict[str, Any]
    flags: int


class TeamKnowledgePermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    grantee_organization_id: UUID
    knowledge_base_id: UUID
    team_id: UUID
    auth_state: str
    assigned_by: UUID
    assigned_at: datetime
    options: dict[str, Any]
    flags: int


class UserWorkflowPermissionResponse(BaseModel):
    """user direct workflow permission upsert 응답 schema."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    grantee_organization_id: UUID
    workflow_id: UUID
    user_id: UUID
    auth_state: str
    assigned_by: UUID
    assigned_at: datetime
    options: dict[str, Any]
    flags: int


class UserKnowledgePermissionResponse(BaseModel):
    """user direct Knowledge Base permission upsert 응답 schema."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    grantee_organization_id: UUID
    knowledge_base_id: UUID
    user_id: UUID
    auth_state: str
    assigned_by: UUID
    assigned_at: datetime
    options: dict[str, Any]
    flags: int


class UserLLMPermissionResponse(BaseModel):
    """user direct LLM credential permission upsert 응답 schema."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    grantee_organization_id: UUID
    llm_credential_id: UUID
    user_id: UUID
    auth_state: str
    assigned_by: UUID
    assigned_at: datetime
    options: dict[str, Any]
    flags: int
