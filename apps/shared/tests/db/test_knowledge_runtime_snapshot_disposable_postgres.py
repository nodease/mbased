# ruff: noqa: E402

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest

RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "nodease_knowledge_snapshot_test"

if os.getenv(RUN_ENV) != "1":
    pytest.skip(
        f"set {RUN_ENV}=1 to run disposable Knowledge snapshot evidence",
        allow_module_level=True,
    )

from apps.shared.domain.knowledge_runtime_candidates import (
    AnonymousPublicAudience,
    AuthenticatedAudience,
    KnowledgeRuntimeCandidateRequest,
)
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from apps.workflow_engine.adapters.knowledge_runtime_candidates import (
    KnowledgeRuntimeCandidateSnapshotError,
    PostgresKnowledgeRuntimeCandidateSnapshotAdapter,
)
from apps.workflow_engine.application.provider_execution import ProviderExecutionPlan
from apps.workflow_engine.application.query_embedding_execution import (
    QueryEmbeddingPlan,
)
from apps.workflow_engine.application.runtime_retrieval.knowledge_candidates import (
    KnowledgeRuntimeCandidateResolver,
)
from apps.workflow_engine.workflow.nodes.llm.entities import (
    KnowledgeBaseRef,
    KnowledgeCollectionRef,
    LLMNodeData,
)
from apps.workflow_engine.workflow.nodes.llm.llm_node import (
    RAG_NO_EVIDENCE_MESSAGE,
    LLMNode,
    WorkflowRAGFanoutResult,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker


def _create_schema(engine) -> None:
    statements = (
        """
        CREATE TABLE users (
            id UUID PRIMARY KEY,
            email VARCHAR(255) NOT NULL,
            name VARCHAR(255) NOT NULL,
            password VARCHAR(255) NULL,
            social_provider VARCHAR(50) NOT NULL DEFAULT 'test',
            social_id VARCHAR(255) NULL,
            avatar_url VARCHAR(255) NULL,
            deactivated_at TIMESTAMPTZ NULL,
            last_login_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE organization (
            id UUID PRIMARY KEY,
            name VARCHAR(255) NOT NULL DEFAULT 'test organization',
            options JSONB NOT NULL DEFAULT '{}'::jsonb,
            flags BIGINT NOT NULL DEFAULT 0,
            created_by UUID NULL,
            managed_by UUID NULL,
            is_active BOOLEAN NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deactivated_at TIMESTAMPTZ NULL
        )
        """,
        """
        CREATE TABLE organization_memberships (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL,
            user_id UUID NOT NULL,
            membership_state VARCHAR(50) NOT NULL,
            organization_auth_state VARCHAR(50) NOT NULL,
            invited_by UUID NULL,
            invited_at TIMESTAMPTZ NULL,
            accepted_at TIMESTAMPTZ NULL,
            removed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            options JSONB NOT NULL DEFAULT '{}'::jsonb,
            flags BIGINT NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE teams (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL,
            is_active BOOLEAN NOT NULL
        )
        """,
        """
        CREATE TABLE team_memberships (
            id UUID PRIMARY KEY,
            grantee_organization_id UUID NOT NULL,
            team_id UUID NOT NULL,
            user_id UUID NOT NULL
        )
        """,
        """
        CREATE TABLE knowledge_collections (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL,
            source_identity_id UUID NULL,
            lifecycle_state VARCHAR(50) NOT NULL,
            sync_state VARCHAR(50) NOT NULL,
            is_system_managed BOOLEAN NOT NULL,
            safe_metadata JSONB NOT NULL
        )
        """,
        """
        CREATE TABLE knowledge_collection_items (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL,
            collection_id UUID NOT NULL,
            knowledge_base_id UUID NOT NULL,
            rank INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE knowledge_bases (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL,
            active_document_version_id UUID NULL,
            source_identity_id UUID NULL,
            sync_state VARCHAR(50) NOT NULL,
            lifecycle_state VARCHAR(50) NOT NULL
        )
        """,
        """
        CREATE TABLE document_versions (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL,
            knowledge_base_id UUID NOT NULL,
            status VARCHAR(32) NOT NULL
        )
        """,
        """
        CREATE TABLE documents (
            id UUID PRIMARY KEY,
            knowledge_base_id UUID NOT NULL,
            status VARCHAR(50) NOT NULL
        )
        """,
        """
        CREATE TABLE document_chunks (
            id UUID PRIMARY KEY,
            document_id UUID NOT NULL,
            document_version_id UUID NULL,
            knowledge_base_id UUID NOT NULL
        )
        """,
        """
        CREATE TABLE team_knowledge_collection_permissions (
            knowledge_collection_id UUID NOT NULL,
            team_id UUID NOT NULL,
            grantee_organization_id UUID NOT NULL,
            permission_action VARCHAR(32) NOT NULL
        )
        """,
        """
        CREATE TABLE user_knowledge_collection_permissions (
            knowledge_collection_id UUID NOT NULL,
            user_id UUID NOT NULL,
            grantee_organization_id UUID NOT NULL,
            permission_action VARCHAR(32) NOT NULL
        )
        """,
        """
        CREATE TABLE team_knowledge_permissions (
            knowledge_base_id UUID NOT NULL,
            team_id UUID NOT NULL,
            grantee_organization_id UUID NOT NULL,
            auth_state VARCHAR(50) NOT NULL
        )
        """,
        """
        CREATE TABLE user_knowledge_permissions (
            knowledge_base_id UUID NOT NULL,
            user_id UUID NOT NULL,
            grantee_organization_id UUID NOT NULL,
            auth_state VARCHAR(50) NOT NULL
        )
        """,
        """
        CREATE TABLE source_policy_kb_use_grants (
            knowledge_base_id UUID NOT NULL,
            source_identity_id UUID NULL,
            organization_id UUID NOT NULL,
            permission_action VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL,
            expires_at TIMESTAMPTZ NULL,
            subject_type VARCHAR(32) NOT NULL,
            subject_id UUID NOT NULL
        )
        """,
        """
        CREATE TABLE source_authorization_provenance (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL,
            knowledge_base_id UUID NOT NULL,
            source_identity_id UUID NULL,
            requester_subject_type VARCHAR(32) NOT NULL,
            requester_subject_id UUID NOT NULL,
            source_acl_state VARCHAR(32) NOT NULL,
            requester_source_authorization VARCHAR(32) NOT NULL,
            source_permission_action VARCHAR(64) NULL,
            freshness_epoch BIGINT NOT NULL,
            freshness_expires_at TIMESTAMPTZ NULL,
            status VARCHAR(32) NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE snapshot_write_probe (
            id UUID PRIMARY KEY
        )
        """,
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _seed_ready_public_collection(engine):
    organization_id = uuid4()
    collection_id = uuid4()
    knowledge_base_id = uuid4()
    version_id = uuid4()
    item_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization (id, is_active) "
                "VALUES (:organization_id, true)"
            ),
            {"organization_id": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_collections "
                "(id, organization_id, lifecycle_state, sync_state, "
                "is_system_managed, safe_metadata) "
                "VALUES (:collection_id, :organization_id, 'active', "
                "'manual', false, '{\"visibility\": \"public\"}'::jsonb)"
            ),
            {
                "collection_id": collection_id,
                "organization_id": organization_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, organization_id, active_document_version_id, "
                "source_identity_id, sync_state, lifecycle_state) "
                "VALUES (:knowledge_base_id, :organization_id, :version_id, "
                "NULL, 'manual', 'active')"
            ),
            {
                "knowledge_base_id": knowledge_base_id,
                "organization_id": organization_id,
                "version_id": version_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_versions "
                "(id, organization_id, knowledge_base_id, status) "
                "VALUES (:version_id, :organization_id, "
                ":knowledge_base_id, 'ready')"
            ),
            {
                "version_id": version_id,
                "organization_id": organization_id,
                "knowledge_base_id": knowledge_base_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_collection_items "
                "(id, organization_id, collection_id, knowledge_base_id, "
                "rank, created_at) "
                "VALUES (:item_id, :organization_id, :collection_id, "
                ":knowledge_base_id, 0, clock_timestamp())"
            ),
            {
                "item_id": item_id,
                "organization_id": organization_id,
                "collection_id": collection_id,
                "knowledge_base_id": knowledge_base_id,
            },
        )
    return organization_id, collection_id, knowledge_base_id


