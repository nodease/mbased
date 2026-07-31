from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql

from apps.gateway.services.retrieval import RetrievalService
from apps.shared.db.models.knowledge import (
    DocumentChunk,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
    KnowledgeIngestionOutbox,
    KnowledgeSourceIdentity,
    SourceAuthorizationProvenance,
    SourcePolicyKBUseGrant,
)


def _constraint_names(model):
    return {constraint.name for constraint in model.__table__.constraints}


def test_knowledge_integration_phase1_tables_are_named_for_target_model():
    assert KnowledgeCollection.__tablename__ == "knowledge_collections"
    assert KnowledgeCollectionItem.__tablename__ == "knowledge_collection_items"
    assert KnowledgeSourceIdentity.__tablename__ == "knowledge_source_identities"
    assert DocumentVersion.__tablename__ == "document_versions"
    assert SourcePolicyKBUseGrant.__tablename__ == "source_policy_kb_use_grants"
    assert (
        SourceAuthorizationProvenance.__tablename__
        == "source_authorization_provenance"
    )
    assert KnowledgeIngestionOutbox.__tablename__ == "knowledge_ingestion_outbox"


def test_knowledge_base_keeps_legacy_path_and_adds_target_pointers():
    columns = KnowledgeBase.__table__.columns

    assert "active_document_version_id" in columns
    assert "source_identity_id" in columns
    assert "sync_state" in columns
    assert "lifecycle_state" in columns
    assert "documents" in KnowledgeBase.__mapper__.relationships
    assert "document_versions" in KnowledgeBase.__mapper__.relationships

    names = _constraint_names(KnowledgeBase)
    assert "ck_knowledge_bases_sync_state" in names
    assert "ck_knowledge_bases_lifecycle_state" in names
    assert "uq_knowledge_bases_source_identity_id" in names


def test_document_version_status_and_active_pointer_invariants_are_represented():
    names = _constraint_names(DocumentVersion)

    assert "ck_document_versions_status" in names
    assert "ck_document_versions_version_positive" in names
    assert "uq_document_versions_org_kb_version" in names
    assert "document_version_id" in DocumentChunk.__table__.columns
    assert DocumentChunk.__table__.columns["document_version_id"].nullable is True


def test_source_policy_grant_and_authorization_provenance_are_separate():
    grant_columns = SourcePolicyKBUseGrant.__table__.columns
    provenance_columns = SourceAuthorizationProvenance.__table__.columns

    assert grant_columns["permission_action"].default.arg == "use"
    assert "requester_source_authorization" not in grant_columns

    assert "requester_source_authorization" in provenance_columns
    assert "source_acl_state" in provenance_columns
    assert "permission_action" not in provenance_columns

    provenance_checks = [
        constraint
        for constraint in SourceAuthorizationProvenance.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    assert any(
        "source_acl_state IN" in str(constraint.sqltext)
        and "denied" not in str(constraint.sqltext)
        for constraint in provenance_checks
    )
    assert any(
        "requester_source_authorization IN" in str(constraint.sqltext)
        and "denied" in str(constraint.sqltext)
        for constraint in provenance_checks
    )


def test_collection_membership_does_not_encode_content_access():
    columns = KnowledgeCollectionItem.__table__.columns

    assert "collection_id" in columns
    assert "knowledge_base_id" in columns
    assert "permission_action" not in columns
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_knowledge_collection_items_collection_kb"
        for constraint in KnowledgeCollectionItem.__table__.constraints
    )


def test_outbox_has_retry_dead_letter_and_fencing_columns():
    columns = KnowledgeIngestionOutbox.__table__.columns

    for column_name in (
        "status",
        "owner_token",
        "fencing_token",
        "lease_expires_at",
        "attempt_count",
        "max_attempts",
        "next_retry_at",
        "retryable",
        "safe_reason_code",
        "dead_lettered_at",
        "redrive_requested_at",
    ):
        assert column_name in columns

    names = _constraint_names(KnowledgeIngestionOutbox)
    assert "ck_knowledge_ingestion_outbox_status" in names
    assert "ck_knowledge_ingestion_outbox_attempt_nonnegative" in names
    assert "ck_knowledge_ingestion_outbox_max_attempts_positive" in names


def test_retrieval_visible_condition_allows_legacy_or_active_ready_version():
    compiled = str(
        RetrievalService._retrieval_visible_chunk_condition().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "document_chunks.document_version_id IS NULL" in compiled
    assert "knowledge_bases.active_document_version_id IS NULL" in compiled
    assert "knowledge_bases.active_document_version_id" in compiled
    assert "document_versions.status = 'ready'" in compiled


def test_keyword_search_applies_active_version_visibility_filter():
    captured = {}

    class FakeResult:
        def fetchall(self):
            return []

    class FakeDb:
        def execute(self, stmt, params):
            captured["stmt"] = str(stmt)
            captured["params"] = params
            return FakeResult()

    service = RetrievalService(db=FakeDb(), user_id=None)
    result = service._keyword_search("policy", "kb-id", 3)

    assert result == []
    assert "LEFT JOIN document_versions dv" in captured["stmt"]
    assert "JOIN knowledge_bases kb" in captured["stmt"]
    assert "status = 'completed'" in captured["stmt"]
    assert "kb.active_document_version_id IS NULL" in captured["stmt"]
    assert "dc.document_version_id IS NULL" in captured["stmt"]
    assert "kb.active_document_version_id = dc.document_version_id" in captured["stmt"]
    assert "dv.status = 'ready'" in captured["stmt"]
    assert captured["params"] == {"query": "policy", "kb_id": "kb-id", "top_k": 3}


def test_vector_search_filters_completed_documents():
    captured = {}

    class FakeResult:
        def all(self):
            return []

    class FakeDb:
        def execute(self, stmt):
            captured["stmt"] = stmt
            return FakeResult()

    service = RetrievalService(db=FakeDb(), user_id=None)
    result = service._vector_search([0.1, 0.2], "kb-id", 3)

    compiled = str(
        captured["stmt"].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).replace('"', "")

    assert result == []
    assert "documents.status = 'completed'" in compiled
    assert "knowledge_bases.active_document_version_id" in compiled


def test_hierarchy_availability_uses_active_ready_version_filter():
    captured = {}

    class FakeQuery:
        def join(self, *args, **kwargs):
            return self

        def outerjoin(self, *args, **kwargs):
            return self

        def filter(self, *criteria):
            captured["criteria"] = criteria
            return self

        def first(self):
            return None

    class FakeDb:
        def query(self, *args, **kwargs):
            return FakeQuery()

    service = RetrievalService(db=FakeDb(), user_id=None)
    assert service._has_valid_hierarchy("kb-id") is False

    compiled = "\n".join(
        str(
            criterion.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        for criterion in captured["criteria"]
    ).replace('"', "")

    assert "document_chunks.document_version_id IS NULL" in compiled
    assert "active_document_version_id IS NULL" in compiled
    assert "knowledge_bases.active_document_version_id" in compiled
    assert "status = 'ready'" in compiled
    assert "documents.status = 'completed'" in compiled
