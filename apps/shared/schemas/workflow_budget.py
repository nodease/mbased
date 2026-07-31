from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MONTHLY_BUDGET_USD = Decimal("9999999999.99")


class WorkflowBudgetUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_budget_usd: Decimal = Field(
        gt=0,
        le=MAX_MONTHLY_BUDGET_USD,
    )
    is_enabled: bool

    @field_validator("monthly_budget_usd")
    @classmethod
    def validate_budget_scale(cls, value: Decimal) -> Decimal:
        if value.as_tuple().exponent < -2:
            raise ValueError("monthly_budget_usd supports at most 2 decimal places")
        return value


class WorkflowBudgetResponse(BaseModel):
    workflow_id: UUID
    workflow_name: str
    monthly_budget_usd: float
    is_enabled: bool
    created_by: UUID | None = None
    updated_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
    current_month_cost: float | None = None
    usage_ratio: float | None = None
    status: str | None = None
    usage_data_complete: bool | None = None
    unresolved_provider_call_count: int | None = None


class WorkflowBudgetListResponse(BaseModel):
    total: int
    items: list[WorkflowBudgetResponse]