def _seed_two_public_collections(engine):
    organization_id = uuid4()
    collection_ids = (uuid4(), uuid4())
    knowledge_base_ids_by_collection = []
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization (id, is_active) "
                "VALUES (:organization_id, true)"
            ),
            {"organization_id": organization_id},
        )
        for collection_id in collection_ids:
            connection.execute(
                text(
                    "INSERT INTO knowledge_collections "
                    "(id, organization_id, lifecycle_state, sync_state, "
                    "is_system_managed, safe_metadata) "
                    "VALUES (:collection_id, :organization_id, 'active', "
                    "'manual', false, "
                    "'{\"visibility\": \"public\"}'::jsonb)"
                ),
                {
                    "collection_id": collection_id,
                    "organization_id": organization_id,
                },
            )
            collection_kb_ids = []
            # More than scan_cap + 1 in the bounded-scan test so the per-
            # Collection LATERAL limit is exercised by real PostgreSQL.
            for rank in range(6):
                knowledge_base_id = uuid4()
                version_id = uuid4()
                collection_kb_ids.append(knowledge_base_id)
                connection.execute(
                    text(
                        "INSERT INTO knowledge_bases "
                        "(id, organization_id, active_document_version_id, "
                        "source_identity_id, sync_state, lifecycle_state) "
                        "VALUES (:knowledge_base_id, :organization_id, "
                        ":version_id, NULL, 'manual', 'active')"
                    ),
                    {
                        "knowledge_base_id": knowledge_base_id,
                        "organization_id": organization_id,
                        "version_id": version_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO document_versions "
                        "(id, organization_id, knowledge_base_id, status) "
                        "VALUES (:version_id, :organization_id, "
                        ":knowledge_base_id, 'ready')"
                    ),
                    {
                        "version_id": version_id,
                        "organization_id": organization_id,
                        "knowledge_base_id": knowledge_base_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO knowledge_collection_items "
                        "(id, organization_id, collection_id, "
                        "knowledge_base_id, rank, created_at) "
                        "VALUES (:item_id, :organization_id, "
                        ":collection_id, :knowledge_base_id, :rank, "
                        "clock_timestamp())"
                    ),
                    {
                        "item_id": uuid4(),
                        "organization_id": organization_id,
                        "collection_id": collection_id,
                        "knowledge_base_id": knowledge_base_id,
                        "rank": rank,
                    },
                )
            knowledge_base_ids_by_collection.append(tuple(collection_kb_ids))
    return (
        organization_id,
        collection_ids,
        tuple(knowledge_base_ids_by_collection),
    )


