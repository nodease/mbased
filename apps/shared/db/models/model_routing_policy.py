import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

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


class LLMNodeModelRoutingPolicy(Base):
    """배포된 workflow의 LLM node가 사용할 자동 모델 라우팅 정책 snapshot."""

    __tablename__ = "llm_node_model_routing_policies"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "deployment_id",
            "node_id",
            name="uq_model_routing_policy_workflow_deployment_node",
        ),
        Index(
            "ix_model_routing_policy_runtime_lookup",
            "workflow_id",
            "deployment_id",
            "node_id",
            "enabled",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="collecting")
    policy_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    active_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    pending_policy: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    refresh_every_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    eligible_runs_since_last_refresh: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    refresh_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_refreshed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_refresh_result: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    judge_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # 배포 실행자가 실제로 사용할 수 있는 credential/model만 정책에 들어가게 한다.
    execution_subject_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    validation_budget_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=3
    )
    # 자동/직접 등록 입력군이 동시에 늘어나도 runtime catalog가 과도하게 커지지
    # 않도록 policy 단위의 활성 입력군 상한을 보관한다.
    max_cohorts: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class LLMNodeModelRoutingPolicyUpdate(Base):
    """정책 refresh 시도와 judge safe summary를 남기는 감사 가능한 이력."""

    __tablename__ = "llm_node_model_routing_policy_updates"
    __table_args__ = (
        Index("ix_model_routing_policy_update_policy_created", "policy_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    eligible_run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_reason_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    judge_provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    judge_model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    judge_usage_log_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("llm_usage_logs.id", ondelete="SET NULL"), nullable=True
    )
    prompt_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    input_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    new_policy_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class LLMNodeModelRoutingPolicyRunEvent(Base):
    """정책 갱신 카운터에 반영된 배포 후 운영 run의 중복 방지 event."""

    __tablename__ = "llm_node_model_routing_policy_run_events"
    __table_args__ = (
        UniqueConstraint(
            "policy_id",
            "workflow_run_id",
            name="uq_model_routing_policy_run_event",
        ),
        Index("ix_model_routing_policy_run_event_policy_created", "policy_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
