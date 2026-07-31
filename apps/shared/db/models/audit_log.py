import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from apps.shared.db.base import Base
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


class ActorType(str, Enum):
    USER = "user"
    ADMIN = "admin"
    SYSTEM = "system"
    PUBLIC = "public"


class AuditCategory(str, Enum):
    ACTION = "action"  # 사용자 행동 (계층 A)
    DATA_CHANGE = "data_change"  # 데이터 변경 이력 (계층 B)


class AuditStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


def enum_values(enum_cls):
    return [item.value for item in enum_cls]


class AuditLog(Base):
    """
    사용자 작업 감사(Audit) 로그 테이블.

    - 계층 A(action): 엔드포인트에서 발생한 의미 있는 사용자 행동
    - 계층 B(data_change): 추적 대상 모델의 변경 전/후(before/after)

    Append-only로 운영하며(생성만, 수정/삭제 없음), `relationship`은 의도적으로
    생략하고 `actor_id`로 직접 조회한다(기존 Connection/App 컨벤션).
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # 행위 주체. 유저 삭제 시에도 로그는 보존(append-only)되도록 SET NULL.
    # 누가 했는지는 audit_metadata.actor 스냅샷으로 별도 보존한다.
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_type: Mapped[ActorType] = mapped_column(
        SQLEnum(ActorType, name="audit_actor_type", values_callable=enum_values),
        nullable=False,
    )

    category: Mapped[AuditCategory] = mapped_column(
        SQLEnum(AuditCategory, name="audit_category", values_callable=enum_values),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    target_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    before: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Workflow execution correlation. Audit rows outlive execution retention,
    # so referenced run deletion preserves the audit row and clears only the FK.
    workflow_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_node_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_node_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[AuditStatus] = mapped_column(
        SQLEnum(AuditStatus, name="audit_status", values_callable=enum_values),
        nullable=False,
        default=AuditStatus.SUCCESS,
    )

    # ip, user_agent, request_id, actor 스냅샷 등 부가정보
    audit_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_audit_logs_target", "target_type", "target_id"),
        Index("ix_audit_logs_occurred_at_id", "occurred_at", "id"),
        Index("ix_audit_logs_workflow_run_id", "workflow_run_id"),
        Index(
            "ix_audit_logs_workflow_node_run_id",
            "workflow_node_run_id",
        ),
    )


class AuditEventOutbox(Base):
    """Durable handoff for asynchronous ``AuditLog`` persistence."""

    __tablename__ = "audit_event_outbox"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_audit_event_outbox_idempotency_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'leased', 'succeeded', "
            "'retry_scheduled', 'dead_lettered')",
            name="ck_audit_event_outbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_audit_event_outbox_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "max_attempts > 0",
            name="ck_audit_event_outbox_max_attempts_positive",
        ),
        Index(
            "ix_audit_event_outbox_status_retry",
            "status",
            "next_retry_at",
        ),
        Index(
            "ix_audit_event_outbox_lease",
            "status",
            "lease_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default=text("'pending'")
    )
    owner_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retryable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    safe_reason_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dead_lettered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
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
