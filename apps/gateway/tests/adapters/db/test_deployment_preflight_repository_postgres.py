"""Opt-in PostgreSQL evidence for deployment preflight Knowledge predicates."""

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from apps.gateway.adapters.db.deployment_preflight_repository import (
    SqlAlchemyDeploymentPreflightRepository,
)
from apps.gateway.services.knowledge_collection_picker_query_service import (
    MAX_LLM_SELECTABLE_COLLECTION_SCAN,
    KnowledgeCollectionPickerQueryService,
)
from apps.gateway.services.workflow_knowledge_reference_service import (
    WorkflowKnowledgeReferenceService,
    WorkflowKnowledgeReferenceUnavailable,
)
from apps.shared.db.models.knowledge import (
    Document,
    DocumentChunk,
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
)
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MEMBER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.team import UserKnowledgeCollectionPermission
from apps.shared.db.models.user import User
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)

ROOT_DIR = Path(__file__).resolve().parents[5]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_preflight"


def _run_alembic(database: str, config: DisposablePostgresConfig) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "apps/shared/alembic.ini",
            "upgrade",
            "heads",
        ],
        cwd=ROOT_DIR,
        env=config.subprocess_environment(database=database, root_dir=ROOT_DIR),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"alembic failed with exit code {result.returncode}; "
            "stdout/stderr omitted to avoid leaking local configuration"
        )


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL preflight integration",
)
def test_save_and_preflight_queries_filter_deleted_and_cross_org_resources():
    try:
        config = DisposablePostgresConfig.from_environment()
    except DisposablePostgresConfigurationError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL connection settings are not safely configured",
            pytrace=False,
        ) from None

    database = f"{DB_PREFIX}_{uuid.uuid4().hex[:12]}"
    quoted_database = quote_disposable_database_name(database, prefix=DB_PREFIX)
    admin_engine = create_engine(
        config.database_url(config.maintenance_database),
        isolation_level="AUTOCOMMIT",
    )
    database_created = False
    engine = None
    db = None

    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE {quoted_database}"))
        database_created = True

        engine = create_engine(config.database_url(database))
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        engine.dispose()
        engine = None

        _run_alembic(database, config)
        engine = create_engine(config.database_url(database))
        db = sessionmaker(bind=engine, expire_on_commit=False)()

        owner_id = uuid.uuid4()
        member_id = uuid.uuid4()
        owner = User(
            id=owner_id,
            email=f"preflight-{owner_id}@example.test",
            name="Preflight owner",
            social_provider="local",
        )
        member = User(
            id=member_id,
            email=f"picker-{member_id}@example.test",
            name="Picker member",
            social_provider="local",
        )
        db.add_all([owner, member])
        db.flush()

        organization_id = uuid.uuid4()
        other_organization_id = uuid.uuid4()
        db.add_all(
            [
                Organization(
                    id=organization_id,
                    name="Preflight organization",
                    created_by=owner_id,
                ),
                Organization(
                    id=other_organization_id,
                    name="Other organization",
                    created_by=owner_id,
                ),
            ]
        )
        db.flush()
        db.add(
            OrganizationMembership(
                organization_id=organization_id,
                user_id=member_id,
                membership_state=ORGANIZATION_MEMBERSHIP_ACTIVE,
                organization_auth_state=ORGANIZATION_AUTH_MEMBER,
                invited_by=owner_id,
            )
        )
        db.flush()

        active_kb_id = uuid.uuid4()
        deleted_kb_id = uuid.uuid4()
        archived_kb_id = uuid.uuid4()
        other_org_kb_id = uuid.uuid4()
        db.add_all(
            [
                KnowledgeBase(
                    id=active_kb_id,
                    organization_id=organization_id,
                    user_id=owner_id,
                    name="Active KB",
                    sync_state="manual",
                    lifecycle_state="active",
                ),
                KnowledgeBase(
                    id=deleted_kb_id,
                    organization_id=organization_id,
                    user_id=owner_id,
                    name="Source deleted KB",
                    sync_state="source_deleted",
                    lifecycle_state="active",
                ),
                KnowledgeBase(
                    id=archived_kb_id,
                    organization_id=organization_id,
                    user_id=owner_id,
                    name="Archived KB",
                    sync_state="manual",
                    lifecycle_state="archived",
                ),
                KnowledgeBase(
                    id=other_org_kb_id,
                    organization_id=other_organization_id,
                    user_id=owner_id,
                    name="Other organization KB",
                    sync_state="manual",
                    lifecycle_state="active",
                ),
            ]
        )
        db.flush()

        for index, knowledge_base_id in enumerate(
            (
                active_kb_id,
                deleted_kb_id,
                archived_kb_id,
                other_org_kb_id,
            )
        ):
            document = Document(
                id=uuid.uuid4(),
                knowledge_base_id=knowledge_base_id,
                filename=f"ready-{index}.md",
                status="completed",
            )
            db.add(document)
            db.flush()
            db.add(
                DocumentChunk(
                    id=uuid.uuid4(),
                    document_id=document.id,
                    knowledge_base_id=knowledge_base_id,
                    content=f"retrieval-visible-{index}",
                    embedding=[0.0] * 1536,
                    chunk_index=0,
                )
            )
        db.flush()

        public_collection_id = uuid.uuid4()
        deleted_only_collection_id = uuid.uuid4()
        source_deleted_collection_id = uuid.uuid4()
        db.add_all(
            [
                KnowledgeCollection(
                    id=public_collection_id,
                    organization_id=organization_id,
                    name="Public mixed collection",
                    safe_metadata={"visibility": "public"},
                    created_by=owner_id,
                ),
                KnowledgeCollection(
                    id=deleted_only_collection_id,
                    organization_id=organization_id,
                    name="Deleted only collection",
                    safe_metadata={"visibility": "public"},
                    created_by=owner_id,
                ),
                KnowledgeCollection(
                    id=source_deleted_collection_id,
                    organization_id=organization_id,
                    name="Source deleted collection",
                    sync_state="source_deleted",
                    safe_metadata={"visibility": "public"},
                    created_by=owner_id,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                KnowledgeCollectionItem(
                    organization_id=organization_id,
                    collection_id=public_collection_id,
                    knowledge_base_id=active_kb_id,
                ),
                KnowledgeCollectionItem(
                    organization_id=organization_id,
                    collection_id=public_collection_id,
                    knowledge_base_id=deleted_kb_id,
                ),
                KnowledgeCollectionItem(
                    organization_id=organization_id,
                    collection_id=public_collection_id,
                    knowledge_base_id=archived_kb_id,
                ),
                KnowledgeCollectionItem(
                    organization_id=organization_id,
                    collection_id=public_collection_id,
                    knowledge_base_id=other_org_kb_id,
                ),
                KnowledgeCollectionItem(
                    organization_id=organization_id,
                    collection_id=deleted_only_collection_id,
                    knowledge_base_id=deleted_kb_id,
                ),
            ]
        )
        db.commit()

        repository = SqlAlchemyDeploymentPreflightRepository(db)
        save_service = WorkflowKnowledgeReferenceService(
            db,
            user_id=owner_id,
            organization_id=organization_id,
        )
        save_loaded = save_service._load_direct_kbs(  # noqa: SLF001 - SQL evidence
            (active_kb_id, deleted_kb_id, archived_kb_id, other_org_kb_id)
        )
        with pytest.raises(WorkflowKnowledgeReferenceUnavailable):
            save_service.validate_editable_graph(
                {
                    "nodes": [
                        {
                            "id": "llm",
                            "type": "llmNode",
                            "data": {
                                "knowledgeBases": [
                                    {
                                        "id": str(deleted_kb_id),
                                        "name": "Source deleted",
                                    }
                                ]
                            },
                        }
                    ]
                }
            )
        with pytest.raises(WorkflowKnowledgeReferenceUnavailable):
            save_service.validate_editable_graph(
                {
                    "nodes": [
                        {
                            "id": "llm",
                            "type": "llmNode",
                            "data": {
                                "knowledgeCollections": [
                                    {
                                        "id": str(source_deleted_collection_id),
                                        "safeLabel": "Source deleted",
                                    }
                                ]
                            },
                        }
                    ]
                }
            )
        direct = repository.get_active_knowledge_bases(
            [active_kb_id, deleted_kb_id, archived_kb_id, other_org_kb_id],
            organization_id,
        )
        public = repository.get_public_runtime_eligible_knowledge_base_ids(
            [active_kb_id, deleted_kb_id, archived_kb_id, other_org_kb_id],
            organization_id,
        )
        collections = repository.get_active_knowledge_collections(
            [
                public_collection_id,
                deleted_only_collection_id,
                source_deleted_collection_id,
            ],
            organization_id,
        )

        assert {row.id for row in save_loaded} == {active_kb_id}
        assert set(direct) == {active_kb_id}
        assert public == {active_kb_id}
        assert source_deleted_collection_id not in collections
        assert collections[public_collection_id].candidate_member_count == 1
        assert collections[deleted_only_collection_id].candidate_member_count == 0
        assert collections[public_collection_id].has_source_managed_members is False
        assert (
            collections[deleted_only_collection_id].has_source_managed_members
            is False
        )

        oldest_allowed_id = uuid.uuid4()
        oldest_allowed = KnowledgeCollection(
            id=oldest_allowed_id,
            organization_id=organization_id,
            name="Oldest route-authorized collection",
            safe_metadata={"safe_label": "Oldest allowed"},
            created_by=owner_id,
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        newer_denied = [
            KnowledgeCollection(
                id=uuid.uuid4(),
                organization_id=organization_id,
                name=f"Newer denied collection {index:03d}",
                safe_metadata={"safe_label": f"Denied {index:03d}"},
                created_by=owner_id,
                created_at=(
                    datetime(2030, 1, 1, tzinfo=timezone.utc)
                    + timedelta(seconds=index)
                ),
            )
            for index in range(MAX_LLM_SELECTABLE_COLLECTION_SCAN)
        ]
        db.add_all([oldest_allowed, *newer_denied])
        db.flush()
        db.add(
            UserKnowledgeCollectionPermission(
                grantee_organization_id=organization_id,
                user_id=member_id,
                assigned_by=owner_id,
                knowledge_collection_id=oldest_allowed_id,
                permission_action="route",
            )
        )
        db.add(
            UserKnowledgeCollectionPermission(
                grantee_organization_id=organization_id,
                user_id=member_id,
                assigned_by=owner_id,
                knowledge_collection_id=source_deleted_collection_id,
                permission_action="route",
            )
        )
        db.commit()

        picker = KnowledgeCollectionPickerQueryService(
            db,
            user_id=member_id,
            organization_id=organization_id,
        )
        scoped_response = picker.list_llm_selectable()
        assert [item.id for item in scoped_response.collections] == [oldest_allowed_id]

        db.add_all(
            [
                UserKnowledgeCollectionPermission(
                    grantee_organization_id=organization_id,
                    user_id=member_id,
                    assigned_by=owner_id,
                    knowledge_collection_id=collection.id,
                    permission_action="route",
                )
                for collection in newer_denied
            ]
        )
        db.commit()

        capped_response = picker.list_llm_selectable()
        assert len(capped_response.collections) == MAX_LLM_SELECTABLE_COLLECTION_SCAN
        assert oldest_allowed_id not in {item.id for item in capped_response.collections}
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable or rejected the connection; "
            "connection details omitted",
            pytrace=False,
        ) from None
    finally:
        if db is not None:
            db.close()
        if engine is not None:
            engine.dispose()
        if database_created:
            try:
                with admin_engine.connect() as conn:
                    conn.execute(
                        text(
                            """
                            SELECT pg_terminate_backend(pid)
                            FROM pg_stat_activity
                            WHERE datname = :database
                              AND pid <> pg_backend_pid()
                            """
                        ),
                        {"database": database},
                    )
                    conn.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup could not connect; "
                    "connection details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()
