from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AdminUsagePeriodResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    start_at: datetime = Field(alias="startAt")
    end_at: datetime = Field(alias="endAt")


class AdminWorkflowBudgetBlock(BaseModel):
    monthly_budget_usd: float
    current_month_cost: float
    usage_ratio: float
    status: str
    usage_data_complete: bool = True
    unresolved_provider_call_count: int = 0


class AdminBudgetSummaryBlock(BaseModel):
    budgeted_workflow_count: int
    at_risk_count: int
    exceeded_count: int
    ratio: float


class AdminWorkflowUsageItem(BaseModel):
    workflow_id: UUID
    workflow_name: str
    prompt_tokens: int
    completion_tokens: int
    call_count: int
    total_cost: float
    workflow_execution_cost: float
    agent_builder_cost: float
    usage_data_complete: bool = True
    unresolved_provider_call_count: int = 0
    budget: AdminWorkflowBudgetBlock | None = None


class AdminWorkflowUsageResponse(BaseModel):
    total: int
    period: AdminUsagePeriodResponse
    usage_data_complete: bool = True
    unresolved_provider_call_count: int = 0
    items: list[AdminWorkflowUsageItem]


class AdminOrganizationSummaryResponse(BaseModel):
    month: str
    total_cost: float
    workflow_execution_cost: float
    agent_builder_cost: float
    usage_data_complete: bool = True
    unresolved_provider_call_count: int = 0
    budget: AdminBudgetSummaryBlock | None = None