def _seed_authenticated_collection(engine, *, source_managed: bool):
    owner_id = uuid4()
    requester_id = uuid4()
    organization_id = uuid4()
    membership_id = uuid4()
    collection_id = uuid4()
    knowledge_base_id = uuid4()
    version_id = uuid4()
    item_id = uuid4()
    source_identity_id = uuid4() if source_managed else None
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, name, social_provider) VALUES "
                "(:owner_id, :owner_email, 'owner', 'test'), "
                "(:requester_id, :requester_email, 'requester', 'test')"
            ),
            {
                "owner_id": owner_id,
                "owner_email": f"owner-{owner_id.hex}@test.invalid",
                "requester_id": requester_id,
                "requester_email": f"requester-{requester_id.hex}@test.invalid",
            },
        )
        connection.execute(
            text(
                "INSERT INTO organization "
                "(id, created_by, is_active) "
                "VALUES (:organization_id, :owner_id, true)"
            ),
            {
                "organization_id": organization_id,
                "owner_id": owner_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO organization_memberships "
                "(id, organization_id, user_id, membership_state, "
                "organization_auth_state) "
                "VALUES (:membership_id, :organization_id, :requester_id, "
                "'active', 'member')"
            ),
            {
                "membership_id": membership_id,
                "organization_id": organization_id,
                "requester_id": requester_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_collections "
                "(id, organization_id, lifecycle_state, sync_state, "
                "is_system_managed, safe_metadata) "
                "VALUES (:collection_id, :organization_id, 'active', "
                "'manual', false, '{\"visibility\": \"private\"}'::jsonb)"
            ),
            {
                "collection_id": collection_id,
                "organization_id": organization_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, organization_id, active_document_version_id, "
                "source_identity_id, sync_state, lifecycle_state) "
                "VALUES (:knowledge_base_id, :organization_id, :version_id, "
                ":source_identity_id, 'manual', 'active')"
            ),
            {
                "knowledge_base_id": knowledge_base_id,
                "organization_id": organization_id,
                "version_id": version_id,
                "source_identity_id": source_identity_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_versions "
                "(id, organization_id, knowledge_base_id, status) "
                "VALUES (:version_id, :organization_id, "
                ":knowledge_base_id, 'ready')"
            ),
            {
                "version_id": version_id,
                "organization_id": organization_id,
                "knowledge_base_id": knowledge_base_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_collection_items "
                "(id, organization_id, collection_id, knowledge_base_id, "
                "rank, created_at) "
                "VALUES (:item_id, :organization_id, :collection_id, "
                ":knowledge_base_id, 0, clock_timestamp())"
            ),
            {
                "item_id": item_id,
                "organization_id": organization_id,
                "collection_id": collection_id,
                "knowledge_base_id": knowledge_base_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO user_knowledge_collection_permissions "
                "(knowledge_collection_id, user_id, "
                "grantee_organization_id, permission_action) "
                "VALUES (:collection_id, :requester_id, "
                ":organization_id, 'route')"
            ),
            {
                "collection_id": collection_id,
                "requester_id": requester_id,
                "organization_id": organization_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO user_knowledge_permissions "
                "(knowledge_base_id, user_id, grantee_organization_id, "
                "auth_state) VALUES (:knowledge_base_id, :requester_id, "
                ":organization_id, 'operator')"
            ),
            {
                "knowledge_base_id": knowledge_base_id,
                "requester_id": requester_id,
                "organization_id": organization_id,
            },
        )
        if source_identity_id is not None:
            connection.execute(
                text(
                    "INSERT INTO source_authorization_provenance "
                    "(id, organization_id, knowledge_base_id, "
                    "source_identity_id, requester_subject_type, "
                    "requester_subject_id, source_acl_state, "
                    "requester_source_authorization, "
                    "source_permission_action, freshness_epoch, "
                    "freshness_expires_at, status) VALUES "
                    "(:provenance_id, :organization_id, "
                    ":knowledge_base_id, :source_identity_id, 'user', "
                    ":requester_id, 'fresh', 'allowed', 'read', 1, "
                    "clock_timestamp() + interval '1 hour', 'active')"
                ),
                {
                    "provenance_id": uuid4(),
                    "organization_id": organization_id,
                    "knowledge_base_id": knowledge_base_id,
                    "source_identity_id": source_identity_id,
                    "requester_id": requester_id,
                },
            )
    return {
        "organization_id": organization_id,
        "requester_id": requester_id,
        "collection_id": collection_id,
        "knowledge_base_id": knowledge_base_id,
    }


