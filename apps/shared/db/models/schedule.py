"""Schedule Model - 배포된 워크플로우의 스케줄 정보"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from apps.shared.db.base import Base
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


class Schedule(Base):
    """
    배포된 워크플로우의 스케줄 정보를 저장합니다.

    관계:
    - 1 Deployment : 1 Schedule (ScheduleTrigger 노드가 있는 경우)
    - deployment_id는 UNIQUE 제약 조건

    동작 방식:
    - Gateway periodic tick이 active/current Schedule의 persisted cursor를 DB에서 claim한다.
    - 배포 생성/설정 변경 시: typed cron/timezone validation 뒤 next_run_at을 계산한다.
    - 잘못된 legacy configuration은 safe quarantine code로 후보에서 제외한다.
    - 배포 삭제 시: ON DELETE CASCADE로 자동 삭제된다.
    """

    __tablename__ = "schedules"
    __table_args__ = (
        CheckConstraint(
            "configuration_error_code IS NULL OR "
            "configuration_error_code IN ('schedule_configuration_invalid')",
            name="ck_schedules_configuration_error_code",
        ),
    )

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
        index=True,
    )

    # Foreign Key: Deployment (1:1 관계)
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="CASCADE"),
        unique=True,  # 한 배포는 하나의 스케줄만
        nullable=False,
        index=True,
    )

    # ScheduleTriggerNode의 ID (배포 스냅샷 내에서)
    node_id: Mapped[str] = mapped_column(
        String, nullable=False, comment="ScheduleTrigger 노드 ID"
    )

    # Cron 표현식
    cron_expression: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Cron 표현식 (예: '0 9 * * *' = 매일 오전 9시)",
    )

    # 타임존
    timezone: Mapped[str] = mapped_column(
        String, nullable=False, default="UTC", comment="타임존 (예: Asia/Seoul, UTC)"
    )

    # 실행 이력
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="마지막 실행 시간"
    )

    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="다음 실행 예정 시간 (APScheduler가 계산)",
    )

    # Legacy configuration quarantine. Cleared when a valid schedule is saved.
    configuration_error_code: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )

    # 메타데이터
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
