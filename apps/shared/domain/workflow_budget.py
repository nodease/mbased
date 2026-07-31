from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

BudgetDecisionStatus = Literal["allowed", "blocked", "unavailable"]
KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True)
class BudgetExecutionDecision:
    status: BudgetDecisionStatus

    @property
    def is_allowed(self) -> bool:
        return self.status == "allowed"


@dataclass(frozen=True, slots=True)
class MonthlyBudgetPeriod:
    start_at: datetime
    end_at: datetime


def resolve_month_period_kst(now: datetime) -> MonthlyBudgetPeriod:
    if now.tzinfo is None or now.utcoffset() is None:
        now = now.replace(tzinfo=KST)
    kst_now = now.astimezone(KST)
    start = kst_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return MonthlyBudgetPeriod(start_at=start, end_at=end)
