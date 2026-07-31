from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from apps.shared.schemas.permission import WorkflowPermissionSource
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class AppIcon(BaseModel):
    type: str
    content: str
    background_color: str


class AppCreateRequest(BaseModel):
    """앱 생성 요청 스키마"""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: Optional[str] = None
    icon: AppIcon
    is_market: bool = False


class AppUpdateRequest(BaseModel):
    """앱 수정 요청 스키마"""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[AppIcon] = None
    is_market: Optional[bool] = None


class AppBudgetStatus(BaseModel):
    usage_ratio: float
    status: Literal["normal", "at_risk", "exceeded"]


class AppOperationMetrics(BaseModel):
    current_month_cost: float
    current_month_workflow_execution_cost: float
    current_month_agent_builder_cost: float
    projected_month_cost: Optional[float] = None
    projected_month_workflow_execution_cost: Optional[float] = None
    projected_month_agent_builder_cost: Optional[float] = None
    previous_month_cost: float
    trend_percent: Optional[float] = None
    usage_data_complete: bool = True
    unresolved_provider_call_count: int = Field(default=0, ge=0)


class AppOperationsCostSummary(BaseModel):
    """All readable active deployment workflows for the My Module cost card."""

    active_workflow_count: int = Field(default=0, ge=0)
    projected_month_cost: float = Field(default=0.0, ge=0)
    projected_month_workflow_execution_cost: float = Field(default=0.0, ge=0)
    projected_month_agent_builder_cost: float = Field(default=0.0, ge=0)
    usage_data_complete: bool = True
    unresolved_provider_call_count: int = Field(default=0, ge=0)


class AppResponse(BaseModel):
    """앱 응답 스키마"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: Optional[str]
    icon: AppIcon
    workflow_id: Optional[UUID] = None  # App의 작업실 Workflow
    url_slug: Optional[str] = None  # 첫 배포 시 생성
    is_market: bool
    forked_from: Optional[UUID] = None
    active_deployment_id: Optional[UUID] = None
    active_deployment_type: Optional[str] = None
    active_deployment_is_active: Optional[bool] = None  # 활성 배포의 is_active 상태
    owner_name: Optional[str] = None  # UI 표시용 (생성자 이름)
    budget_status: Optional[AppBudgetStatus] = None
    created_at: datetime
    updated_at: datetime


class AppAuthSecretStatusResponse(BaseModel):
    configured: bool
    version: int = Field(ge=0)
    rotation_enabled: bool = False
    rotated_at: Optional[datetime] = None
    previous_grace_active: bool
    previous_valid_until: Optional[datetime] = None


class AppAuthSecretRotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: StrictInt = Field(ge=0)
    revoke_previous_immediately: StrictBool = False


class AppAuthSecretRotateResponse(BaseModel):
    secret: str = Field(repr=False, min_length=1, max_length=512)
    version: int = Field(ge=1)
    rotated_at: datetime
    previous_grace_active: bool
    previous_valid_until: Optional[datetime] = None


class AppOperationAppSummary(BaseModel):
    """Safe app summary for the module operations list."""

    id: UUID
    name: str
    description: Optional[str] = None
    icon: Optional[AppIcon] = None
    workflow_id: Optional[UUID] = None
    owner_name: Optional[str] = None
    budget_status: Optional[AppBudgetStatus] = None
    operation_metrics: Optional[AppOperationMetrics] = None
    created_at: datetime
    updated_at: datetime


class AppOperationPermissionSummary(BaseModel):
    workflow_id: UUID
    organization_id: Optional[UUID] = None
    auth_state: str
    can_read: bool
    can_write: bool
    can_execute: bool
    can_deploy: bool
    can_manage: bool


class AppOperationDeploymentSummary(BaseModel):
    state: Literal["active", "inactive", "undeployed"]
    deployment_id: Optional[UUID] = None
    type: Optional[str] = None
    is_active: Optional[bool] = None


class AppOperationLatestRunSummary(BaseModel):
    state: Literal["running", "success", "failed", "not_started", "unavailable"]
    run_id: Optional[UUID] = None
    raw_status: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None


class AppOperationAutomaticOptimizationSummary(BaseModel):
    """운영 현황 목록에서만 사용하는 배포별 자동 최적화 safe summary."""

    enabled: bool = False
    status: Literal[
        "disabled", "collecting", "ready", "paused", "budget_exhausted", "failed"
    ] = "disabled"
    node_count: int = 0
    collected_runs: int = 0
    check_every_runs: int = 50
    validation_spend_usd: float = 0.0
    monthly_validation_budget_usd: float = 3.0


class AppOperationRow(BaseModel):
    app: AppOperationAppSummary
    permission: Optional[AppOperationPermissionSummary] = None
    permission_status: Literal["loaded", "failed", "not_available"]
    permission_sources: list[WorkflowPermissionSource] = Field(default_factory=list)
    permission_error: Optional[str] = None
    deployment: AppOperationDeploymentSummary
    latest_run: AppOperationLatestRunSummary
    automatic_optimization: AppOperationAutomaticOptimizationSummary | None = None
