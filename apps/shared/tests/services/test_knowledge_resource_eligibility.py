from types import SimpleNamespace

from apps.shared.db.models.knowledge import KnowledgeBase, KnowledgeCollection
from apps.shared.services.knowledge_resource_eligibility import (
    is_anonymous_public_knowledge_collection,
    is_operational_knowledge_resource,
    knowledge_base_operational_predicates,
    knowledge_collection_anonymous_public_predicates,
    knowledge_collection_operational_predicates,
    retrieval_visible_chunk_exists,
)
from sqlalchemy import select
from sqlalchemy.dialects import postgresql


def _postgres_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


def test_operational_predicates_share_lifecycle_and_source_deleted_boundary():
    kb_sql = _postgres_sql(
        select(KnowledgeBase.id).where(*knowledge_base_operational_predicates())
    )
    collection_sql = _postgres_sql(
        select(KnowledgeCollection.id).where(
            *knowledge_collection_operational_predicates()
        )
    )

    assert "knowledge_bases.lifecycle_state = 'active'" in kb_sql
    assert "knowledge_bases.sync_state != 'source_deleted'" in kb_sql
    assert "knowledge_collections.lifecycle_state = 'active'" in collection_sql
    assert "knowledge_collections.sync_state != 'source_deleted'" in collection_sql


def test_anonymous_public_collection_predicates_include_source_and_visibility_gate():
    sql = _postgres_sql(
        select(KnowledgeCollection.id).where(
            *knowledge_collection_anonymous_public_predicates()
        )
    )

    assert "knowledge_collections.lifecycle_state = 'active'" in sql
    assert "knowledge_collections.sync_state != 'source_deleted'" in sql
    assert "knowledge_collections.source_identity_id is null" in sql
    assert "knowledge_collections.safe_metadata" in sql
    assert "= 'public'" in sql


def test_retrieval_visible_chunk_predicate_covers_versioned_and_legacy_paths():
    sql = _postgres_sql(
        select(KnowledgeBase.id).where(retrieval_visible_chunk_exists())
    )

    assert "exists" in sql
    assert "documents.status = 'completed'" in sql
    assert "knowledge_bases.active_document_version_id is null" in sql
    assert "document_chunks.document_version_id is null" in sql
    assert "document_chunks.document_version_id = knowledge_bases.active_document_version_id" in sql
    assert "document_versions.status = 'ready'" in sql
    assert "document_versions.organization_id = knowledge_bases.organization_id" in sql


def test_loaded_resource_boundary_allows_stale_snapshot_but_not_deleted_source():
    active_stale = SimpleNamespace(lifecycle_state="active", sync_state="stale")
    source_deleted = SimpleNamespace(
        lifecycle_state="active",
        sync_state="source_deleted",
    )
    archived = SimpleNamespace(lifecycle_state="archived", sync_state="manual")

    assert is_operational_knowledge_resource(active_stale) is True
    assert is_operational_knowledge_resource(source_deleted) is False
    assert is_operational_knowledge_resource(archived) is False


def test_loaded_anonymous_public_collection_is_manual_public_and_fail_closed():
    manual_public = SimpleNamespace(
        lifecycle_state="active",
        sync_state="manual",
        source_identity_id=None,
        safe_metadata={"visibility": "public"},
    )
    source_managed = SimpleNamespace(
        lifecycle_state="active",
        sync_state="synced",
        source_identity_id="opaque-source",
        safe_metadata={"visibility": "public"},
    )
    missing_source_state = SimpleNamespace(
        lifecycle_state="active",
        sync_state="manual",
        safe_metadata={"visibility": "public"},
    )
    private = SimpleNamespace(
        lifecycle_state="active",
        sync_state="manual",
        source_identity_id=None,
        safe_metadata={"visibility": "private"},
    )

    assert is_anonymous_public_knowledge_collection(manual_public) is True
    assert is_anonymous_public_knowledge_collection(source_managed) is False
    assert is_anonymous_public_knowledge_collection(missing_source_state) is False
    assert is_anonymous_public_knowledge_collection(private) is False
