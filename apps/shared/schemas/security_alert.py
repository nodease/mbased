from datetime import datetime
from typing import Literal
from uuid import UUID

from apps.shared.db.models.audit_log import ActorType, AuditCategory, AuditStatus
from apps.shared.schemas.member_access import normalize_management_reason
from pydantic import BaseModel, ConfigDict, Field, field_validator

SecurityAlertRuleId = Literal[
    "repeated_permission_denied",
    "multi_resource_permission_probe",
    "repeated_policy_block",
]
SecurityAlertSeverity = Literal["medium", "high"]
SecurityAlertStatus = Literal["open", "acknowledged", "resolved"]
SecurityAlertResolutionType = Literal[
    "mitigated", "false_positive", "accepted_risk"
]
SecurityAlertActorState = Literal["active", "suspended", "removed", "deleted"]


class SecurityAlertSafeActor(BaseModel):
    # Lifecycle actor FK는 user 삭제 시 SET NULL이므로 historical projection은
    # ID 없이 deleted 상태만 남을 수 있다. Alert subject actor는 항상 ID가 있다.
    id: UUID | None
    display_name: str | None
    state: SecurityAlertActorState


class SecurityAlertListItem(BaseModel):
    id: UUID
    organization_id: UUID
    rule_id: SecurityAlertRuleId
    rule_version: str
    severity: SecurityAlertSeverity
    status: SecurityAlertStatus
    policy_reason: str | None = None
    actor: SecurityAlertSafeActor
    occurrence_count: int = Field(ge=0)
    first_detected_at: datetime
    last_detected_at: datetime
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class SecurityAlertAcknowledgement(BaseModel):
    by: SecurityAlertSafeActor
    at: datetime


class SecurityAlertResolution(BaseModel):
    type: SecurityAlertResolutionType
    reason: str
    by: SecurityAlertSafeActor
    at: datetime


class SecurityAlertDetail(SecurityAlertListItem):
    evidence_count: int = Field(ge=0)
    acknowledged: SecurityAlertAcknowledgement | None = None
    resolution: SecurityAlertResolution | None = None


class SecurityAlertListResponse(BaseModel):
    total: int = Field(ge=0)
    items: list[SecurityAlertListItem]


class SecurityAlertSummaryItem(BaseModel):
    id: UUID
    rule_id: SecurityAlertRuleId
    severity: SecurityAlertSeverity
    actor: SecurityAlertSafeActor
    occurrence_count: int = Field(ge=0)
    last_detected_at: datetime


class SecurityAlertSummaryResponse(BaseModel):
    open_count: int = Field(ge=0)
    high_open_count: int = Field(ge=0)
    recent_items: list[SecurityAlertSummaryItem] = Field(max_length=5)


class SecurityAlertAuditLogItem(BaseModel):
    id: UUID
    occurred_at: datetime
    actor_id: UUID | None
    actor_type: ActorType
    category: AuditCategory
    action: str
    target_type: str | None
    target_id: str | None
    status: AuditStatus
    request_id: str | None = None
    required_permission: str | None = None
    requested_operation: str | None = None
    denial_reason: str | None = None


class SecurityAlertAuditLogListResponse(BaseModel):
    total: int = Field(ge=0)
    items: list[SecurityAlertAuditLogItem]


class SecurityAlertVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class SecurityAlertAcknowledgeRequest(SecurityAlertVersionRequest):
    pass


class SecurityAlertReopenRequest(SecurityAlertVersionRequest):
    pass


class SecurityAlertResolveRequest(SecurityAlertVersionRequest):
    resolution_type: SecurityAlertResolutionType
    reason: str

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = normalize_management_reason(value)
        if normalized is None:
            raise ValueError("reason must not be blank")
        return normalized
