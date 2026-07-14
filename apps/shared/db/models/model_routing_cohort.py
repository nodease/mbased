"""적응형 모델 라우팅 입력군과 검증 증거 저장 모델."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from apps.shared.db.base import Base
from sqlalchemy import (
    Boolean,
    Date,
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


class LLMNodeModelRoutingCohort(Base):
    """Policy별 semantic input cohort의 현재 lifecycle 상태."""

    __tablename__ = "llm_node_model_routing_cohorts"
    __table_args__ = (
        UniqueConstraint("policy_id", "cohort_key", name="uq_model_routing_cohort_key"),
        Index("ix_model_routing_cohort_policy_status", "policy_id", "status"),
        Index("ix_model_routing_cohort_dormant_since", "dormant_since"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    cohort_key: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    label_en: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="auto")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    safety_protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    encoder_model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    centroid_embedding: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_window_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_share_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_traffic_share: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=0
    )
    node_config_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dormant_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class LLMNodeModelRoutingCohortExample(Base):
    """운영 원문이 아닌 합성 대표 질의만 보관한다."""

    __tablename__ = "llm_node_model_routing_cohort_examples"
    __table_args__ = (
        UniqueConstraint("cohort_id", "ordinal", name="uq_model_routing_cohort_example_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_node_model_routing_cohorts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    synthetic_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    embedding: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class LLMNodeModelRoutingObservation(Base):
    """배포 후 성공 LLM node run의 가림 처리된 입력 특징."""

    __tablename__ = "llm_node_model_routing_observations"
    __table_args__ = (
        UniqueConstraint(
            "policy_id",
            "workflow_node_run_id",
            name="uq_model_routing_observation_policy_node_run",
        ),
        Index("ix_model_routing_observation_policy_observed", "policy_id", "observed_at"),
        Index("ix_model_routing_observation_policy_match", "policy_id", "matched_cohort_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_node_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_node_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    review_window_key: Mapped[str] = mapped_column(String(128), nullable=False)
    encoder_model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    matched_cohort_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_node_model_routing_cohorts.id", ondelete="SET NULL"),
        nullable=True,
    )
    match_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unmatched")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class LLMNodeModelRoutingModelEvidence(Base):
    """입력군별 candidate model의 검증된 품질·비용 증거 요약."""

    __tablename__ = "llm_node_model_routing_model_evidence"
    __table_args__ = (
        UniqueConstraint(
            "cohort_id",
            "model_id",
            "node_config_fingerprint",
            name="uq_model_routing_evidence_cohort_model_fingerprint",
        ),
        Index("ix_model_routing_evidence_cohort_status", "cohort_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_node_model_routing_cohorts.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    node_config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="evaluating")
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    efficiency_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_candidate_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class LLMNodeModelRoutingValidationBatch(Base):
    """여러 candidate Replay를 하나의 policy activation 단위로 묶는다."""

    __tablename__ = "llm_node_model_routing_validation_batches"
    __table_args__ = (
        UniqueConstraint("request_fingerprint", name="uq_model_routing_validation_batch_request"),
        Index("ix_model_routing_validation_batch_policy_status", "policy_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    policy_update_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_node_model_routing_policy_updates.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_plan: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=0
    )
    spent_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=0
    )
    error_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class LLMNodeModelRoutingValidationBudgetMonth(Base):
    """Node별 월간 자동 검증 비용 예산과 동시 예약 상태."""

    __tablename__ = "llm_node_model_routing_validation_budget_months"
    __table_args__ = (
        UniqueConstraint("policy_id", "month_start", name="uq_model_routing_budget_month"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    month_start: Mapped[date] = mapped_column(Date, nullable=False)
    limit_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=3
    )
    spent_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=0
    )
    reserved_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=0
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class LLMNodeModelRoutingValidationItem(Base):
    """검증 배치 안의 한 cohort/model/replay 입력 조합."""

    __tablename__ = "llm_node_model_routing_validation_items"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "cohort_id",
            "observation_id",
            "model_id",
            name="uq_model_routing_validation_item_request",
        ),
        Index("ix_model_routing_validation_item_batch_status", "batch_id", "status"),
        Index("ix_model_routing_validation_item_cohort_model", "cohort_id", "model_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_node_model_routing_validation_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_node_model_routing_cohorts.id", ondelete="CASCADE"),
        nullable=False,
    )
    observation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_node_model_routing_observations.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    baseline_model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    candidate_workflow_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    candidate_node_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_node_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    execution_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    quality_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    actual_cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class LLMNodeModelRoutingValidationCostEvent(Base):
    """자동 검증이 실제로 사용한 비용을 batch와 usage log에 연결한다."""

    __tablename__ = "llm_node_model_routing_validation_cost_events"
    __table_args__ = (
        Index("ix_model_routing_validation_cost_batch", "batch_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_node_model_routing_validation_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    usage_log_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("llm_usage_logs.id", ondelete="SET NULL"), nullable=True
    )
    candidate_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cost_optimizer_candidates.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
