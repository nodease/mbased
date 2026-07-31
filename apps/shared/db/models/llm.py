import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from apps.shared.db.models.user import User

from apps.shared.db.base import Base
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# === Legacy Models (Mapped to renamed tables for reference/migration) ===
# class LegacyLLMProvider(Base):
#     __tablename__ = "legacy_llm_provider"
#     id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
# ... (simplified)


# class LegacyLLMCredential(Base):
#     __tablename__ = "legacy_llm_credentials"
#     id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
# ... (simplified)


# === New Models ===


class LLMProvider(Base):
    """
    LLM 공급사 정보 (예: OpenAI, Google)
    시스템 전체 공통 데이터. 변경이 거의 없음.
    """

    __tablename__ = "llm_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)  # ex: openai
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)  # ex: system, custom
    base_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    auth_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # api_key, oauth, aws_sigv4
    doc_url: Mapped[str] = mapped_column(Text, nullable=False)

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

    # Relations
    models: Mapped[List["LLMModel"]] = relationship(
        "LLMModel", back_populates="provider", cascade="all, delete-orphan"
    )
    credentials: Mapped[List["LLMCredential"]] = relationship(
        "LLMCredential", back_populates="provider"
    )


class LLMModel(Base):
    """
    Provider가 제공하는 모델 목록 (예: gpt-4, claude-2)
    시스템 전역 카탈로그.
    """

    __tablename__ = "llm_models"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_id_for_api_call: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # ex: gpt-4o
    name: Mapped[str] = mapped_column(Text, nullable=False)  # ex: GPT-4o (Omni)
    type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # chat, embedding (default 삭제 요청 반영)
    context_window: Mapped[int] = mapped_column(Integer, nullable=False)
    input_price_1k: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 6), nullable=True
    )
    output_price_1k: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 6), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    model_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, nullable=True
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

    # Relations
    provider: Mapped["LLMProvider"] = relationship(
        "LLMProvider", back_populates="models"
    )
    usage_logs: Mapped[List["LLMUsageLog"]] = relationship(
        "LLMUsageLog", back_populates="model"
    )
    # M:N mapping through LLMRelCredentialModel is usually handled explicitly or via association proxy if needed

    @property
    def provider_name(self) -> str:
        return self.provider.name if self.provider else "Unknown"


class LLMCredential(Base):
    """
    유저가 저장한 인증 정보 (예: API Key)
    """

    __tablename__ = "llm_credentials"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_llm_credentials_id_organization_id",
        ),
        CheckConstraint(
            "(encryption_key_version IS NULL) = "
            "(encryption_algorithm IS NULL)",
            name="ck_llm_credentials_encryption_metadata_pair",
        ),
        Index(
            "ix_llm_credentials_encryption_key_version",
            "encryption_key_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id"),
        nullable=True,
        index=True,
    )
    credential_name: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_config: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    encryption_key_version: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    encryption_algorithm: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    config_preview: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # sk-****

    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Quota
    quota_type: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    quota_limit: Mapped[int] = mapped_column(BigInteger, nullable=False, default=-1)
    quota_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relations
    provider: Mapped["LLMProvider"] = relationship(
        "LLMProvider", back_populates="credentials"
    )
    user: Mapped["User"] = relationship(
        "User"
    )  # Avoid circular import if possible, or use string
    usage_logs: Mapped[List["LLMUsageLog"]] = relationship(
        "LLMUsageLog", back_populates="credential"
    )


