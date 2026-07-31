"""Add server-owned LLM deployment policies and provider capabilities.

Revision ID: ad1e2f3a4b5c
Revises: b05c6d7e8f94
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ad1e2f3a4b5c"
down_revision: str | Sequence[str] | None = "b05c6d7e8f94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "llm_deployment_credential_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_version", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "credential_principal_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "policy_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_llm_deploy_credential_policy_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_llm_deploy_credential_policy_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["workflow_deployments.id"],
            name="fk_llm_deploy_credential_policy_deployment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["llm_models.id"],
            name="fk_llm_deploy_credential_policy_model",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["llm_credentials.id"],
            name="fk_llm_deploy_credential_policy_credential",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_principal_user_id"],
            ["users.id"],
            name="fk_llm_deploy_credential_policy_principal",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_llm_deploy_credential_policy_id_org",
        ),
        sa.CheckConstraint(
            "deployment_version >= 1 AND policy_revision >= 1",
            name="ck_llm_deploy_credential_policy_revision",
        ),
    )
    op.create_index(
        "uq_llm_deploy_credential_policy_active",
        "llm_deployment_credential_policies",
        ["organization_id", "deployment_id", "deployment_version", "node_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_llm_deploy_credential_policy_lookup",
        "llm_deployment_credential_policies",
        ["organization_id", "deployment_id", "deployment_version", "node_id", "is_active"],
    )

    op.create_table(
        "provider_execution_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_version", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("node_invocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "execution_admission_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("provider_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "credential_principal_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("execution_subject_kind", sa.String(length=32), nullable=False),
        sa.Column("execution_subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("billing_principal_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "billing_principal_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("audit_actor_kind", sa.String(length=32), nullable=False),
        sa.Column("audit_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "capability_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("permission_revision", sa.String(length=64), nullable=False),
        sa.Column("relation_revision", sa.String(length=64), nullable=False),
        sa.Column("egress_revision", sa.String(length=64), nullable=False),
        sa.Column("pricing_revision", sa.String(length=64), nullable=False),
        sa.Column("input_token_cap", sa.Integer(), nullable=False),
        sa.Column("output_token_cap", sa.Integer(), nullable=False),
        sa.Column("cost_cap_microusd", sa.BigInteger(), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_provider_execution_capability_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id", "organization_id"],
            [
                "llm_deployment_credential_policies.id",
                "llm_deployment_credential_policies.organization_id",
            ],
            name="fk_provider_execution_capability_policy_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_provider_execution_capability_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["workflow_deployments.id"],
            name="fk_provider_execution_capability_deployment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["llm_providers.id"],
            name="fk_provider_execution_capability_provider",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["llm_models.id"],
            name="fk_provider_execution_capability_model",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["llm_credentials.id"],
            name="fk_provider_execution_capability_credential",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_principal_user_id"],
            ["users.id"],
            name="fk_provider_execution_capability_principal",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_provider_execution_capability_id_org",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "provider_attempt_id",
            "purpose",
            name="uq_provider_execution_capability_attempt_purpose",
        ),
        sa.CheckConstraint(
            "deployment_version >= 1 AND capability_revision >= 1 "
            "AND input_token_cap >= 0 AND output_token_cap >= 0 "
            "AND cost_cap_microusd >= 0",
            name="ck_provider_execution_capability_bounds",
        ),
        sa.CheckConstraint(
            "purpose IN ('main_generation', 'memory_summary') "
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
        sa.CheckConstraint(
            "(execution_subject_kind = 'user' AND execution_subject_id IS NOT NULL) "
            "OR (execution_subject_kind IN ('anonymous_public', 'system') "
            "AND execution_subject_id IS NULL)",
            name="ck_provider_execution_capability_execution_subject",
        ),
        sa.CheckConstraint(
            "(audit_actor_kind = 'user' AND audit_actor_id IS NOT NULL) "
            "OR (audit_actor_kind IN ('public', 'system') AND audit_actor_id IS NULL)",
            name="ck_provider_execution_capability_audit_actor",
        ),
        sa.CheckConstraint(
            "billing_principal_id = organization_id",
            name="ck_provider_execution_capability_billing_scope",
        ),
        sa.CheckConstraint(
            "(execution_subject_kind = 'user' AND audit_actor_kind = 'user' "
            "AND execution_subject_id = audit_actor_id) "
            "OR (execution_subject_kind = 'anonymous_public' "
            "AND audit_actor_kind = 'public') "
            "OR (execution_subject_kind = 'system' AND audit_actor_kind = 'system')",
            name="ck_provider_execution_capability_identity_alignment",
        ),
        sa.CheckConstraint(
            "(state = 'active' AND revoked_at IS NULL) "
            "OR (state = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_provider_execution_capability_revocation_state",
        ),
    )
    op.create_index(
        "ix_provider_execution_capability_expiry",
        "provider_execution_capabilities",
        ["organization_id", "state", "expires_at"],
    )


def downgrade() -> None:
    op.drop_table("provider_execution_capabilities")
    op.drop_table("llm_deployment_credential_policies")
