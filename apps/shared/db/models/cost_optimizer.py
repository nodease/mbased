import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.shared.db.base import Base


class CostOptimizerExperiment(Base):
    """
    특정 workflow LLM node의 A/B 비용 비교 세션을 저장한다.

    실행 원천 데이터는 workflow_runs, workflow_node_runs, llm_usage_logs,
    trace_payloads에 남기고 이 테이블은 baseline과 후보들을 묶는 메타데이터만 가진다.
    """

    __tablename__ = "cost_optimizer_experiments"
    __table_args__ = (
        Index(
            "ix_cost_optimizer_experiments_lookup",
            "workflow_id",
            "node_id",
            "baseline_node_run_id",
            "created_at",
        ),
        Index(
            "ix_cost_optimizer_experiments_user_history",
            "organization_id",
            "created_by",
            "created_at",
        ),
        Index(
            "ix_cost_optimizer_experiments_retention",
            "retention_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organization.id"), nullable=True, index=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    app_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    baseline_node_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_node_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    baseline_workflow_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    baseline_node_options: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    baseline_usage_summary: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    baseline_trace_summary: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    baseline_downstream_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    usage_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
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
    retention_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    candidates: Mapped[list["CostOptimizerCandidate"]] = relationship(
        "CostOptimizerCandidate",
        back_populates="experiment",
        cascade="all, delete-orphan",
    )


class CostOptimizerCandidate(Base):
    """
    하나의 Cost Optimizer experiment 안에서 실행한 B 후보 결과를 저장한다.
    """

    __tablename__ = "cost_optimizer_candidates"
    __table_args__ = (
        Index(
            "ix_cost_optimizer_candidates_experiment_status",
            "experiment_id",
            "status",
            "is_applied",
        ),
        Index(
            "ix_cost_optimizer_candidates_model_created",
            "model_id",
            "created_at",
        ),
        Index(
            "uq_cost_optimizer_candidates_one_applied",
            "experiment_id",
            unique=True,
            postgresql_where=text("is_applied = true"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cost_optimizer_experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    fallback_model_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    task_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    candidate_settings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    candidate_workflow_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    candidate_node_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_node_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    total_cost: Mapped[Optional[float]] = mapped_column(Numeric(12, 6), nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    schema_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    downstream_state: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    usage_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    schema_validation: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    retrieval_summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    downstream_compatibility: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    diff_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    is_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    applied_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applied_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    applied_llm_node_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_node_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
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

    experiment: Mapped["CostOptimizerExperiment"] = relationship(
        "CostOptimizerExperiment", back_populates="candidates"
    )


class CostOptimizerRecommendationVerification(Base):
    """추천 설정 빠른 검증의 멱등 요청과 safe 결과를 보관한다."""

    __tablename__ = "cost_optimizer_recommendation_verifications"
    __table_args__ = (
        UniqueConstraint(
            "created_by",
            "idempotency_key",
            name="uq_cost_optimizer_recommendation_verification_user_key",
        ),
        Index(
            "ix_cost_optimizer_recommendation_verification_lookup",
            "workflow_id",
            "node_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    experiment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cost_optimizer_experiments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    candidate_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cost_optimizer_candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    response_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
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
