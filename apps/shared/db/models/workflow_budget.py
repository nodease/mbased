import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from apps.shared.db.base import Base
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


class WorkflowBudget(Base):
    """workflow 단위 월간 LLM 예산 (docs/features/budget-management).

    workflow당 최대 1 row로 최신 설정만 유지한다. 비활성화는 row 삭제가 아니라
    is_enabled=false로 표현한다. organization_id는 대상 workflow의
    organization_id와 composite FK로 정합을 강제한다 (teams 패턴).
    """

    __tablename__ = "workflow_budgets"
    __table_args__ = (
        UniqueConstraint("workflow_id", name="uq_workflow_budgets_workflow_id"),
        # workflow_id의 참조 무결성은 이 composite FK가 담당한다 (단일 컬럼 FK 없음).
        # 두 컬럼 모두 NOT NULL이므로 MATCH SIMPLE에서도 항상 검사된다.
        ForeignKeyConstraint(
            ["workflow_id", "organization_id"],
            ["workflows.id", "workflows.organization_id"],
            name="fk_workflow_budgets_workflow_org",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "monthly_budget_usd > 0",
            name="ck_workflow_budgets_monthly_budget_usd_positive",
        ),
        CheckConstraint(
            "flags >= 0", name="ck_workflow_budgets_flags_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id"),
        nullable=False,
        index=True,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    monthly_budget_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
    options: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    flags: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
