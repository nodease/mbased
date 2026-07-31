"""Deployment-scoped LLM parameter optimization policy records."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from apps.shared.db.base import Base
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


class DeploymentParameterOptimizationPlan(Base):
    """Automatic parameter-optimization plan for one deployment snapshot.

    The policy is deliberately separate from model routing. It only controls
    evidence collection and future parameter recommendation validation for the
    deployment snapshot that owns the node.
    """

    __tablename__ = "deployment_parameter_optimization_plans"
    __table_args__ = (
        UniqueConstraint(
            "deployment_id",
            name="uq_deployment_parameter_optimization_plan",
        ),
        Index(
            "ix_deployment_parameter_optimization_plan_lookup",
            "deployment_id",
            "enabled",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    app_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    check_every_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    monthly_validation_budget_usd: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, default=3.0
    )
    validation_spend_usd: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, default=0.0
    )
    validation_spend_month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="collecting")
    active_parameter_patch: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
