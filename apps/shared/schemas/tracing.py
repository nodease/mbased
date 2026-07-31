from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

ViewLevel = Literal["metadata", "redacted", "raw"]
ScopeType = Literal["global", "organization", "app"]


class TraceSummarySchema(BaseModel):
    id: UUID
    workflow_id: UUID
    app_id: Optional[UUID] = None
    user_id: Optional[UUID]
    deployment_id: Optional[UUID] = None
    status: str
    trigger_mode: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration: Optional[float] = None
    total_tokens: Optional[int] = 0
    total_cost: Optional[float] = 0.0
    redaction_applied: bool = False
    pii_detected: bool = False
    payload_storage_mode: str = "redacted_only"

    class Config:
        from_attributes = True


class TraceListResponse(BaseModel):
    total: int
    items: List[TraceSummarySchema]
    has_more: bool = False
    total_is_estimated: bool = False
    scan_limit_reached: bool = False


class TraceSpanSchema(BaseModel):
    id: UUID
    trace_id: UUID
    node_id: str
    node_type: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration: Optional[float] = None
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Dict[str, Any]] = None
    process_data: Optional[Dict[str, Any]] = None
    trace_metadata: Dict[str, Any] = Field(default_factory=dict)
    redaction_applied: bool = False
    pii_detected: bool = False
    sequence: Optional[int] = None
    retry_count: int = 0


class TracePayloadSchema(BaseModel):
    id: UUID
    trace_id: UUID
    span_id: Optional[UUID] = None
    scope: str
    payload_kind: str
    sequence: Optional[int] = None
    attempt: int = 1
    view: ViewLevel
    payload: Optional[Any] = None
    redaction_applied: bool = False
    pii_detected: bool = False
    secret_detected: bool = False
    redaction_metadata: Optional[Dict[str, Any]] = None
    storage_mode: str
    created_at: datetime


class TraceDetailSchema(TraceSummarySchema):
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    trace_metadata: Dict[str, Any] = Field(default_factory=dict)
    spans: List[TraceSpanSchema] = Field(default_factory=list)
    payloads: List[TracePayloadSchema] = Field(default_factory=list)


class RetentionPurgeRequest(BaseModel):
    scope_type: ScopeType = "global"
    scope_id: Optional[UUID] = None
    dry_run: bool = True
    limit: int = Field(default=1000, ge=1, le=10000)


class RetentionPurgeResponse(BaseModel):
    task_id: Optional[str] = None
    dry_run: bool
    status: str
    result: Optional[Dict[str, Any]] = None


class TraceRedactionPolicyPatch(BaseModel):
    scope_type: ScopeType = "global"
    scope_id: Optional[UUID] = None
    redaction_enabled: Optional[bool] = None
    raw_payload_storage_enabled: Optional[bool] = None
    prompt_completion_storage_enabled: Optional[bool] = None
    pii_detection_enabled: Optional[bool] = None
    store_redacted_copy_only: Optional[bool] = None
    sensitive_headers: Optional[List[str]] = None
    sensitive_json_paths: Optional[List[str]] = None
    sensitive_keywords: Optional[List[str]] = None
    regex_rules: Optional[List[Dict[str, Any]]] = None
    replacement: Optional[str] = None
    is_active: Optional[bool] = None


class TraceRetentionPolicyPatch(BaseModel):
    scope_type: ScopeType = "global"
    scope_id: Optional[UUID] = None
    metadata_retention_days: Optional[int] = Field(default=None, ge=1)
    raw_payload_retention_days: Optional[int] = Field(default=None, ge=1)
    redacted_payload_retention_days: Optional[int] = Field(default=None, ge=1)
    prompt_completion_retention_days: Optional[int] = Field(default=None, ge=1)
    failed_trace_retention_days: Optional[int] = Field(default=None, ge=1)
    retention_action: Optional[Literal["delete", "anonymize", "summarize"]] = None
    is_active: Optional[bool] = None


class TraceVisibilityPolicyPatch(BaseModel):
    scope_type: ScopeType = "global"
    scope_id: Optional[UUID] = None
    owner_trace_access_enabled: Optional[bool] = None
    owner_redacted_payload_access_enabled: Optional[bool] = None
    owner_raw_payload_access_enabled: Optional[bool] = None
    owner_prompt_completion_access_enabled: Optional[bool] = None
    admin_raw_payload_access_enabled: Optional[bool] = None
    admin_prompt_completion_access_enabled: Optional[bool] = None
    deny_owner_trace_access: Optional[bool] = None
    default_view_level: Optional[ViewLevel] = None
    is_active: Optional[bool] = None


class TracePolicyResponse(BaseModel):
    id: Optional[UUID] = None
    scope_type: str
    scope_id: Optional[UUID] = None
    is_active: bool = True
    updated_by: Optional[UUID] = None
    updated_at: Optional[datetime] = None
    values: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        from_attributes = True