def _seed_authenticated_user_matrix(engine):
    organization_ids = {"a": uuid4(), "b": uuid4()}
    user_ids = {
        "dev_a": uuid4(),
        "planning_a": uuid4(),
        "direct_a": uuid4(),
        "none_a": uuid4(),
        "dev_b": uuid4(),
    }
    team_ids = {"dev_a": uuid4(), "planning_a": uuid4(), "dev_b": uuid4()}
    collection_ids = {"department_a": uuid4(), "department_b": uuid4()}
    knowledge_base_ids = {
        "common_a": uuid4(),
        "dev_a": uuid4(),
        "planning_a": uuid4(),
        "direct_a": uuid4(),
        "hidden_a": uuid4(),
        "common_b": uuid4(),
    }

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, name, social_provider) "
                "VALUES (:id, :email, :name, 'test')"
            ),
            [
                {
                    "id": user_id,
                    "email": f"{alias}-{user_id.hex}@test.invalid",
                    "name": alias,
                }
                for alias, user_id in user_ids.items()
            ],
        )
        connection.execute(
            text(
                "INSERT INTO organization (id, is_active) "
                "VALUES (:id, true)"
            ),
            [{"id": organization_id} for organization_id in organization_ids.values()],
        )
        connection.execute(
            text(
                "INSERT INTO organization_memberships "
                "(id, organization_id, user_id, membership_state, "
                "organization_auth_state) VALUES "
                "(:id, :organization_id, :user_id, 'active', 'member')"
            ),
            [
                {
                    "id": uuid4(),
                    "organization_id": organization_ids["a"],
                    "user_id": user_ids[alias],
                }
                for alias in ("dev_a", "planning_a", "direct_a", "none_a")
            ]
            + [
                {
                    "id": uuid4(),
                    "organization_id": organization_ids["b"],
                    "user_id": user_ids["dev_b"],
                }
            ],
        )
        connection.execute(
            text(
                "INSERT INTO teams (id, organization_id, is_active) "
                "VALUES (:id, :organization_id, true)"
            ),
            [
                {
                    "id": team_ids["dev_a"],
                    "organization_id": organization_ids["a"],
                },
                {
                    "id": team_ids["planning_a"],
                    "organization_id": organization_ids["a"],
                },
                {
                    "id": team_ids["dev_b"],
                    "organization_id": organization_ids["b"],
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO team_memberships "
                "(id, grantee_organization_id, team_id, user_id) "
                "VALUES (:id, :organization_id, :team_id, :user_id)"
            ),
            [
                {
                    "id": uuid4(),
                    "organization_id": organization_ids["a"],
                    "team_id": team_ids["dev_a"],
                    "user_id": user_ids["dev_a"],
                },
                {
                    "id": uuid4(),
                    "organization_id": organization_ids["a"],
                    "team_id": team_ids["planning_a"],
                    "user_id": user_ids["planning_a"],
                },
                {
                    "id": uuid4(),
                    "organization_id": organization_ids["b"],
                    "team_id": team_ids["dev_b"],
                    "user_id": user_ids["dev_b"],
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_collections "
                "(id, organization_id, lifecycle_state, sync_state, "
                "is_system_managed, safe_metadata) VALUES "
                "(:id, :organization_id, 'active', 'manual', false, "
                "'{\"visibility\": \"private\"}'::jsonb)"
            ),
            [
                {
                    "id": collection_ids["department_a"],
                    "organization_id": organization_ids["a"],
                },
                {
                    "id": collection_ids["department_b"],
                    "organization_id": organization_ids["b"],
                },
            ],
        )

        version_ids = {alias: uuid4() for alias in knowledge_base_ids}
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, organization_id, active_document_version_id, "
                "source_identity_id, sync_state, lifecycle_state) VALUES "
                "(:id, :organization_id, :version_id, NULL, 'manual', 'active')"
            ),
            [
                {
                    "id": knowledge_base_id,
                    "organization_id": (
                        organization_ids["b"]
                        if alias.endswith("_b")
                        else organization_ids["a"]
                    ),
                    "version_id": version_ids[alias],
                }
                for alias, knowledge_base_id in knowledge_base_ids.items()
            ],
        )
        connection.execute(
            text(
                "INSERT INTO document_versions "
                "(id, organization_id, knowledge_base_id, status) "
                "VALUES (:id, :organization_id, :knowledge_base_id, 'ready')"
            ),
            [
                {
                    "id": version_ids[alias],
                    "organization_id": (
                        organization_ids["b"]
                        if alias.endswith("_b")
                        else organization_ids["a"]
                    ),
                    "knowledge_base_id": knowledge_base_id,
                }
                for alias, knowledge_base_id in knowledge_base_ids.items()
            ],
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_collection_items "
                "(id, organization_id, collection_id, knowledge_base_id, "
                "rank, created_at) VALUES "
                "(:id, :organization_id, :collection_id, :knowledge_base_id, "
                ":rank, clock_timestamp())"
            ),
            [
                {
                    "id": uuid4(),
                    "organization_id": organization_ids["a"],
                    "collection_id": collection_ids["department_a"],
                    "knowledge_base_id": knowledge_base_ids[alias],
                    "rank": rank,
                }
                for rank, alias in enumerate(
                    ("common_a", "dev_a", "planning_a", "hidden_a")
                )
            ]
            + [
                {
                    "id": uuid4(),
                    "organization_id": organization_ids["b"],
                    "collection_id": collection_ids["department_b"],
                    "knowledge_base_id": knowledge_base_ids["common_b"],
                    "rank": 0,
                }
            ],
        )
        connection.execute(
            text(
                "INSERT INTO team_knowledge_collection_permissions "
                "(knowledge_collection_id, team_id, grantee_organization_id, "
                "permission_action) VALUES "
                "(:collection_id, :team_id, :organization_id, 'route')"
            ),
            [
                {
                    "collection_id": collection_ids["department_a"],
                    "team_id": team_ids["dev_a"],
                    "organization_id": organization_ids["a"],
                },
                {
                    "collection_id": collection_ids["department_a"],
                    "team_id": team_ids["planning_a"],
                    "organization_id": organization_ids["a"],
                },
                {
                    "collection_id": collection_ids["department_b"],
                    "team_id": team_ids["dev_b"],
                    "organization_id": organization_ids["b"],
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO team_knowledge_permissions "
                "(knowledge_base_id, team_id, grantee_organization_id, auth_state) "
                "VALUES (:knowledge_base_id, :team_id, :organization_id, 'operator')"
            ),
            [
                {
                    "knowledge_base_id": knowledge_base_ids[kb_alias],
                    "team_id": team_ids[team_alias],
                    "organization_id": organization_ids[organization_alias],
                }
                for team_alias, organization_alias, kb_alias in (
                    ("dev_a", "a", "common_a"),
                    ("dev_a", "a", "dev_a"),
                    ("planning_a", "a", "common_a"),
                    ("planning_a", "a", "planning_a"),
                    ("dev_b", "b", "common_b"),
                )
            ],
        )
        connection.execute(
            text(
                "INSERT INTO user_knowledge_permissions "
                "(knowledge_base_id, user_id, grantee_organization_id, auth_state) "
                "VALUES (:knowledge_base_id, :user_id, :organization_id, 'operator')"
            ),
            {
                "knowledge_base_id": knowledge_base_ids["direct_a"],
                "user_id": user_ids["direct_a"],
                "organization_id": organization_ids["a"],
            },
        )

    return {
        "organizations": organization_ids,
        "users": user_ids,
        "collections": collection_ids,
        "knowledge_bases": knowledge_base_ids,
    }


def _apply_authenticated_mutation(connection, mutation, seeded):
    params = {
        "organization_id": seeded["organization_id"],
        "requester_id": seeded["requester_id"],
        "collection_id": seeded["collection_id"],
        "knowledge_base_id": seeded["knowledge_base_id"],
    }
    statements = {
        "collection_route": (
            "DELETE FROM user_knowledge_collection_permissions "
            "WHERE grantee_organization_id = :organization_id "
            "AND user_id = :requester_id "
            "AND knowledge_collection_id = :collection_id"
        ),
        "kb_use": (
            "DELETE FROM user_knowledge_permissions "
            "WHERE grantee_organization_id = :organization_id "
            "AND user_id = :requester_id "
            "AND knowledge_base_id = :knowledge_base_id"
        ),
        "organization_membership": (
            "UPDATE organization_memberships "
            "SET membership_state = 'removed', removed_at = clock_timestamp() "
            "WHERE organization_id = :organization_id "
            "AND user_id = :requester_id"
        ),
        "source_provenance": (
            "UPDATE source_authorization_provenance "
            "SET source_acl_state = 'revoked', "
            "requester_source_authorization = 'denied', "
            "freshness_epoch = freshness_epoch + 1, "
            "updated_at = clock_timestamp() "
            "WHERE organization_id = :organization_id "
            "AND requester_subject_id = :requester_id "
            "AND knowledge_base_id = :knowledge_base_id"
        ),
        "collection_lifecycle": (
            "UPDATE knowledge_collections SET lifecycle_state = 'archived' "
            "WHERE organization_id = :organization_id "
            "AND id = :collection_id"
        ),
        "kb_lifecycle": (
            "UPDATE knowledge_bases SET lifecycle_state = 'archived' "
            "WHERE organization_id = :organization_id "
            "AND id = :knowledge_base_id"
        ),
    }
    connection.execute(text(statements[mutation]), params)


def _snapshot_contains(snapshot, knowledge_base_id):
    return knowledge_base_id in snapshot.eligible_direct_kb_ids or any(
        knowledge_base_id in stream.eligible_kb_ids
        for stream in snapshot.collection_streams
    )


@pytest.fixture
def disposable_snapshot_database():
    try:
        config = DisposablePostgresConfig.from_environment()
    except DisposablePostgresConfigurationError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL connection settings are not safely configured",
            pytrace=False,
        ) from None

    database = f"{DB_PREFIX}_{uuid4().hex[:12]}"
    quoted_database = quote_disposable_database_name(database, prefix=DB_PREFIX)
    admin_engine = create_engine(
        config.database_url(config.maintenance_database),
        isolation_level="AUTOCOMMIT",
    )
    engine = None
    database_created = False
    try:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f"CREATE DATABASE {quoted_database}"))
            database_created = True
            engine = create_engine(config.database_url(database), pool_size=2)
            _create_schema(engine)
        except SQLAlchemyError:
            raise pytest.fail.Exception(
                "disposable PostgreSQL setup failed",
                pytrace=False,
            ) from None
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        try:
            if database_created:
                with admin_engine.connect() as connection:
                    connection.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) "
                            "FROM pg_stat_activity "
                            "WHERE datname = :database "
                            "AND pid <> pg_backend_pid()"
                        ),
                        {"database": database},
                    )
                    connection.execute(text(f"DROP DATABASE {quoted_database}"))
        except SQLAlchemyError:
            raise pytest.fail.Exception(
                "disposable PostgreSQL cleanup failed",
                pytrace=False,
            ) from None
        finally:
            admin_engine.dispose()


