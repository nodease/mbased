from datetime import datetime, timezone
from uuid import uuid4

from apps.shared.schemas.app import AppIcon, AppOperationAppSummary, AppResponse


def _icon():
    return AppIcon(type="emoji", content="B", background_color="#E0F2FE")


def test_app_response_includes_member_budget_status_only():
    now = datetime.now(timezone.utc)

    response = AppResponse(
        id=uuid4(),
        name="예산 앱",
        description="member budget status",
        icon=_icon(),
        workflow_id=uuid4(),
        is_market=False,
        created_at=now,
        updated_at=now,
        budget_status={"usage_ratio": 0.9, "status": "at_risk"},
    )

    data = response.model_dump()

    assert data["budget_status"] == {"usage_ratio": 0.9, "status": "at_risk"}
    assert "monthly_budget_usd" not in data["budget_status"]
    assert "current_month_cost" not in data["budget_status"]


def test_operation_app_summary_includes_member_budget_status_only():
    now = datetime.now(timezone.utc)

    summary = AppOperationAppSummary(
        id=uuid4(),
        name="운영 앱",
        description="operations budget status",
        icon=_icon(),
        workflow_id=uuid4(),
        owner_name="Builder",
        created_at=now,
        updated_at=now,
        budget_status={"usage_ratio": 1.000001, "status": "exceeded"},
    )

    data = summary.model_dump()

    assert data["budget_status"] == {"usage_ratio": 1.000001, "status": "exceeded"}
    assert "monthly_budget_usd" not in data["budget_status"]
    assert "current_month_cost" not in data["budget_status"]
