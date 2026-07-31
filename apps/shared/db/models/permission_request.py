import uuid
from datetime import datetime, timezone
from typing import Optional

from apps.shared.db.base import Base
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

PERMISSION_REQUEST_PENDING = "pending"
PERMISSION_REQUEST_APPROVED = "approved"
PERMISSION_REQUEST_REJECTED = "rejected"

REQUESTED_PERMISSION_APP_CREATE = "app.create"


class PermissionRequest(Base):
    """권한 신청 (ADR-0016). 제출/승인/거절 상태를 보관한다."""

    __tablename__ = "permission_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_permission_requests_status",
        ),
        CheckConstraint(
            "requested_permission IN ('app.create')",
            name="ck_permission_requests_requested_permission",
        ),
        CheckConstraint(
            "flags >= 0",
            name="ck_permission_requests_flags_nonnegative",
        ),
        # 같은 조직에 처리 대기 신청은 1건만 허용한다 (ADR-0016).
        Index(
            "uq_permission_requests_pending",
            "organization_id",
            "user_id",
            "requested_permission",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_permission_requests_organization_status",
            "organization_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    requested_permission: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default=REQUESTED_PERMISSION_APP_CREATE,
        server_default=text("'app.create'"),
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=PERMISSION_REQUEST_PENDING,
        server_default=text("'pending'"),
    )
    decided_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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