class _ProviderMustNotRun:
    def __init__(self) -> None:
        self.calls = 0

    def invoke_sync(self, **_kwargs):
        self.calls += 1
        raise AssertionError("LLM provider must not run without usable evidence")


class _ProviderRuntimeMustNotResolve:
    def __init__(self) -> None:
        self.preflight_calls = 0
        self.resolve_calls = 0

    def preflight(self, request):
        self.preflight_calls += 1
        assert request.client_override is not None
        return ProviderExecutionPlan(
            fixed_model_id=request.configured_model_id,
            allow_legacy_memory_summary=False,
            state=object(),
        )

    def resolve(self, _request):
        self.resolve_calls += 1
        raise AssertionError("provider resolution must not run without usable evidence")


class _QueryEmbeddingRuntimeForSnapshot:
    def __init__(self) -> None:
        self.preflight_calls = 0
        self.execute_calls = 0

    def preflight(self, request):
        self.preflight_calls += 1
        assert request.legacy_credential_user_id is not None
        return QueryEmbeddingPlan(
            capability_required=False,
            organization_id=request.organization_id,
            node_id=request.node_id,
            state=object(),
        )

    def execute(self, _request):
        self.execute_calls += 1
        raise AssertionError("query embedding execution is replaced by this fixture")


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable Knowledge user matrix evidence",
)
def test_internal_chatbot_user_matrix_reaches_llm_node_with_authorized_candidates_only(
    disposable_snapshot_database,
    monkeypatch,
):
    engine = disposable_snapshot_database
    seeded = _seed_authenticated_user_matrix(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    resolver = KnowledgeRuntimeCandidateResolver(
        snapshot_port=PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
            session_factory=session_factory
        )
    )
    organizations = seeded["organizations"]
    users = seeded["users"]
    collections = seeded["collections"]
    kbs = seeded["knowledge_bases"]
    direct_kb_ids = (kbs["direct_a"], kbs["hidden_a"])
    collection_ids = (collections["department_a"], collections["department_b"])

    matrix = (
        (
            "dev_a",
            organizations["a"],
            ((kbs["common_a"], "collection"), (kbs["dev_a"], "collection")),
        ),
        (
            "planning_a",
            organizations["a"],
            (
                (kbs["common_a"], "collection"),
                (kbs["planning_a"], "collection"),
            ),
        ),
        ("direct_a", organizations["a"], ((kbs["direct_a"], "direct"),)),
        ("none_a", organizations["a"], ()),
        ("dev_b", organizations["a"], ()),
        ("dev_b", organizations["b"], ((kbs["common_b"], "collection"),)),
    )

    for user_alias, organization_id, expected in matrix:
        resolution = resolver.resolve(
            KnowledgeRuntimeCandidateRequest(
                audience=AuthenticatedAudience(
                    organization_id=organization_id,
                    user_id=users[user_alias],
                ),
                direct_kb_ids=direct_kb_ids,
                collection_ids=collection_ids,
            )
        )

        assert tuple(
            (candidate.knowledge_base_id, candidate.provenance.kind)
            for candidate in resolution.candidates
        ) == expected
        assert resolution.policy_excluded_count_bucket in {
            "0",
            "1",
            "2-10",
            "11-100",
            "100+",
        }
        assert str(kbs["hidden_a"]) not in repr(resolution)

    node_cases = (
        ("dev_a", (kbs["common_a"], kbs["dev_a"])),
        ("planning_a", (kbs["common_a"], kbs["planning_a"])),
        ("none_a", ()),
    )
    for user_alias, expected_kb_ids in node_cases:
        provider = _ProviderMustNotRun()
        provider_runtime = _ProviderRuntimeMustNotResolve()
        query_embedding_runtime = _QueryEmbeddingRuntimeForSnapshot()
        node = LLMNode(
            "llm-mba-238",
            LLMNodeData(
                title="LLM",
                provider="openai",
                model_id="gpt-4o",
                user_prompt="policy question",
                knowledgeBases=[
                    KnowledgeBaseRef(id=str(kbs["direct_a"]), name="Direct"),
                    KnowledgeBaseRef(
                        id=str(kbs["hidden_a"]),
                        name="DENIED_SENTINEL_HIDDEN",
                    ),
                ],
                knowledgeCollections=[
                    KnowledgeCollectionRef(
                        id=str(collections["department_a"]),
                        safeLabel="Department A",
                    ),
                    KnowledgeCollectionRef(
                        id=str(collections["department_b"]),
                        safeLabel="DENIED_SENTINEL_CROSS_ORG",
                    ),
                ],
            ),
            execution_context={
                "user_id": str(users[user_alias]),
                "organization_id": str(organizations["a"]),
                "execution_subject": {
                    "type": "user",
                    "id": str(users[user_alias]),
                },
                "db": object(),
            },
        )
        node.bind_knowledge_runtime_candidate_resolver(resolver)
        node.bind_provider_execution_runtime(provider_runtime)
        node.bind_query_embedding_runtime(query_embedding_runtime)
        node._client_override = provider  # noqa: SLF001
        precompute_calls = []
        fanout_calls = []

        if expected_kb_ids:
            monkeypatch.setattr(
                node,
                "_precompute_rag_query_vectors_by_kb",
                lambda *args, **kwargs: precompute_calls.append(
                    tuple(kwargs["knowledge_base_ids"])
                )
                or ({}, {}, 0, False),
            )

            def capture_fanout(**kwargs):
                fanout_calls.append(tuple(kwargs["knowledge_base_ids"]))
                return WorkflowRAGFanoutResult(results=[], failed_count=0)

            monkeypatch.setattr(node, "_run_rag_retrieval_fanout", capture_fanout)
        else:
            monkeypatch.setattr(
                node,
                "_precompute_rag_query_vectors_by_kb",
                lambda *args, **kwargs: pytest.fail(
                    "embedding must not run for zero candidates"
                ),
            )
            monkeypatch.setattr(
                node,
                "_run_rag_retrieval_fanout",
                lambda **kwargs: pytest.fail(
                    "retrieval must not run for zero candidates"
                ),
            )

        result = node._run({})  # noqa: SLF001

        assert provider.calls == 0
        assert provider_runtime.preflight_calls == 1
        assert provider_runtime.resolve_calls == 0
        assert query_embedding_runtime.preflight_calls == (
            1 if expected_kb_ids else 0
        )
        assert query_embedding_runtime.execute_calls == 0
        assert result["text"] == RAG_NO_EVIDENCE_MESSAGE
        assert fanout_calls == (
            [tuple(str(kb_id) for kb_id in expected_kb_ids)]
            if expected_kb_ids
            else []
        )
        assert precompute_calls == fanout_calls

        public_projection = json.dumps(
            {"result": result, "trace": node._trace_payloads},  # noqa: SLF001
            ensure_ascii=False,
            default=str,
        )
        assert "DENIED_SENTINEL" not in public_projection
        assert all(
            str(resource_id) not in public_projection
            for resource_id in (*kbs.values(), *collections.values())
        )


