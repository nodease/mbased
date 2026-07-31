import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from apps.shared.db.base import Base
from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from apps.shared.db.models.organization_membership import OrganizationMembership


class User(Base):
    """사용자 프로필 테이블 (서비스 전반에서 사용)"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    social_provider: Mapped[str] = mapped_column(String(50), nullable=False)

    social_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )

    avatar_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    deactivated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    organization_memberships: Mapped[list["OrganizationMembership"]] = relationship(
        "OrganizationMembership",
        back_populates="user",
        foreign_keys="OrganizationMembership.user_id",
    )
    invited_organization_memberships: Mapped[list["OrganizationMembership"]] = (
        relationship(
            "OrganizationMembership",
            back_populates="inviter",
            foreign_keys="OrganizationMembership.invited_by",
        )
    )
