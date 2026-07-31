from decimal import Decimal

import pytest
from apps.shared.schemas.workflow_budget import WorkflowBudgetUpsertRequest
from pydantic import ValidationError


def test_workflow_budget_upsert_request_rejects_numeric_12_2_overflow():
    request = WorkflowBudgetUpsertRequest(
        monthly_budget_usd=Decimal("9999999999.99"),
        is_enabled=True,
    )

    assert request.monthly_budget_usd == Decimal("9999999999.99")

    with pytest.raises(ValidationError):
        WorkflowBudgetUpsertRequest(
            monthly_budget_usd=Decimal("10000000000.00"),
            is_enabled=True,
        )


def test_workflow_budget_upsert_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        WorkflowBudgetUpsertRequest(
            monthly_budget_usd=Decimal("100.00"),
            is_enabled=True,
            is_enabledd=False,
        )
