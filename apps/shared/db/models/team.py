import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from apps.shared.db.base import Base
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

if TYPE_CHECKING:
    from apps.shared.db.models.knowledge import KnowledgeBase, KnowledgeCollection
    from apps.shared.db.models.llm import LLMCredential
    from apps.shared.db.models.mail_credential import MailCredential
    from apps.shared.db.models.organization import Organization
    from apps.shared.db.models.user import User
    from apps.shared.db.models.workflow import Workflow


class Team(Base):
    """Team owned by one organization."""

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "name",
            name="uq_teams_organization_name",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_teams_id_organization_id",
        ),
        CheckConstraint("flags >= 0", name="ck_teams_flags_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    options: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    flags: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    managed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_auto_add: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    creator: Mapped["User"] = relationship("User", foreign_keys=[created_by])
    manager: Mapped[Optional["User"]] = relationship("User", foreign_keys=[managed_by])


class TeamAssignmentMixin:
    """Common assignment columns for team membership and resource permissions."""

    @declared_attr
    def grantee_organization_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("organization.id"),
            nullable=False,
            index=True,
        )

    @declared_attr
    def team_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("teams.id"),
            nullable=False,
            index=True,
        )

    @declared_attr
    def assigned_by(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("users.id"),
            nullable=False,
            index=True,
        )

    @declared_attr
    def assigned_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(timezone.utc),
        )

    @declared_attr
    def options(cls) -> Mapped[dict]:
        return mapped_column(
            JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
        )

    @declared_attr
    def flags(cls) -> Mapped[int]:
        return mapped_column(
            BigInteger, nullable=False, default=0, server_default=text("0")
        )

    @declared_attr
    def grantee_organization(cls) -> Mapped["Organization"]:
        return relationship(
            "Organization",
            foreign_keys=lambda: [cls.grantee_organization_id],
        )

    @declared_attr
    def team(cls) -> Mapped["Team"]:
        return relationship("Team", foreign_keys=lambda: [cls.team_id])

    @declared_attr
    def assigner(cls) -> Mapped["User"]:
        return relationship("User", foreign_keys=lambda: [cls.assigned_by])


class TeamResourcePermissionMixin(TeamAssignmentMixin):
    """Common permission state for resource-specific team permission tables."""

    @declared_attr
    def auth_state(cls) -> Mapped[str]:
        return mapped_column(String(50), nullable=False, default="none")


class UserResourcePermissionMixin:
    """Common assignment columns for direct user resource permissions."""

    @declared_attr
    def grantee_organization_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("organization.id"),
            nullable=False,
            index=True,
        )

    @declared_attr
    def user_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
        )

    @declared_attr
    def auth_state(cls) -> Mapped[str]:
        return mapped_column(String(50), nullable=False, default="none")

    @declared_attr
    def assigned_by(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("users.id"),
            nullable=False,
            index=True,
        )

    @declared_attr
    def assigned_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(timezone.utc),
        )

    @declared_attr
    def options(cls) -> Mapped[dict]:
        return mapped_column(
            JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
        )

    @declared_attr
    def flags(cls) -> Mapped[int]:
        return mapped_column(
            BigInteger, nullable=False, default=0, server_default=text("0")
        )

    @declared_attr
    def grantee_organization(cls) -> Mapped["Organization"]:
        return relationship(
            "Organization",
            foreign_keys=lambda: [cls.grantee_organization_id],
        )

    @declared_attr
    def user(cls) -> Mapped["User"]:
        return relationship("User", foreign_keys=lambda: [cls.user_id])

    @declared_attr
    def assigner(cls) -> Mapped["User"]:
        return relationship("User", foreign_keys=lambda: [cls.assigned_by])


