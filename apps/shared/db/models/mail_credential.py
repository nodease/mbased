import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from apps.shared.db.base import Base
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from apps.shared.db.models.organization import Organization
    from apps.shared.db.models.user import User


MAIL_CREDENTIAL_ACTIVE = "active"
MAIL_CREDENTIAL_REVOKED = "revoked"
MAIL_CREDENTIAL_STATUSES = {
    MAIL_CREDENTIAL_ACTIVE,
    MAIL_CREDENTIAL_REVOKED,
}


class MailCredential(Base):
    """Organization-scoped Mail provider credential."""

    __tablename__ = "mail_credentials"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_mail_credentials_id_organization_id",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_mail_credentials_status",
        ),
        CheckConstraint(
            "imap_port > 0 AND imap_port <= 65535",
            name="ck_mail_credentials_imap_port",
        ),
        CheckConstraint(
            "(oauth_refresh_lease_owner_hash IS NULL) = "
            "(oauth_refresh_lease_expires_at IS NULL)",
            name="ck_mail_credentials_oauth_refresh_lease_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id"),
        nullable=False,
        index=True,
    )
    credential_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False)
    imap_host: Mapped[str] = mapped_column(String(255), nullable=False)
    imap_port: Mapped[int] = mapped_column(nullable=False, default=993)
    use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    encryption_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MAIL_CREDENTIAL_ACTIVE, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
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
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    oauth_refresh_lease_owner_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    oauth_refresh_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization: Mapped["Organization"] = relationship("Organization")
    creator: Mapped["User"] = relationship("User", foreign_keys=[created_by])
