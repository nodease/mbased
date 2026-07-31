import uuid
from datetime import datetime, timezone

from apps.shared.db.base import Base
from apps.shared.domain.mail_processing import (
    MailDraftEffectStatus,
    MailProcessingStatus,
)
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class MailMessageProcessing(Base):
    __tablename__ = "mail_message_processings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["credential_id", "organization_id"],
            ["mail_credentials.id", "mail_credentials.organization_id"],
            name="fk_mail_message_processings_credential_org",
        ),
        UniqueConstraint(
            "organization_id",
            "workflow_id",
            "source_node_id",
            "credential_id",
            "provider",
            "message_identity_hash",
            name="uq_mail_message_processings_logical_message",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ack_pending', 'succeeded', "
            "'failed', 'outcome_unknown')",
            name="ck_mail_message_processings_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_mail_message_processings_attempt_count",
        ),
        CheckConstraint(
            "(lease_owner_hash IS NULL) = (lease_expires_at IS NULL)",
            name="ck_mail_message_processings_lease_pair",
        ),
        CheckConstraint(
            "((status IN ('pending', 'processing', 'ack_pending') "
            "AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'outcome_unknown') "
            "AND completed_at IS NOT NULL "
            "AND lease_owner_hash IS NULL AND lease_expires_at IS NULL))",
            name="ck_mail_message_processings_status_shape",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False, index=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    message_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_source_reference: Mapped[str] = mapped_column(Text, nullable=False)
    source_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MailProcessingStatus.PENDING.value,
        index=True,
    )
    lease_owner_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safe_reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    required_effect_contract_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
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
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    draft_effects: Mapped[list["MailDraftEffect"]] = relationship(
        "MailDraftEffect",
        back_populates="processing",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class MailDraftEffect(Base):
    __tablename__ = "mail_draft_effects"
    __table_args__ = (
        UniqueConstraint(
            "processing_id",
            "node_id",
            "operation_key_hash",
            name="uq_mail_draft_effects_operation",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'succeeded', "
            "'failed_before_effect', 'outcome_unknown', 'exhausted')",
            name="ck_mail_draft_effects_status",
        ),
        CheckConstraint(
            "attempt_count >= 0", name="ck_mail_draft_effects_attempt_count"
        ),
        CheckConstraint(
            "(lease_owner_hash IS NULL) = (lease_expires_at IS NULL)",
            name="ck_mail_draft_effects_lease_pair",
        ),
        CheckConstraint(
            "(encrypted_draft_reference IS NULL AND draft_key_version IS NULL "
            "AND draft_algorithm IS NULL) OR "
            "(encrypted_draft_reference IS NOT NULL AND draft_key_version IS NOT NULL "
            "AND draft_algorithm IS NOT NULL)",
            name="ck_mail_draft_effects_reference_envelope",
        ),
        CheckConstraint(
            "((status = 'pending' AND lease_owner_hash IS NULL "
            "AND completed_at IS NULL AND next_attempt_at IS NULL "
            "AND encrypted_draft_reference IS NULL) OR "
            "(status = 'claimed' AND lease_owner_hash IS NOT NULL "
            "AND completed_at IS NULL AND next_attempt_at IS NULL "
            "AND encrypted_draft_reference IS NULL) OR "
            "(status = 'failed_before_effect' AND lease_owner_hash IS NULL "
            "AND completed_at IS NULL AND next_attempt_at IS NOT NULL "
            "AND encrypted_draft_reference IS NULL) OR "
            "(status = 'succeeded' AND lease_owner_hash IS NULL "
            "AND completed_at IS NOT NULL AND next_attempt_at IS NULL "
            "AND encrypted_draft_reference IS NOT NULL) OR "
            "(status IN ('outcome_unknown', 'exhausted') "
            "AND lease_owner_hash IS NULL AND completed_at IS NOT NULL "
            "AND next_attempt_at IS NULL "
            "AND encrypted_draft_reference IS NULL))",
            name="ck_mail_draft_effects_status_shape",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    processing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mail_message_processings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MailDraftEffectStatus.PENDING.value,
        index=True,
    )
    encrypted_draft_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_key_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    draft_algorithm: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_owner_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safe_reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    processing: Mapped[MailMessageProcessing] = relationship(
        "MailMessageProcessing", back_populates="draft_effects"
    )