class _PausingSnapshotAdapter(PostgresKnowledgeRuntimeCandidateSnapshotAdapter):
    def __init__(self, *, session_factory, snapshot_started, writer_finished):
        super().__init__(session_factory=session_factory)
        self.snapshot_started = snapshot_started
        self.writer_finished = writer_finished
        self.isolation_level = None
        self.transaction_read_only = None
        self.evaluation_time = None

    def _load_policy_evaluation_time(self, db):
        self.evaluation_time = super()._load_policy_evaluation_time(db)
        return self.evaluation_time

    def _load_selected_collections(
        self,
        db,
        organization_id,
        collection_ids,
    ):
        self.isolation_level = db.execute(
            text("SHOW transaction_isolation")
        ).scalar_one()
        self.transaction_read_only = db.execute(
            text("SHOW transaction_read_only")
        ).scalar_one()
        self.snapshot_started.set()
        if not self.writer_finished.wait(timeout=15):
            raise RuntimeError("writer_timeout")
        return super()._load_selected_collections(
            db,
            organization_id,
            collection_ids,
        )


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable Knowledge snapshot evidence",
)
def test_repeatable_read_snapshot_does_not_mix_concurrent_membership_change(
    disposable_snapshot_database,
):
    engine = disposable_snapshot_database
    organization_id, collection_id, knowledge_base_id = (
        _seed_ready_public_collection(engine)
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    snapshot_started = Event()
    writer_finished = Event()
    adapter = _PausingSnapshotAdapter(
        session_factory=session_factory,
        snapshot_started=snapshot_started,
        writer_finished=writer_finished,
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=organization_id),
        collection_ids=(collection_id,),
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(adapter.load_snapshot, request)
        assert snapshot_started.wait(timeout=15)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM knowledge_collection_items "
                    "WHERE organization_id = :organization_id "
                    "AND collection_id = :collection_id"
                ),
                {
                    "organization_id": organization_id,
                    "collection_id": collection_id,
                },
            )
        writer_finished.set()
        first_snapshot = future.result(timeout=15)

    assert adapter.isolation_level == "repeatable read"
    assert adapter.transaction_read_only == "on"
    assert first_snapshot.collection_streams[0].eligible_kb_ids == (
        knowledge_base_id,
    )

    next_snapshot = PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
        session_factory=session_factory
    ).load_snapshot(request)
    assert next_snapshot.collection_streams[0].eligible_kb_ids == ()

    probe_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO snapshot_write_probe (id) VALUES (:probe_id)"),
            {"probe_id": probe_id},
        )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT id FROM snapshot_write_probe WHERE id = :probe_id"),
            {"probe_id": probe_id},
        ).scalar_one() == probe_id


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable Knowledge public policy evidence",
)
def test_anonymous_snapshot_excludes_source_managed_public_collection(
    disposable_snapshot_database,
):
    engine = disposable_snapshot_database
    organization_id, collection_id, _knowledge_base_id = (
        _seed_ready_public_collection(engine)
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE knowledge_collections "
                "SET source_identity_id = :source_identity_id "
                "WHERE organization_id = :organization_id "
                "AND id = :collection_id"
            ),
            {
                "source_identity_id": uuid4(),
                "organization_id": organization_id,
                "collection_id": collection_id,
            },
        )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=organization_id),
        collection_ids=(collection_id,),
    )

    snapshot = PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
        session_factory=session_factory
    ).load_snapshot(request)

    assert snapshot.collection_streams == ()
    assert snapshot.policy_excluded_count == 1


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable Knowledge bounded scan evidence",
)
def test_lateral_membership_scan_is_bounded_and_fair_in_postgres(
    disposable_snapshot_database,
):
    engine = disposable_snapshot_database
    organization_id, collection_ids, kb_ids_by_collection = (
        _seed_two_public_collections(engine)
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=organization_id),
        collection_ids=collection_ids,
        candidate_budget=4,
        candidate_scan_cap=4,
    )

    snapshot = PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
        session_factory=session_factory
    ).load_snapshot(request)

    assert snapshot.scan_limited is True
    assert tuple(stream.collection_id for stream in snapshot.collection_streams) == (
        collection_ids
    )
    assert tuple(
        stream.eligible_kb_ids for stream in snapshot.collection_streams
    ) == (
        kb_ids_by_collection[0][:2],
        kb_ids_by_collection[1][:2],
    )


