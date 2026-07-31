"""add knowledge integration phase1 schema

Revision ID: e1a2b3c4d5f6
Revises: b3c4d5e6f7a8
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a2b3c4d5f6"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_source_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_item_ref", sa.String(length=255), nullable=False),
        sa.Column("source_parent_ref", sa.String(length=255), nullable=True),
        sa.Column("source_url_ref", sa.String(length=255), nullable=True),
        sa.Column("source_principal_ref", sa.String(length=255), nullable=True),
        sa.Column("hmac_key_version", sa.String(length=64), nullable=False),
        sa.Column("safe_display_name", sa.String(length=255), nullable=True),
        sa.Column("safe_display_path", sa.String(length=1024), nullable=True),
        sa.Column("safe_display_url", sa.String(length=1024), nullable=True),
        sa.Column("safe_display_description", sa.Text(), nullable=True),
        sa.Column(
            "display_policy_state",
            sa.String(length=32),
            server_default=sa.text("'unreviewed'"),
            nullable=False,
        ),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "display_policy_state IN ('unreviewed', 'approved', 'rejected')",
            name="ck_knowledge_source_identities_display_policy",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "source_system",
            "source_item_ref",
            "hmac_key_version",
            name="uq_knowledge_source_identities_org_source_ref_key",
        ),
    )
    op.create_index(
        "ix_knowledge_source_identities_organization_id",
        "knowledge_source_identities",
        ["organization_id"],
    )
    op.create_index(
        "ix_knowledge_source_identities_org_source",
        "knowledge_source_identities",
        ["organization_id", "source_system", "source_item_ref"],
    )

    op.create_table(
        "knowledge_collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_connector_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "is_system_managed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "sync_state",
            sa.String(length=50),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
        sa.Column(
            "lifecycle_state",
            sa.String(length=50),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lifecycle_state IN ('active', 'archived', 'deleted')",
            name="ck_knowledge_collections_lifecycle_state",
        ),
        sa.CheckConstraint(
            "sync_state IN ('manual', 'pending', 'syncing', 'synced', 'failed', 'stale', 'source_deleted')",
            name="ck_knowledge_collections_sync_state",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(
            ["source_identity_id"],
            ["knowledge_source_identities.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "name",
            name="uq_knowledge_collections_org_name",
        ),
    )
    op.create_index(
        "ix_knowledge_collections_organization_id",
        "knowledge_collections",
        ["organization_id"],
    )
    op.create_index(
        "ix_knowledge_collections_source_identity_id",
        "knowledge_collections",
        ["source_identity_id"],
    )
    op.create_index(
        "ix_knowledge_collections_org_sync",
        "knowledge_collections",
        ["organization_id", "sync_state"],
    )

    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legacy_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'staging'"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("chunking_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
        sa.Column("processing_policy_version", sa.String(length=64), nullable=True),
        sa.Column("source_tier", sa.String(length=64), nullable=True),
        sa.Column("approval_state", sa.String(length=64), nullable=True),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('staging', 'indexing', 'ready', 'failed', 'superseded')",
            name="ck_document_versions_status",
        ),
        sa.CheckConstraint(
            "version_number > 0", name="ck_document_versions_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["legacy_document_id"], ["documents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(
            ["source_identity_id"],
            ["knowledge_source_identities.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "knowledge_base_id",
            "version_number",
            name="uq_document_versions_org_kb_version",
        ),
    )
    op.create_index(
        "ix_document_versions_organization_id",
        "document_versions",
        ["organization_id"],
    )
    op.create_index(
        "ix_document_versions_knowledge_base_id",
        "document_versions",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_document_versions_legacy_document_id",
        "document_versions",
        ["legacy_document_id"],
    )
    op.create_index(
        "ix_document_versions_source_identity_id",
        "document_versions",
        ["source_identity_id"],
    )
    op.create_index(
        "ix_document_versions_org_kb_status",
        "document_versions",
        ["organization_id", "knowledge_base_id", "status"],
    )

    op.add_column(
        "knowledge_bases",
        sa.Column("active_document_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column("source_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "sync_state",
            sa.String(length=50),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "lifecycle_state",
            sa.String(length=50),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_knowledge_bases_sync_state",
        "knowledge_bases",
        "sync_state IN ('manual', 'pending', 'syncing', 'synced', 'failed', 'stale', 'source_deleted')",
    )
    op.create_check_constraint(
        "ck_knowledge_bases_lifecycle_state",
        "knowledge_bases",
        "lifecycle_state IN ('active', 'archived', 'deleted')",
    )
    op.create_foreign_key(
        "fk_knowledge_bases_active_document_version_id",
        "knowledge_bases",
        "document_versions",
        ["active_document_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_knowledge_bases_source_identity_id",
        "knowledge_bases",
        "knowledge_source_identities",
        ["source_identity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_knowledge_bases_source_identity_id",
        "knowledge_bases",
        ["source_identity_id"],
    )
    op.create_index(
        "ix_knowledge_bases_active_document_version_id",
        "knowledge_bases",
        ["active_document_version_id"],
    )
    op.create_index(
        "ix_knowledge_bases_source_identity_id",
        "knowledge_bases",
        ["source_identity_id"],
    )

    op.add_column(
        "document_chunks",
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_document_chunks_document_version_id",
        "document_chunks",
        "document_versions",
        ["document_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_document_chunks_document_version_id",
        "document_chunks",
        ["document_version_id"],
    )
    op.create_index(
        "ix_document_chunks_kb_doc_version",
        "document_chunks",
        ["knowledge_base_id", "document_version_id"],
    )

    op.create_table(
        "knowledge_collection_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("safe_source_path_ref", sa.String(length=512), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["knowledge_collections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collection_id",
            "knowledge_base_id",
            name="uq_knowledge_collection_items_collection_kb",
        ),
    )
    op.create_index(
        "ix_knowledge_collection_items_organization_id",
        "knowledge_collection_items",
        ["organization_id"],
    )
    op.create_index(
        "ix_knowledge_collection_items_collection_id",
        "knowledge_collection_items",
        ["collection_id"],
    )
    op.create_index(
        "ix_knowledge_collection_items_knowledge_base_id",
        "knowledge_collection_items",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_knowledge_collection_items_org_collection_rank",
        "knowledge_collection_items",
        ["organization_id", "collection_id", "rank"],
    )

    op.create_table(
        "team_knowledge_collection_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grantee_organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("flags", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("knowledge_collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_action", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_team_knowledge_collection_permissions_flags_nonnegative",
        ),
        sa.CheckConstraint(
            "permission_action IN ('read', 'route', 'manage', 'sync')",
            name="ck_team_knowledge_collection_permissions_action",
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["grantee_organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(
            ["knowledge_collection_id"],
            ["knowledge_collections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(
            ["team_id", "grantee_organization_id"],
            ["teams.id", "teams.organization_id"],
            name="fk_team_knowledge_collection_permissions_team_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grantee_organization_id",
            "knowledge_collection_id",
            "team_id",
            "permission_action",
            name="uq_team_knowledge_collection_permissions_action",
        ),
    )
    op.create_index(
        "ix_team_knowledge_collection_permissions_org",
        "team_knowledge_collection_permissions",
        ["grantee_organization_id"],
    )
    op.create_index(
        "ix_team_knowledge_collection_permissions_team",
        "team_knowledge_collection_permissions",
        ["team_id"],
    )
    op.create_index(
        "ix_team_knowledge_collection_permissions_collection",
        "team_knowledge_collection_permissions",
        ["knowledge_collection_id"],
    )

    op.create_table(
        "user_knowledge_collection_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grantee_organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_action", sa.String(length=32), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("flags", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "flags >= 0",
            name="ck_user_knowledge_collection_permissions_flags_nonnegative",
        ),
        sa.CheckConstraint(
            "permission_action IN ('read', 'route', 'manage', 'sync')",
            name="ck_user_knowledge_collection_permissions_action",
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["grantee_organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(
            ["knowledge_collection_id"],
            ["knowledge_collections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grantee_organization_id",
            "user_id",
            "knowledge_collection_id",
            "permission_action",
            name="uq_user_knowledge_collection_permissions_action",
        ),
    )
    op.create_index(
        "ix_user_knowledge_collection_permissions_org",
        "user_knowledge_collection_permissions",
        ["grantee_organization_id"],
    )
    op.create_index(
        "ix_user_knowledge_collection_permissions_user",
        "user_knowledge_collection_permissions",
        ["user_id"],
    )
    op.create_index(
        "ix_user_knowledge_collection_permissions_collection",
        "user_knowledge_collection_permissions",
        ["knowledge_collection_id"],
    )

    op.create_table(
        "source_policy_kb_use_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "permission_action",
            sa.String(length=32),
            server_default=sa.text("'use'"),
            nullable=False,
        ),
        sa.Column("source_policy_id", sa.String(length=255), nullable=False),
        sa.Column("provisioned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revocation_behavior",
            sa.String(length=64),
            server_default=sa.text("'deactivate_on_revoke'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("freshness_epoch", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "freshness_epoch >= 0",
            name="ck_source_policy_kb_use_grants_epoch_nonnegative",
        ),
        sa.CheckConstraint(
            "permission_action = 'use'",
            name="ck_source_policy_kb_use_grants_action",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_source_policy_kb_use_grants_status",
        ),
        sa.CheckConstraint(
            "subject_type IN ('organization', 'team', 'user')",
            name="ck_source_policy_kb_use_grants_subject_type",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["provisioned_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_identity_id"],
            ["knowledge_source_identities.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "knowledge_base_id",
            "subject_type",
            "subject_id",
            "source_policy_id",
            name="uq_source_policy_kb_use_grants_subject_policy",
        ),
    )
    op.create_index(
        "ix_source_policy_kb_use_grants_organization_id",
        "source_policy_kb_use_grants",
        ["organization_id"],
    )
    op.create_index(
        "ix_source_policy_kb_use_grants_knowledge_base_id",
        "source_policy_kb_use_grants",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_source_policy_kb_use_grants_source_identity_id",
        "source_policy_kb_use_grants",
        ["source_identity_id"],
    )
    op.create_index(
        "ix_source_policy_grants_subject_active",
        "source_policy_kb_use_grants",
        ["organization_id", "subject_type", "subject_id", "status", "freshness_epoch"],
    )

    op.create_table(
        "source_authorization_provenance",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requester_subject_type", sa.String(length=32), nullable=False),
        sa.Column("requester_subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_acl_state", sa.String(length=32), nullable=False),
        sa.Column("requester_source_authorization", sa.String(length=32), nullable=False),
        sa.Column("source_permission_action", sa.String(length=64), nullable=True),
        sa.Column("source_principal_ref", sa.String(length=255), nullable=True),
        sa.Column("hmac_key_version", sa.String(length=64), nullable=True),
        sa.Column("freshness_epoch", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("freshness_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "freshness_epoch >= 0",
            name="ck_source_authorization_epoch_nonnegative",
        ),
        sa.CheckConstraint(
            "requester_source_authorization IN ('allowed', 'denied', 'unknown', 'not_applicable')",
            name="ck_source_authorization_requester_result",
        ),
        sa.CheckConstraint(
            "requester_subject_type IN ('user', 'team', 'organization', 'service_account')",
            name="ck_source_authorization_requester_type",
        ),
        sa.CheckConstraint(
            "source_acl_state IN ('fresh', 'stale', 'unmapped', 'ambiguous', 'unverified', 'revoked', 'not_source_managed')",
            name="ck_source_authorization_acl_state",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_source_authorization_status",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(
            ["source_identity_id"],
            ["knowledge_source_identities.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "knowledge_base_id",
            "requester_subject_type",
            "requester_subject_id",
            "source_identity_id",
            name="uq_source_authorization_provenance_requester",
        ),
    )
    op.create_index(
        "ix_source_authorization_provenance_organization_id",
        "source_authorization_provenance",
        ["organization_id"],
    )
    op.create_index(
        "ix_source_authorization_provenance_knowledge_base_id",
        "source_authorization_provenance",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_source_authorization_provenance_source_identity_id",
        "source_authorization_provenance",
        ["source_identity_id"],
    )
    op.create_index(
        "ix_source_authorization_provenance_requester_subject_id",
        "source_authorization_provenance",
        ["requester_subject_id"],
    )
    op.create_index(
        "ix_source_authorization_subject_freshness",
        "source_authorization_provenance",
        [
            "organization_id",
            "requester_subject_type",
            "requester_subject_id",
            "status",
            "freshness_expires_at",
        ],
    )

    op.create_table(
        "knowledge_ingestion_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("owner_token", sa.String(length=128), nullable=True),
        sa.Column("fencing_token", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retryable", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("safe_reason_code", sa.String(length=100), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redrive_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "target_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_knowledge_ingestion_outbox_attempt_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_knowledge_ingestion_outbox_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'succeeded', 'retry_scheduled', 'dead_lettered', 'cancelled')",
            name="ck_knowledge_ingestion_outbox_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(
            ["source_identity_id"],
            ["knowledge_source_identities.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_knowledge_ingestion_outbox_org_idempotency",
        ),
    )
    op.create_index(
        "ix_knowledge_ingestion_outbox_organization_id",
        "knowledge_ingestion_outbox",
        ["organization_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_outbox_knowledge_base_id",
        "knowledge_ingestion_outbox",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_outbox_document_version_id",
        "knowledge_ingestion_outbox",
        ["document_version_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_outbox_source_identity_id",
        "knowledge_ingestion_outbox",
        ["source_identity_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_outbox_status_retry",
        "knowledge_ingestion_outbox",
        ["status", "next_retry_at"],
    )
    op.create_index(
        "ix_knowledge_ingestion_outbox_lease",
        "knowledge_ingestion_outbox",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_ingestion_outbox_lease", table_name="knowledge_ingestion_outbox")
    op.drop_index("ix_knowledge_ingestion_outbox_status_retry", table_name="knowledge_ingestion_outbox")
    op.drop_index("ix_knowledge_ingestion_outbox_source_identity_id", table_name="knowledge_ingestion_outbox")
    op.drop_index("ix_knowledge_ingestion_outbox_document_version_id", table_name="knowledge_ingestion_outbox")
    op.drop_index("ix_knowledge_ingestion_outbox_knowledge_base_id", table_name="knowledge_ingestion_outbox")
    op.drop_index("ix_knowledge_ingestion_outbox_organization_id", table_name="knowledge_ingestion_outbox")
    op.drop_table("knowledge_ingestion_outbox")

    op.drop_index("ix_source_authorization_subject_freshness", table_name="source_authorization_provenance")
    op.drop_index("ix_source_authorization_provenance_requester_subject_id", table_name="source_authorization_provenance")
    op.drop_index("ix_source_authorization_provenance_source_identity_id", table_name="source_authorization_provenance")
    op.drop_index("ix_source_authorization_provenance_knowledge_base_id", table_name="source_authorization_provenance")
    op.drop_index("ix_source_authorization_provenance_organization_id", table_name="source_authorization_provenance")
    op.drop_table("source_authorization_provenance")

    op.drop_index("ix_source_policy_grants_subject_active", table_name="source_policy_kb_use_grants")
    op.drop_index("ix_source_policy_kb_use_grants_source_identity_id", table_name="source_policy_kb_use_grants")
    op.drop_index("ix_source_policy_kb_use_grants_knowledge_base_id", table_name="source_policy_kb_use_grants")
    op.drop_index("ix_source_policy_kb_use_grants_organization_id", table_name="source_policy_kb_use_grants")
    op.drop_table("source_policy_kb_use_grants")

    op.drop_index("ix_user_knowledge_collection_permissions_collection", table_name="user_knowledge_collection_permissions")
    op.drop_index("ix_user_knowledge_collection_permissions_user", table_name="user_knowledge_collection_permissions")
    op.drop_index("ix_user_knowledge_collection_permissions_org", table_name="user_knowledge_collection_permissions")
    op.drop_table("user_knowledge_collection_permissions")

    op.drop_index("ix_team_knowledge_collection_permissions_collection", table_name="team_knowledge_collection_permissions")
    op.drop_index("ix_team_knowledge_collection_permissions_team", table_name="team_knowledge_collection_permissions")
    op.drop_index("ix_team_knowledge_collection_permissions_org", table_name="team_knowledge_collection_permissions")
    op.drop_table("team_knowledge_collection_permissions")

    op.drop_index("ix_knowledge_collection_items_org_collection_rank", table_name="knowledge_collection_items")
    op.drop_index("ix_knowledge_collection_items_knowledge_base_id", table_name="knowledge_collection_items")
    op.drop_index("ix_knowledge_collection_items_collection_id", table_name="knowledge_collection_items")
    op.drop_index("ix_knowledge_collection_items_organization_id", table_name="knowledge_collection_items")
    op.drop_table("knowledge_collection_items")

    op.drop_index("ix_document_chunks_kb_doc_version", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_version_id", table_name="document_chunks")
    op.drop_constraint(
        "fk_document_chunks_document_version_id",
        "document_chunks",
        type_="foreignkey",
    )
    op.drop_column("document_chunks", "document_version_id")

    op.drop_index("ix_knowledge_bases_source_identity_id", table_name="knowledge_bases")
    op.drop_index("ix_knowledge_bases_active_document_version_id", table_name="knowledge_bases")
    op.drop_constraint("uq_knowledge_bases_source_identity_id", "knowledge_bases", type_="unique")
    op.drop_constraint("fk_knowledge_bases_source_identity_id", "knowledge_bases", type_="foreignkey")
    op.drop_constraint("fk_knowledge_bases_active_document_version_id", "knowledge_bases", type_="foreignkey")
    op.drop_constraint("ck_knowledge_bases_lifecycle_state", "knowledge_bases", type_="check")
    op.drop_constraint("ck_knowledge_bases_sync_state", "knowledge_bases", type_="check")
    op.drop_column("knowledge_bases", "lifecycle_state")
    op.drop_column("knowledge_bases", "sync_state")
    op.drop_column("knowledge_bases", "source_identity_id")
    op.drop_column("knowledge_bases", "active_document_version_id")

    op.drop_index("ix_document_versions_org_kb_status", table_name="document_versions")
    op.drop_index("ix_document_versions_source_identity_id", table_name="document_versions")
    op.drop_index("ix_document_versions_legacy_document_id", table_name="document_versions")
    op.drop_index("ix_document_versions_knowledge_base_id", table_name="document_versions")
    op.drop_index("ix_document_versions_organization_id", table_name="document_versions")
    op.drop_table("document_versions")

    op.drop_index("ix_knowledge_collections_org_sync", table_name="knowledge_collections")
    op.drop_index("ix_knowledge_collections_source_identity_id", table_name="knowledge_collections")
    op.drop_index("ix_knowledge_collections_organization_id", table_name="knowledge_collections")
    op.drop_table("knowledge_collections")

    op.drop_index("ix_knowledge_source_identities_org_source", table_name="knowledge_source_identities")
    op.drop_index("ix_knowledge_source_identities_organization_id", table_name="knowledge_source_identities")
    op.drop_table("knowledge_source_identities")
