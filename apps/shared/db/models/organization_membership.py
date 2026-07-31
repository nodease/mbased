import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from apps.shared.db.base import Base
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from apps.shared.db.models.organization import Organization
    from apps.shared.db.models.user import User


ORGANIZATION_MEMBERSHIP_INVITED = "invited"
ORGANIZATION_MEMBERSHIP_ACTIVE = "active"
ORGANIZATION_MEMBERSHIP_SUSPENDED = "suspended"
ORGANIZATION_MEMBERSHIP_REMOVED = "removed"

ORGANIZATION_AUTH_MEMBER = "member"
ORGANIZATION_AUTH_MANAGER = "manager"


class OrganizationMembership(Base):
    """Direct membership between a user and an organization."""

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_memberships_organization_user",
        ),
        CheckConstraint(
            "membership_state IN ('invited', 'active', 'suspended', 'removed')",
            name="ck_organization_memberships_membership_state",
        ),
        CheckConstraint(
            "organization_auth_state IN ('member', 'manager')",
            name="ck_organization_memberships_organization_auth_state",
        ),
        CheckConstraint(
            "flags >= 0",
            name="ck_organization_memberships_flags_nonnegative",
        ),
        Index(
            "ix_organization_memberships_organization_id",
            "organization_id",
        ),
        Index("ix_organization_memberships_user_id", "user_id"),
        Index(
            "ix_organization_memberships_membership_state",
            "membership_state",
        ),
        Index(
            "ix_organization_memberships_org_state",
            "organization_id",
            "membership_state",
        ),
        Index(
            "ix_organization_memberships_user_state",
            "user_id",
            "membership_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    membership_state: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ORGANIZATION_MEMBERSHIP_INVITED,
        server_default=text("'invited'"),
    )
    organization_auth_state: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ORGANIZATION_AUTH_MEMBER,
        server_default=text("'member'"),
    )
    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    invited_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    removed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    flags: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="memberships",
        foreign_keys=[organization_id],
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="organization_memberships",
        foreign_keys=[user_id],
    )
    inviter: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="invited_organization_memberships",
        foreign_keys=[invited_by],
    )