class TeamMembership(TeamAssignmentMixin, Base):
    """Membership: which user belongs to which team."""

    __tablename__ = "team_memberships"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "team_id",
            name="uq_team_memberships_org_user_team",
        ),
        ForeignKeyConstraint(
            ["team_id", "grantee_organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_memberships_team_org",
        ),
        CheckConstraint("flags >= 0", name="ck_team_memberships_flags_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])


class TeamWorkflowPermission(TeamResourcePermissionMixin, Base):
    """Team permission for a workflow resource."""

    __tablename__ = "team_workflow_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "workflow_id",
            "team_id",
            name="uq_team_workflow_permissions_org_workflow_team",
        ),
        ForeignKeyConstraint(
            ["team_id", "grantee_organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_workflow_permissions_team_org",
        ),
        CheckConstraint(
            "flags >= 0", name="ck_team_workflow_permissions_flags_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    workflow: Mapped["Workflow"] = relationship("Workflow")


class UserWorkflowPermission(UserResourcePermissionMixin, Base):
    """Direct additive user permission for a workflow resource."""

    __tablename__ = "user_workflow_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "workflow_id",
            name="uq_user_workflow_permissions_org_user_workflow",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "grantee_organization_id"],
            ["workflows.id", "workflows.organization_id"],
            name="fk_user_workflow_permissions_workflow_org",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')",
            name="ck_user_workflow_permissions_auth_state",
        ),
        CheckConstraint(
            "flags >= 0", name="ck_user_workflow_permissions_flags_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    # 조직 단독 조회는 아래 unique constraint의 왼쪽 접두어로 처리한다.
    grantee_organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    workflow: Mapped["Workflow"] = relationship(
        "Workflow",
        overlaps="grantee_organization",
    )


class UserLLMPermission(UserResourcePermissionMixin, Base):
    """Direct additive user permission for an LLM credential resource."""

    __tablename__ = "user_llm_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "llm_credential_id",
            name="uq_user_llm_permissions_org_user_credential",
        ),
        ForeignKeyConstraint(
            ["llm_credential_id", "grantee_organization_id"],
            ["llm_credentials.id", "llm_credentials.organization_id"],
            name="fk_user_llm_permissions_credential_org",
        ),
        CheckConstraint(
            "auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')",
            name="ck_user_llm_permissions_auth_state",
        ),
        CheckConstraint("flags >= 0", name="ck_user_llm_permissions_flags_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    llm_credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    llm_credential: Mapped["LLMCredential"] = relationship(
        "LLMCredential",
        overlaps="grantee_organization",
    )


class UserMailCredentialPermission(UserResourcePermissionMixin, Base):
    """Direct additive user permission for a Mail credential resource."""

    __tablename__ = "user_mail_credential_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "mail_credential_id",
            name="uq_user_mail_credential_permissions_org_user_credential",
        ),
        ForeignKeyConstraint(
            ["mail_credential_id", "grantee_organization_id"],
            ["mail_credentials.id", "mail_credentials.organization_id"],
            name="fk_user_mail_credential_permissions_credential_org",
        ),
        CheckConstraint(
            "auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')",
            name="ck_user_mail_credential_permissions_auth_state",
        ),
        CheckConstraint(
            "flags >= 0", name="ck_user_mail_credential_permissions_flags_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    mail_credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    mail_credential: Mapped["MailCredential"] = relationship(
        "MailCredential", overlaps="grantee_organization"
    )


class UserKnowledgePermission(UserResourcePermissionMixin, Base):
    """Direct additive user permission for a knowledge base resource."""

    __tablename__ = "user_knowledge_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "knowledge_base_id",
            name="uq_user_knowledge_permissions_org_user_knowledge",
        ),
        ForeignKeyConstraint(
            ["knowledge_base_id", "grantee_organization_id"],
            ["knowledge_bases.id", "knowledge_bases.organization_id"],
            name="fk_user_knowledge_permissions_knowledge_org",
        ),
        CheckConstraint(
            "auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')",
            name="ck_user_knowledge_permissions_auth_state",
        ),
        CheckConstraint(
            "flags >= 0", name="ck_user_knowledge_permissions_flags_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    knowledge_base: Mapped["KnowledgeBase"] = relationship(
        "KnowledgeBase",
        overlaps="grantee_organization",
    )


class TeamKnowledgePermission(TeamResourcePermissionMixin, Base):
    """Team permission for a knowledge base resource."""

    __tablename__ = "team_knowledge_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "knowledge_base_id",
            "team_id",
            name="uq_team_knowledge_permissions_org_knowledge_team",
        ),
        ForeignKeyConstraint(
            ["team_id", "grantee_organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_knowledge_permissions_team_org",
        ),
        CheckConstraint(
            "flags >= 0", name="ck_team_knowledge_permissions_flags_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id"),
        nullable=False,
        index=True,
    )

    knowledge_base: Mapped["KnowledgeBase"] = relationship("KnowledgeBase")


class TeamKnowledgeCollectionPermission(TeamAssignmentMixin, Base):
    """Team additive permission row for a knowledge collection action."""

    __tablename__ = "team_knowledge_collection_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "knowledge_collection_id",
            "team_id",
            "permission_action",
            name="uq_team_knowledge_collection_permissions_action",
        ),
        ForeignKeyConstraint(
            ["team_id", "grantee_organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_knowledge_collection_permissions_team_org",
        ),
        CheckConstraint(
            "permission_action IN ('read', 'route', 'manage', 'sync')",
            name="ck_team_knowledge_collection_permissions_action",
        ),
        CheckConstraint(
            "flags >= 0",
            name="ck_team_knowledge_collection_permissions_flags_nonnegative",
        ),
        Index(
            "ix_team_knowledge_collection_permissions_team",
            "team_id",
        ),
        Index(
            "ix_team_knowledge_collection_permissions_collection",
            "knowledge_collection_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    # 조직 단독 조건은 unique constraint의 왼쪽 접두어로 처리한다. team과
    # collection 단일 인덱스는 FK 대상 삭제 및 역방향 관리 조회를 위해 유지한다.
    grantee_organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id"),
        nullable=False,
    )
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    knowledge_collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_collections.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_action: Mapped[str] = mapped_column(String(32), nullable=False)

    knowledge_collection: Mapped["KnowledgeCollection"] = relationship(
        "KnowledgeCollection"
    )


class UserKnowledgeCollectionPermission(Base):
    """Direct additive user permission row for a knowledge collection action."""

    __tablename__ = "user_knowledge_collection_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "knowledge_collection_id",
            "permission_action",
            name="uq_user_knowledge_collection_permissions_action",
        ),
        CheckConstraint(
            "permission_action IN ('read', 'route', 'manage', 'sync')",
            name="ck_user_knowledge_collection_permissions_action",
        ),
        CheckConstraint(
            "flags >= 0",
            name="ck_user_knowledge_collection_permissions_flags_nonnegative",
        ),
        Index(
            "ix_user_knowledge_collection_permissions_user",
            "user_id",
        ),
        Index(
            "ix_user_knowledge_collection_permissions_collection",
            "knowledge_collection_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    grantee_organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    knowledge_collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_collections.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_action: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    options: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    flags: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )

    grantee_organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[grantee_organization_id],
    )
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    assigner: Mapped["User"] = relationship("User", foreign_keys=[assigned_by])
    knowledge_collection: Mapped["KnowledgeCollection"] = relationship(
        "KnowledgeCollection"
    )


