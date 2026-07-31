from datetime import datetime
from typing import Any, List, Literal, Optional
from uuid import UUID

from apps.shared.db.models.audit_log import ActorType, AuditCategory, AuditStatus
from pydantic import BaseModel, ConfigDict, Field


class AuditDisplayReference(BaseModel):
    label: str
    source: Literal["event_snapshot", "current_resource"]


class AuditLogSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    occurred_at: datetime
    actor_id: Optional[UUID]
    actor_type: ActorType
    category: AuditCategory
    action: str
    target_type: Optional[str]
    target_id: Optional[str]
    status: AuditStatus
    request_id: Optional[str] = None


class AuditLogListResponse(BaseModel):
    total: int
    items: List[AuditLogSchema]


class AdminAuditLogSchema(AuditLogSchema):
    workflow_run_id: Optional[UUID] = None
    workflow_node_run_id: Optional[UUID] = None
    actor_display: AuditDisplayReference | None = None
    target_display: AuditDisplayReference | None = None


class AdminAuditLogListResponse(BaseModel):
    total: Optional[int] = None
    next_cursor: Optional[str] = None
    items: List[AdminAuditLogSchema]


class AuditChangeSummary(BaseModel):
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class AuditLogDetailResponse(AdminAuditLogSchema):
    audit_metadata: dict[str, Any] = Field(default_factory=dict)
    change_summary: AuditChangeSummary | None = None
    resolved_references: dict[str, AuditDisplayReference] = Field(
        default_factory=dict
    )