@pytest.mark.parametrize(
    ("mutation", "source_managed"),
    [
        ("collection_route", False),
        ("kb_use", False),
        ("organization_membership", False),
        ("source_provenance", True),
        ("collection_lifecycle", False),
        ("kb_lifecycle", False),
    ],
)
@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable Knowledge authorization evidence",
)
def test_authenticated_snapshot_keeps_one_revision_and_next_call_sees_mutation(
    disposable_snapshot_database,
    mutation,
    source_managed,
):
    engine = disposable_snapshot_database
    seeded = _seed_authenticated_collection(
        engine,
        source_managed=source_managed,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    snapshot_started = Event()
    writer_finished = Event()
    adapter = _PausingSnapshotAdapter(
        session_factory=session_factory,
        snapshot_started=snapshot_started,
        writer_finished=writer_finished,
    )
    request = KnowledgeRuntimeCandidateRequest(
        audience=AuthenticatedAudience(
            organization_id=seeded["organization_id"],
            user_id=seeded["requester_id"],
        ),
        collection_ids=(seeded["collection_id"],),
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(adapter.load_snapshot, request)
        assert snapshot_started.wait(timeout=15)
        try:
            with engine.begin() as connection:
                _apply_authenticated_mutation(connection, mutation, seeded)
        finally:
            writer_finished.set()
        first_snapshot = future.result(timeout=15)

    assert adapter.isolation_level == "repeatable read"
    assert adapter.transaction_read_only == "on"
    assert adapter.evaluation_time is not None
    assert adapter.evaluation_time.utcoffset() is not None
    assert _snapshot_contains(first_snapshot, seeded["knowledge_base_id"])

    next_snapshot = PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
        session_factory=session_factory
    ).load_snapshot(request)
    assert not _snapshot_contains(next_snapshot, seeded["knowledge_base_id"])


class _WriteAttemptSnapshotAdapter(PostgresKnowledgeRuntimeCandidateSnapshotAdapter):
    def _organization_is_active(self, db, organization_id: UUID) -> bool:
        del organization_id
        db.execute(
            text("INSERT INTO snapshot_write_probe (id) VALUES (:probe_id)"),
            {"probe_id": uuid4()},
        )
        return True


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable Knowledge read-only evidence",
)
def test_snapshot_transaction_rejects_write_and_returns_fixed_error(
    disposable_snapshot_database,
):
    engine = disposable_snapshot_database
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    adapter = _WriteAttemptSnapshotAdapter(session_factory=session_factory)
    request = KnowledgeRuntimeCandidateRequest(
        audience=AnonymousPublicAudience(organization_id=uuid4()),
    )

    with pytest.raises(KnowledgeRuntimeCandidateSnapshotError) as exc_info:
        adapter.load_snapshot(request)

    assert exc_info.value.reason_code == "snapshot_read_failed"
    assert exc_info.value.__cause__ is None
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM snapshot_write_probe")
        ).scalar_one() == 0
