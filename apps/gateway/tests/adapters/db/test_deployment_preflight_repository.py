import uuid
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from apps.gateway.adapters.db import deployment_preflight_repository as repository_module
from apps.gateway.adapters.db.deployment_preflight_repository import (
    SqlAlchemyDeploymentPreflightRepository,
)
from apps.shared.db.models.knowledge import KnowledgeBase


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.criteria = []
        self.joins = []

    def join(self, *args):
        self.joins.append(args)
        return self

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def group_by(self, *_args):
        return self

    def all(self):
        return self.rows


class _Db:
    def __init__(self, *query_rows):
        self.query_rows = list(query_rows)
        self.queries = []

    def query(self, *_args):
        rows = self.query_rows.pop(0) if self.query_rows else []
        query = _Query(rows)
        self.queries.append(query)
        return query


def _compiled_criteria(query):
    return " ".join(
        str(
            criterion.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        for criterion in query.criteria
    ).lower()


def _compiled_join_on(query):
    assert len(query.joins) == 1
    target, on_clause = query.joins[0]
    assert target is KnowledgeBase
    return str(
        on_clause.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _assert_active_runtime_eligible_kb_scope(query):
    sql = _compiled_criteria(query)
    assert "knowledge_bases.lifecycle_state = 'active'" in sql
    assert "knowledge_bases.sync_state != 'source_deleted'" in sql
    assert "documents.status = 'completed'" in sql
    assert "document_chunks.document_version_id is null" in sql
    assert (
        "document_chunks.document_version_id = "
        "knowledge_bases.active_document_version_id" in sql
    )
    assert "document_versions.status = 'ready'" in sql


def test_direct_preflight_query_excludes_source_deleted_kb():
    db = _Db([])
    repository = SqlAlchemyDeploymentPreflightRepository(db)

    result = repository.get_active_knowledge_bases(
        [uuid.uuid4()],
        uuid.uuid4(),
    )

    assert result == {}
    _assert_active_runtime_eligible_kb_scope(db.queries[0])


def test_public_runtime_preflight_query_excludes_source_deleted_membership():
    db = _Db([])
    repository = SqlAlchemyDeploymentPreflightRepository(db)

    result = repository.get_public_runtime_eligible_knowledge_base_ids(
        [uuid.uuid4()],
        uuid.uuid4(),
    )

    assert result == set()
    _assert_active_runtime_eligible_kb_scope(db.queries[0])
    join_sql = _compiled_join_on(db.queries[0])
    assert (
        "knowledge_bases.id = knowledge_collection_items.knowledge_base_id"
        in join_sql
    )
    assert (
        "knowledge_bases.organization_id = "
        "knowledge_collection_items.organization_id" in join_sql
    )


def test_public_runtime_preflight_excludes_source_managed_parent_collection():
    knowledge_base_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    item = SimpleNamespace(
        knowledge_base_id=knowledge_base_id,
        collection_id=collection_id,
    )
    source_managed_collection = SimpleNamespace(
        id=collection_id,
        safe_metadata={"visibility": "public"},
        source_identity_id=uuid.uuid4(),
    )
    db = _Db([item], [source_managed_collection])
    repository = SqlAlchemyDeploymentPreflightRepository(db)

    result = repository.get_public_runtime_eligible_knowledge_base_ids(
        [knowledge_base_id],
        uuid.uuid4(),
    )

    assert result == set()


def test_collection_preflight_aggregate_excludes_source_deleted_members():
    collection_id = uuid.uuid4()
    collection = SimpleNamespace(
        id=collection_id,
        safe_metadata={},
        source_identity_id=None,
    )
    db = _Db([collection], [])
    repository = SqlAlchemyDeploymentPreflightRepository(db)

    result = repository.get_active_knowledge_collections(
        [collection_id],
        uuid.uuid4(),
    )

    assert result[collection_id].candidate_member_count == 0
    assert result[collection_id].has_source_managed_members is False
    _assert_active_runtime_eligible_kb_scope(db.queries[1])
    join_sql = _compiled_join_on(db.queries[1])
    assert (
        "knowledge_bases.id = knowledge_collection_items.knowledge_base_id"
        in join_sql
    )
    assert (
        "knowledge_bases.organization_id = "
        "knowledge_collection_items.organization_id" in join_sql
    )


def test_collection_preflight_excludes_source_deleted_parent():
    db = _Db([])
    repository = SqlAlchemyDeploymentPreflightRepository(db)

    assert repository.get_active_knowledge_collections(
        [uuid.uuid4()],
        uuid.uuid4(),
    ) == {}

    sql = _compiled_criteria(db.queries[0])
    assert "knowledge_collections.lifecycle_state = 'active'" in sql
    assert "knowledge_collections.sync_state != 'source_deleted'" in sql


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.query_count = 0

    def query(self, *args):
        self.query_count += 1
        return _Query(self.rows)


def test_mail_snapshots_use_one_bulk_permission_decision(monkeypatch):
    organization_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    credential_ids = [uuid.uuid4(), uuid.uuid4()]
    rows = [
        SimpleNamespace(
            id=credential_id,
            provider="imap",
            auth_type="password",
        )
        for credential_id in credential_ids
    ]
    session = _Session(rows)
    calls = []

    def _bulk(db, user_id, ids, scoped_organization_id):
        materialized_ids = tuple(ids)
        calls.append((db, user_id, materialized_ids, scoped_organization_id))
        return {
            credential_ids[0]: "operator",
            credential_ids[1]: "viewer",
        }

    monkeypatch.setattr(
        repository_module,
        "get_effective_mail_credential_auth_states",
        _bulk,
    )

    result = SqlAlchemyDeploymentPreflightRepository(
        session
    ).get_mail_credential_snapshots(
        credential_ids,
        organization_id,
        principal_id,
    )

    assert session.query_count == 1
    assert len(calls) == 1
    assert calls[0][1:] == (
        principal_id,
        tuple(credential_ids),
        organization_id,
    )
    assert result[credential_ids[0]].usable_by_principal is True
    assert result[credential_ids[1]].usable_by_principal is False
    assert result[credential_ids[1]].effective_auth_state == "viewer"