class LLMRelCredentialModel(Base):
    """
    Credential <-> Model 매핑 (가용 모델/권한)
    """

    __tablename__ = "llm_rel_credential_models"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class LLMDeploymentCredentialPolicy(Base):
    """Server-owned credential selection for one deployed LLM node.

    The workflow graph intentionally never stores ``credential_id``.  This
    policy is separately versioned and is the only source an execution
    capability issuer may use to select a generation credential.
    """

    __tablename__ = "llm_deployment_credential_policies"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_llm_deploy_credential_policy_id_org",
        ),
        CheckConstraint(
            "deployment_version >= 1 AND policy_revision >= 1 "
            "AND purpose IN ('main_generation', 'query_embedding')",
            name="ck_llm_deploy_credential_policy_revision",
        ),
        Index(
            "uq_llm_deploy_credential_policy_active",
            "organization_id",
            "deployment_id",
            "deployment_version",
            "node_location_digest",
            unique=True,
            postgresql_where=text(
                "is_active AND purpose = 'main_generation'"
            ),
        ),
        Index(
            "uq_llm_deploy_credential_policy_active_query_embedding",
            "organization_id",
            "deployment_id",
            "deployment_version",
            "node_location_digest",
            "model_id",
            unique=True,
            postgresql_where=text(
                "is_active AND purpose = 'query_embedding'"
            ),
        ),
        Index(
            "ix_llm_deploy_credential_policy_lookup",
            "organization_id",
            "deployment_id",
            "deployment_version",
            "node_location_digest",
            "purpose",
            "model_id",
            "is_active",
        ),
        CheckConstraint(
            "jsonb_typeof(container_path) = 'array' "
            "AND length(node_location_digest) = 64",
            name="ck_llm_deploy_credential_policy_location",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="CASCADE"),
        nullable=False,
    )
    deployment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    container_path: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    node_location_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="main_generation",
        server_default=text("'main_generation'"),
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="RESTRICT"),
        nullable=False,
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_credentials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    credential_principal_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
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


class ProviderExecutionCapabilityRecord(Base):
    """Durable opaque scope of one provider attempt; never stores a secret."""

    __tablename__ = "provider_execution_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_provider_execution_capability_id_org",
        ),
        UniqueConstraint(
            "organization_id",
            "provider_attempt_id",
            "purpose",
            name="uq_provider_execution_capability_attempt_purpose",
        ),
        ForeignKeyConstraint(
            ["policy_id", "organization_id"],
            [
                "llm_deployment_credential_policies.id",
                "llm_deployment_credential_policies.organization_id",
            ],
            name="fk_provider_execution_capability_policy_org",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "deployment_version >= 1 AND capability_revision >= 1 "
            "AND input_token_cap >= 0 AND output_token_cap >= 0 "
            "AND cost_cap_microusd >= 0",
            name="ck_provider_execution_capability_bounds",
        ),
        CheckConstraint(
            "purpose IN ('main_generation', 'memory_summary', 'query_embedding') "
            "AND state IN ('active', 'revoked') "
            "AND execution_subject_kind IN ('user', 'anonymous_public', 'system') "
            "AND billing_principal_kind = 'organization' "
            "AND audit_actor_kind IN ('user', 'public', 'system') "
            "AND length(permission_revision) = 64 "
            "AND length(relation_revision) = 64 "
            "AND length(egress_revision) = 64 "
            "AND length(pricing_revision) = 64",
            name="ck_provider_execution_capability_state",
        ),
        CheckConstraint(
            "purpose <> 'query_embedding' OR output_token_cap = 0",
            name="ck_provider_execution_capability_query_embedding_output",
        ),
        CheckConstraint(
            "(execution_subject_kind = 'user' AND execution_subject_id IS NOT NULL) "
            "OR (execution_subject_kind IN ('anonymous_public', 'system') "
            "AND execution_subject_id IS NULL)",
            name="ck_provider_execution_capability_execution_subject",
        ),
        CheckConstraint(
            "(audit_actor_kind = 'user' AND audit_actor_id IS NOT NULL) "
            "OR (audit_actor_kind IN ('public', 'system') AND audit_actor_id IS NULL)",
            name="ck_provider_execution_capability_audit_actor",
        ),
        CheckConstraint(
            "billing_principal_id = organization_id",
            name="ck_provider_execution_capability_billing_scope",
        ),
        CheckConstraint(
            "(execution_subject_kind = 'user' AND audit_actor_kind = 'user' "
            "AND execution_subject_id = audit_actor_id) "
            "OR (execution_subject_kind = 'anonymous_public' "
            "AND audit_actor_kind = 'public') "
            "OR (execution_subject_kind = 'system' AND audit_actor_kind = 'system')",
            name="ck_provider_execution_capability_identity_alignment",
        ),
        CheckConstraint(
            "(state = 'active' AND revoked_at IS NULL) "
            "OR (state = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_provider_execution_capability_revocation_state",
        ),
        CheckConstraint(
            "jsonb_typeof(container_path) = 'array' "
            "AND length(node_location_digest) = 64",
            name="ck_provider_execution_capability_location",
        ),
        Index(
            "ix_provider_execution_capability_expiry",
            "organization_id",
            "state",
            "expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_deployments.id", ondelete="CASCADE"),
        nullable=False,
    )
    deployment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    container_path: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    node_location_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    node_invocation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    execution_admission_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    provider_attempt_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="RESTRICT"),
        nullable=False,
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_credentials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    credential_principal_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    execution_subject_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_subject_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    billing_principal_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_principal_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    audit_actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    audit_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    capability_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    permission_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    relation_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    egress_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    pricing_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    input_token_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    output_token_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_cap_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
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


