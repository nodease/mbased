from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.domain.workflow_node_binding import strip_workflow_node_bindings
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    field_serializer,
    field_validator,
)

DeploymentPreflightStatus = Literal["passed", "warning", "blocked"]
DeploymentPreflightAudience = Literal[
    "anonymous_public",
    "authenticated_user",
    "workflow_node_inherited",
]
BrowserAccessContractVersion = Literal["deployment_browser_access.v1"]


class WorkflowNodeContainerPathSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["loop"]
    node_id: StrictStr = Field(min_length=1, max_length=255)


class DeploymentBrowserEmbeddingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool
    parent_origins: list[StrictStr] = Field(default_factory=list)


class DeploymentBrowserAccessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: BrowserAccessContractVersion
    embedding: DeploymentBrowserEmbeddingPolicy


class DeploymentBrowserAccessRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    browser_access_policy: DeploymentBrowserAccessPolicy
    is_active: StrictBool = False


class DeploymentBrowserAccessProjectionEmbedding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool
    frame_ancestors: list[StrictStr] = Field(default_factory=list, max_length=20)


class DeploymentBrowserAccessProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: BrowserAccessContractVersion
    deployment_version: int = Field(ge=1)
    embedding: DeploymentBrowserAccessProjectionEmbedding


class DeploymentParameterOptimizationConfig(BaseModel):
    """배포 후 LLM 파라미터 자동 최적화의 안전한 범위 설정."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    node_ids: list[str] = Field(default_factory=list)
    check_every_runs: int = Field(default=50, ge=20, le=200)
    monthly_validation_budget_usd: float = Field(default=3.0, ge=0.5, le=10.0)


class DeploymentParameterOptimizationStatus(BaseModel):
    enabled: bool = False
    status: Literal[
        "disabled", "collecting", "ready", "paused", "budget_exhausted", "failed"
    ] = "disabled"
    node_ids: list[str] = Field(default_factory=list)
    node_count: int = 0
    collected_runs: int = 0
    check_every_runs: int = 50
    validation_spend_usd: float = 0.0
    monthly_validation_budget_usd: float = 3.0


class DeploymentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: DeploymentType = DeploymentType.API
    url_slug: Optional[str] = Field(
        None, max_length=255, pattern=r"^[a-z0-9-]+$"
    )  # 소문자, 숫자, 하이픈만 허용
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = {}
    parameter_optimization: DeploymentParameterOptimizationConfig | None = None
    is_active: bool = True
    browser_access_policy: Optional[DeploymentBrowserAccessPolicy] = None


class DeploymentCreate(DeploymentBase):
    app_id: UUID  # App ID
    # TODO: 프론트엔드에서 localStorage에 저장된 스냅샷을 보내주는 방식으로 변경
    # 현재는 백엔드에서 DB의 draft를 읽어서 저장함
    graph_snapshot: Optional[Dict[str, Any]] = None


class DeploymentPreflightRequest(DeploymentBase):
    app_id: UUID
    graph_snapshot: Optional[Dict[str, Any]] = None
    audience: Optional[DeploymentPreflightAudience] = None


class DeploymentPreflightSummary(BaseModel):
    blocked_reason: Optional[str] = None
    affected_node_count: int = 0
    affected_kb_count_bucket: str = "0"
    affected_collection_count_bucket: str = "0"
    candidate_budget_limited: bool = False


class DeploymentPreflightRequiredAction(BaseModel):
    action: str
    label: str


class DeploymentPreflightNodeResult(BaseModel):
    node_id: Optional[str] = None
    node_type: str
    status: DeploymentPreflightStatus
    reason_codes: list[str] = Field(default_factory=list)
    knowledge_base_count_bucket: str = "0"
    knowledge_collection_count_bucket: str = "0"
    candidate_budget_limited: bool = False


class DeploymentPreflightResponse(BaseModel):
    status: DeploymentPreflightStatus
    audience: DeploymentPreflightAudience
    safe_summary: DeploymentPreflightSummary = Field(
        default_factory=DeploymentPreflightSummary
    )
    required_actions: list[DeploymentPreflightRequiredAction] = Field(
        default_factory=list
    )
    warnings: list[str] = Field(default_factory=list)
    nodes: list[DeploymentPreflightNodeResult] = Field(default_factory=list)
    normalized_browser_access_policy: Optional[DeploymentBrowserAccessPolicy] = None


class DeploymentResponse(DeploymentBase):
    id: UUID
    app_id: UUID
    version: int
    created_by: UUID
    created_at: datetime
    graph_snapshot: Dict[str, Any]
    input_schema: Optional[Dict[str, Any]] = None  # StartNode 입력 스키마
    output_schema: Optional[Dict[str, Any]] = None  # AnswerNode 출력 스키마

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("browser_access_policy", mode="before")
    @classmethod
    def normalize_malformed_persisted_browser_access_policy(cls, value):
        if value is None or isinstance(value, DeploymentBrowserAccessPolicy):
            return value
        try:
            return DeploymentBrowserAccessPolicy.model_validate(value)
        except ValidationError:
            return {
                "contract_version": "deployment_browser_access.v1",
                "embedding": {
                    "enabled": False,
                    "parent_origins": [],
                },
            }

    @field_serializer("graph_snapshot")
    def serialize_graph_snapshot(self, value: Dict[str, Any]) -> Dict[str, Any]:
        from apps.shared.services.workflow_node_secret_service import (
            redact_legacy_workflow_node_secrets,
        )

        return redact_legacy_workflow_node_secrets(strip_workflow_node_bindings(value))


class DeploymentInfoResponse(BaseModel):
    """공개 배포 정보 응답 (인증 불필요)"""

    url_slug: str
    name: str
    version: int
    description: Optional[str] = None
    type: str
    input_schema: Optional[dict] = None
    output_schema: Optional[dict] = None
    public_conversation_contract: Optional[
        Literal["client_history_v1", "legacy_v0"]
    ] = None


class DeploymentRunInfoResponse(BaseModel):
    """인증된 내부 실행 화면용 safe 배포 정보 응답"""

    deployment_id: UUID
    app_id: UUID
    workflow_id: UUID
    name: str
    version: int
    description: Optional[str] = None
    type: str
    input_schema: Optional[dict] = None
    output_schema: Optional[dict] = None


class DeploymentLLMCredentialPolicyUpsert(BaseModel):
    """Manager-only server policy for one immutable deployment LLM node."""

    model_config = ConfigDict(extra="forbid")

    model_id: UUID
    credential_id: UUID
    purpose: Literal["main_generation", "query_embedding"] = "main_generation"
    container_path: list[WorkflowNodeContainerPathSegment] = Field(
        default_factory=list,
        max_length=16,
    )


class DeploymentLLMCredentialPolicyResponse(BaseModel):
    """Safe projection; it never returns credential config or a principal."""

    id: UUID
    deployment_id: UUID
    deployment_version: int = Field(ge=1)
    node_id: str
    container_path: list[WorkflowNodeContainerPathSegment]
    purpose: Literal["main_generation", "query_embedding"]
    model_id: UUID
    credential_id: UUID
    policy_revision: int = Field(ge=1)
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DeploymentConversationControl(BaseModel):
    """인증 실행의 legacy conversation namespace용 bounded client control."""

    model_config = ConfigDict(extra="forbid")

    client_id: UUID


class AuthenticatedDeploymentRunRequest(BaseModel):
    """인증 배포 실행 요청. Conversation control은 업무 inputs와 분리한다."""

    model_config = ConfigDict(extra="forbid")

    # Endpoint가 기존 400 계약을 유지하며 object 여부를 판정한다.
    inputs: Any = Field(default_factory=dict)
    conversation: Optional[DeploymentConversationControl] = None
