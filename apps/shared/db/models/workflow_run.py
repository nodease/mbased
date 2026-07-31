import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from apps.shared.db.base import Base
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    STOPPED = "stopped"


class RunTriggerMode(str, Enum):
    MANUAL = "manual"
    API = "api"
    WEBHOOK = "webhook"
    SCHEDULER = "scheduler"
    APP = "app"


class NodeRunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowRun(Base):
    """
    워크플로우 전체 실행 이력을 저장하는 테이블입니다.
    하나의 워크플로우 실행(Run)은 여러 개의 노드 실행(Node Run)을 포함합니다.
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "user_id IS NOT NULL OR "
            "(trigger_mode = 'SCHEDULER' AND workflow_task_id IS NOT NULL "
            "AND workflow_task_id LIKE 'schedule:%')",
            name="ck_workflow_runs_system_schedule_executor",
        ),
    )

    # === 기본 식별자 ===
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )

    # === 외래 키 (관계) ===
    # 어떤 워크플로우가 실행되었는지
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # System schedule만 nullable. 다른 실행 표면은 application validation에서 user 필수.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    app_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # === 배포 정보 (선택) ===
    # 배포된 버전으로 실행된 경우 연결
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_version: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # 배포 버전 스냅샷

    # === 실행 상태 정보 ===
    # running, success, failed, stopped
    status: Mapped[RunStatus] = mapped_column(
        SQLEnum(RunStatus), nullable=False, index=True
    )
    # manual(수동), api(외부 호출), scheduler(스케줄러) 등
    trigger_mode: Mapped[RunTriggerMode] = mapped_column(
        SQLEnum(RunTriggerMode), nullable=False
    )

    # === 입출력 데이터 (스냅샷) ===
    # 실행 시점의 사용자 입력값 전체
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # 실행 완료 후 최종 결과값
    outputs: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # === 에러 정보 ===
    # 실패 시 에러 메시지
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # === 시간 정보 ===
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 실행 소요 시간 (초 단위) - 편의상 저장
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # === 메타데이터 ===
    # 클라이언트 IP, User Agent 등 추후 확장을 위한 필드
    meta_info: Mapped[Optional[dict]] = mapped_column("meta_info", JSONB, nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    # 챗봇 배포 등 멀티턴 대화에서 방문자별 대화를 격리하기 위한 식별자.
    # 공개 실행은 user_id가 앱 소유자로 고정되므로, 기억 조회는 이 값으로 스코프한다.
    conversation_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    request_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    workflow_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    trace_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    redaction_applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    pii_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    redaction_policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    retention_policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    visibility_policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    payload_storage_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="redacted_only"
    )
    retention_purged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # === 비용/토큰 집계 (Denormalized) ===
    total_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=0
    )
    total_cost: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 6), nullable=True, default=0.0
    )

    # === Relationships ===
    # 1:N 관계 - 하나의 실행은 여러 노드 실행 기록을 가짐
    node_runs: Mapped[List["WorkflowNodeRun"]] = relationship(
        "WorkflowNodeRun", back_populates="workflow_run", cascade="all, delete-orphan"
    )
    trace_payloads: Mapped[List["TracePayload"]] = relationship(
        "TracePayload", back_populates="workflow_run", cascade="all, delete-orphan"
    )

    # LLM 사용 로그와 연동 (1:N) - 하나의 워크플로우 실행에서 여러 번의 LLM 호출이 발생할 수 있음


class WorkflowNodeRun(Base):
    """
    워크플로우 내 개별 노드의 실행 이력을 저장하는 테이블입니다.
    디버깅을 위해 입력값(inputs)과 출력값(outputs)을 상세히 기록합니다.
    """

    __tablename__ = "workflow_node_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # === 외래 키 ===
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # === 노드 정보 ===
    # 워크플로우 그래프 상의 노드 ID (UUID 형식이 아닐 수 있음, 예: 'node-1')
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    # 노드 타입 (예: startNode, llmNode, codeNode 등)
    node_type: Mapped[str] = mapped_column(String, nullable=False)

    # === 실행 상태 ===
    # running, success, failed, skipped
    status: Mapped[NodeRunStatus] = mapped_column(
        SQLEnum(NodeRunStatus), nullable=False, default=NodeRunStatus.RUNNING
    )

    # === 상세 데이터 ===
    # 이 노드에 들어온 입력값
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # 처리 과정 중 추가 데이터 (예: 선택된 분기 핸들 등)
    process_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # 이 노드가 뱉어낸 출력값
    outputs: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # === 에러 정보 ===
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # === 시간 정보 ===
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trace_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    redaction_applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    pii_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    redaction_policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    parent_node_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_node_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sequence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # === Relationships ===
    workflow_run: Mapped["WorkflowRun"] = relationship(
        "WorkflowRun", back_populates="node_runs"
    )
    trace_payloads: Mapped[List["TracePayload"]] = relationship(
        "TracePayload", back_populates="workflow_node_run", cascade="all, delete-orphan"
    )


class TraceRedactionPolicy(Base):
    """전역, 조직, 앱 범위별 추적 페이로드 마스킹 정책."""

    __tablename__ = "trace_redaction_policies"
    __table_args__ = (
        Index(
            "ix_trace_redaction_policies_scope",
            "scope_type",
            "scope_id",
            "is_active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    redaction_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    raw_payload_storage_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    prompt_completion_storage_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    pii_detection_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    store_redacted_copy_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    sensitive_headers: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    sensitive_json_paths: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    sensitive_keywords: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    regex_rules: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    replacement: Mapped[str] = mapped_column(
        String(64), nullable=False, default="[REDACTED]"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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


class TraceRetentionPolicy(Base):
    """추적 메타데이터와 페이로드 보관 정책."""

    __tablename__ = "trace_retention_policies"
    __table_args__ = (
        Index(
            "ix_trace_retention_policies_scope",
            "scope_type",
            "scope_id",
            "is_active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    metadata_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90
    )
    raw_payload_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=7
    )
    redacted_payload_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    prompt_completion_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    failed_trace_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90
    )
    retention_action: Mapped[str] = mapped_column(
        String(32), nullable=False, default="delete"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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


class TraceVisibilityPolicy(Base):
    """앱 소유자와 시스템 관리자를 위한 추적 표시 정책."""

    __tablename__ = "trace_visibility_policies"
    __table_args__ = (
        Index(
            "ix_trace_visibility_policies_scope",
            "scope_type",
            "scope_id",
            "is_active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    owner_trace_access_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    owner_redacted_payload_access_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    owner_raw_payload_access_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    owner_prompt_completion_access_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    admin_raw_payload_access_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    admin_prompt_completion_access_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    deny_owner_trace_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    default_view_level: Mapped[str] = mapped_column(
        String(32), nullable=False, default="metadata"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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


class TracePayload(Base):
    """추가 전용 추적/스팬 페이로드 저장소."""

    __tablename__ = "trace_payloads"
    __table_args__ = (
        Index(
            "ix_trace_payloads_latest_view",
            "workflow_run_id",
            "scope",
            "workflow_node_run_id",
            "payload_kind",
            "created_at",
            "sequence",
            "attempt",
        ),
        Index(
            "ix_trace_payloads_retention_scan",
            "retention_purged_at",
            "payload_kind",
            "created_at",
            "retention_expires_at",
        ),
        Index(
            "ix_trace_payloads_raw_retention",
            "created_at",
            postgresql_where=text("raw_payload_encrypted IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_node_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_node_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    payload_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sequence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    redacted_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    raw_payload_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    redaction_applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    pii_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    secret_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    redaction_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    storage_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="redacted_only"
    )
    retention_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    retention_purged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    workflow_run: Mapped["WorkflowRun"] = relationship(
        "WorkflowRun", back_populates="trace_payloads"
    )
    workflow_node_run: Mapped[Optional["WorkflowNodeRun"]] = relationship(
        "WorkflowNodeRun", back_populates="trace_payloads"
    )


class TracePayloadAccessEvent(Base):
    """원문 페이로드 조회 시도를 남기기 위한 최소 접근 이벤트 모델."""

    __tablename__ = "trace_payload_access_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    payload_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("trace_payloads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_ref: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    view_level: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