class LLMUsageLog(Base):
    """
    LLM 사용 로그
    """

    __tablename__ = "llm_usage_logs"
    __table_args__ = (
        CheckConstraint(
            "runtime_attempt IS NULL OR runtime_attempt > 0",
            name="ck_llm_usage_logs_runtime_attempt_positive",
        ),
        CheckConstraint(
            "runtime_surface <> 'agent_builder_intent' OR "
            "(runtime_session_id IS NOT NULL AND runtime_request_id IS NOT NULL "
            "AND runtime_attempt IS NOT NULL)",
            name="ck_llm_usage_logs_agent_builder_runtime_identity",
        ),
        CheckConstraint(
            "runtime_surface <> 'agent_builder_intent' OR "
            "(prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND total_cost IS NOT NULL AND total_cost >= 0 AND latency_ms >= 0)",
            name="ck_llm_usage_logs_agent_builder_billing_facts",
        ),
        CheckConstraint(
            "(provider_usage_operation_id IS NULL "
            "AND provider_usage_revision IS NULL) OR "
            "(provider_usage_operation_id IS NOT NULL "
            "AND provider_usage_revision IS NOT NULL "
            "AND provider_usage_revision >= 1)",
            name="ck_llm_usage_logs_provider_usage_projection",
        ),
        Index(
            "uq_llm_usage_logs_agent_builder_attempt",
            "runtime_surface",
            "runtime_session_id",
            "runtime_request_id",
            "runtime_attempt",
            unique=True,
            postgresql_where=text("runtime_surface = 'agent_builder_intent'"),
        ),
        Index(
            "ix_llm_usage_logs_org_surface_created",
            "organization_id",
            "runtime_surface",
            "created_at",
        ),
        Index(
            "uq_llm_usage_logs_provider_usage_operation",
            "provider_usage_operation_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organization.id"), nullable=True, index=True,
    )
    credential_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_credentials.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    model_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    cost_optimizer_candidate_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cost_optimizer_candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    node_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    runtime_surface: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    runtime_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    runtime_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    runtime_attempt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    provider_usage_operation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    provider_usage_revision: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost: Mapped[Optional[float]] = mapped_column(Numeric(10, 6), nullable=True)

    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="success")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relations
    credential: Mapped[Optional["LLMCredential"]] = relationship(
        "LLMCredential", back_populates="usage_logs"
    )
    model: Mapped[Optional["LLMModel"]] = relationship(
        "LLMModel", back_populates="usage_logs"
    )