class TeamKnowledgeDomainPermission(Base):
    """Organization-scoped Knowledge management action delegated to a Team."""

    __tablename__ = "team_knowledge_domain_permissions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "team_id",
            "permission_action",
            name="uq_team_knowledge_domain_permissions_action",
        ),
        ForeignKeyConstraint(
            ["team_id", "organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_knowledge_domain_permissions_team_org",
        ),
        CheckConstraint(
            "permission_action IN ('catalog_manage', 'permission_delegate', 'lifecycle_manage', 'sync_manage')",
            name="ck_team_knowledge_domain_permissions_action",
        ),
        CheckConstraint(
            "flags >= 0",
            name="ck_team_knowledge_domain_permissions_flags_nonnegative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False, index=True
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    permission_action: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    flags: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", foreign_keys=[organization_id]
    )
    team: Mapped["Team"] = relationship(
        "Team", foreign_keys=[team_id], overlaps="organization"
    )
    assigner: Mapped["User"] = relationship("User", foreign_keys=[assigned_by])


class UserKnowledgeDomainPermission(Base):
    """Organization-scoped Knowledge management action delegated to a User."""

    __tablename__ = "user_knowledge_domain_permissions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            "permission_action",
            name="uq_user_knowledge_domain_permissions_action",
        ),
        CheckConstraint(
            "permission_action IN ('catalog_manage', 'permission_delegate', 'lifecycle_manage', 'sync_manage')",
            name="ck_user_knowledge_domain_permissions_action",
        ),
        CheckConstraint(
            "flags >= 0",
            name="ck_user_knowledge_domain_permissions_flags_nonnegative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    permission_action: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    flags: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", foreign_keys=[organization_id]
    )
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    assigner: Mapped["User"] = relationship("User", foreign_keys=[assigned_by])


class TeamLLMPermission(TeamResourcePermissionMixin, Base):
    """Team permission for an LLM credential resource."""

    __tablename__ = "team_llm_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "llm_credential_id",
            "team_id",
            name="uq_team_llm_permissions_org_credential_team",
        ),
        ForeignKeyConstraint(
            ["team_id", "grantee_organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_llm_permissions_team_org",
        ),
        CheckConstraint("flags >= 0", name="ck_team_llm_permissions_flags_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    llm_credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_credentials.id"),
        nullable=False,
        index=True,
    )

    llm_credential: Mapped["LLMCredential"] = relationship("LLMCredential")


class TeamMailCredentialPermission(TeamResourcePermissionMixin, Base):
    """Team permission for a Mail credential resource."""

    __tablename__ = "team_mail_credential_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "mail_credential_id",
            "team_id",
            name="uq_team_mail_credential_permissions_org_credential_team",
        ),
        ForeignKeyConstraint(
            ["team_id", "grantee_organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_mail_credential_permissions_team_org",
        ),
        ForeignKeyConstraint(
            ["mail_credential_id", "grantee_organization_id"],
            ["mail_credentials.id", "mail_credentials.organization_id"],
            name="fk_team_mail_credential_permissions_credential_org",
        ),
        CheckConstraint(
            "auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')",
            name="ck_team_mail_credential_permissions_auth_state",
        ),
        CheckConstraint(
            "flags >= 0", name="ck_team_mail_credential_permissions_flags_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    mail_credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    mail_credential: Mapped["MailCredential"] = relationship(
        "MailCredential", overlaps="grantee_organization"
    )


class TeamAuditPermission(TeamResourcePermissionMixin, Base):
    """Team permission for audit visibility over one target organization."""

    __tablename__ = "team_audit_permissions"
    __table_args__ = (
        UniqueConstraint(
            "grantee_organization_id",
            "target_organization_id",
            "team_id",
            name="uq_team_audit_permissions_org_target_team",
        ),
        ForeignKeyConstraint(
            ["team_id", "grantee_organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_audit_permissions_team_org",
        ),
        CheckConstraint(
            "flags >= 0", name="ck_team_audit_permissions_flags_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    target_organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id"),
        nullable=False,
        index=True,
    )

    target_organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[target_organization_id],
    )
