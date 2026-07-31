import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from apps.gateway.services.workflow_service import WorkflowService
from apps.gateway.services.workflow_knowledge_reference_service import (
    WorkflowKnowledgeReferenceService,
    WorkflowKnowledgeReferenceUnavailable,
)
from apps.shared.schemas.workflow import WorkflowDraftRequest


def _graph(kb_id, collection_id):
    return {
        "nodes": [
            {
                "id": "llm",
                "type": "llmNode",
                "data": {
                    "knowledgeBases": [{"id": str(kb_id), "name": "KB"}],
                    "knowledgeCollections": [
                        {"id": str(collection_id), "safeLabel": "Collection"}
                    ],
                },
            }
        ]
    }


def _service(monkeypatch, *, allow_kb=True, allow_collection=True, ready=True):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    service = WorkflowKnowledgeReferenceService(
        None,
        user_id=user_id,
        organization_id=organization_id,
    )
    kb = SimpleNamespace(id=kb_id, organization_id=organization_id)
    collection = SimpleNamespace(id=collection_id, organization_id=organization_id)
    monkeypatch.setattr(service, "_load_direct_kbs", lambda ids: [kb])
    monkeypatch.setattr(service, "_load_collections", lambda ids: [collection])
    monkeypatch.setattr(
        service,
        "_retrieval_ready_ids",
        lambda ids: {kb_id} if ready else set(),
    )
    monkeypatch.setattr(
        service.permission_helper,
        "bulk_evaluate_kb_use",
        lambda kbs: {kb_id: SimpleNamespace(allowed=allow_kb)},
    )
    monkeypatch.setattr(
        service.permission_helper,
        "bulk_evaluate_collection_action",
        lambda collections, action: {
            collection_id: SimpleNamespace(allowed=allow_collection)
        },
    )
    return service, kb_id, collection_id


def test_editable_graph_checks_direct_use_readiness_and_collection_route(monkeypatch):
    service, kb_id, collection_id = _service(monkeypatch)

    parsed = service.validate_editable_graph(_graph(kb_id, collection_id))

    assert len(parsed) == 1
    assert parsed[0].direct_kb_ids == (kb_id,)
    assert parsed[0].collection_ids == (collection_id,)


def test_agent_builder_graph_can_persist_unready_kb_without_bypassing_use_permission(
    monkeypatch,
):
    service, kb_id, collection_id = _service(monkeypatch, ready=False)

    parsed = service.validate_editable_graph(
        _graph(kb_id, collection_id),
        require_retrieval_ready=False,
    )

    assert parsed[0].direct_kb_ids == (kb_id,)

    denied_service, denied_kb_id, denied_collection_id = _service(
        monkeypatch,
        allow_kb=False,
        ready=False,
    )
    with pytest.raises(WorkflowKnowledgeReferenceUnavailable):
        denied_service.validate_editable_graph(
            _graph(denied_kb_id, denied_collection_id),
            require_retrieval_ready=False,
        )


@pytest.mark.parametrize(
    ("allow_kb", "allow_collection", "ready", "expected_field"),
    [
        (False, True, True, "graph.nodes[0].data.knowledgeBases[0]"),
        (True, True, False, "graph.nodes[0].data.knowledgeBases[0]"),
        (True, False, True, "graph.nodes[0].data.knowledgeCollections[0]"),
    ],
)
def test_unavailable_references_use_generic_code_and_index_path(
    monkeypatch,
    allow_kb,
    allow_collection,
    ready,
    expected_field,
):
    service, kb_id, collection_id = _service(
        monkeypatch,
        allow_kb=allow_kb,
        allow_collection=allow_collection,
        ready=ready,
    )

    with pytest.raises(WorkflowKnowledgeReferenceUnavailable) as error:
        service.validate_editable_graph(_graph(kb_id, collection_id))

    assert error.value.reason_code == "knowledge_reference_unavailable"
    assert error.value.field_path == expected_field
    assert str(kb_id) not in str(error.value)
    assert str(collection_id) not in str(error.value)


def test_empty_graph_does_not_query_permission_or_collection_children(monkeypatch):
    service = WorkflowKnowledgeReferenceService(
        None,
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        service,
        "_load_direct_kbs",
        lambda ids: pytest.fail("direct KB query must not run"),
    )
    monkeypatch.setattr(
        service,
        "_load_collections",
        lambda ids: pytest.fail("Collection query must not run"),
    )

    assert service.validate_editable_graph({"nodes": []}) == ()


@pytest.mark.parametrize(
    "graph",
    [
        {"nodes": []},
        {
            "nodes": [
                {
                    "id": "llm",
                    "type": "llmNode",
                    "data": {"knowledgeBases": [], "knowledgeCollections": []},
                }
            ]
        },
        {
            "nodes": [
                {
                    "id": "loop",
                    "type": "loopNode",
                    "data": {"subGraph": {"nodes": []}},
                }
            ]
        },
    ],
)
def test_workflow_service_skips_context_validation_for_graph_without_knowledge_refs(
    monkeypatch,
    graph,
):
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.WorkflowKnowledgeReferenceService",
        lambda *args, **kwargs: pytest.fail("authorization service must not be built"),
    )

    WorkflowService.validate_knowledge_references(
        object(),
        graph,
        user_id="legacy-user-without-uuid",
        organization_id=None,
    )


def test_workflow_service_still_rejects_malformed_empty_knowledge_shape_without_org():
    graph = {
        "nodes": [
            {
                "id": "llm",
                "type": "llmNode",
                "data": {
                    "knowledgeBases": "not-a-list",
                    "knowledgeCollections": [],
                },
            }
        ]
    }

    with pytest.raises(HTTPException) as error:
        WorkflowService.validate_knowledge_references(
            object(),
            graph,
            user_id="legacy-user-without-uuid",
            organization_id=None,
        )

    assert error.value.status_code == 422
    assert error.value.detail == {
        "code": "knowledge_reference_list_invalid",
        "field": "graph.nodes[0].data.knowledgeBases",
    }


def test_workflow_service_requires_context_when_knowledge_reference_exists():
    graph = {
        "nodes": [
            {
                "id": "llm",
                "type": "llmNode",
                "data": {
                    "knowledgeBases": [
                        {"id": str(uuid.uuid4()), "name": "KB"}
                    ],
                    "knowledgeCollections": [],
                },
            }
        ]
    }

    with pytest.raises(HTTPException) as error:
        WorkflowService.validate_knowledge_references(
            object(),
            graph,
            user_id=uuid.uuid4(),
            organization_id=None,
        )

    assert error.value.status_code == 422
    assert error.value.detail == {
        "code": "knowledge_reference_context_invalid",
        "field": "graph",
    }


def test_save_draft_defers_readiness_only_for_agent_builder_mutation(monkeypatch):
    workflow_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    updated_at = datetime.now(timezone.utc)
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        graph={"nodes": [], "edges": []},
        updated_at=updated_at,
    )

    class _Query:
        def filter(self, *_args):
            return self

        def populate_existing(self):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return workflow

    class _Db:
        def query(self, *_args):
            return _Query()

    request = WorkflowDraftRequest.model_validate(
        {
            "nodes": [],
            "edges": [],
            "expected_graph_hash": "a" * 64,
            "expected_updated_at": updated_at,
            "mutation_context": {
                "operation_id": str(uuid.uuid4()),
                "action": "apply",
                "expected_base_graph_hash": "a" * 64,
                "expected_workflow_updated_at": updated_at,
            },
        }
    )
    captured = {}

    def _capture_validation(*_args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after validation policy")

    monkeypatch.setattr(
        WorkflowService,
        "validate_knowledge_references",
        staticmethod(_capture_validation),
    )
    monkeypatch.setattr(
        "apps.gateway.services.workflow_service.has_workflow_permission",
        lambda *args, **kwargs: True,
    )

    with pytest.raises(RuntimeError, match="stop after validation policy"):
        WorkflowService.save_draft(
            _Db(),
            str(workflow_id),
            request,
            user_id=str(uuid.uuid4()),
        )

    assert captured["require_retrieval_ready"] is False


class _CriteriaQuery:
    def __init__(self, rows=()):
        self.criteria = []
        self.rows = list(rows)

    def select_from(self, *_args):
        return self

    def join(self, *_args):
        return self

    def outerjoin(self, *_args):
        return self

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def distinct(self):
        return self

    def all(self):
        return self.rows


class _CriteriaDb:
    def __init__(self, rows=()):
        self.queries = []
        self.rows = rows

    def query(self, *_args):
        query = _CriteriaQuery(self.rows)
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
    )


@pytest.mark.parametrize(
    "method_name",
    ["_load_direct_kbs", "_retrieval_ready_ids"],
)
def test_save_time_direct_kb_queries_exclude_source_deleted(method_name):
    db = _CriteriaDb()
    service = WorkflowKnowledgeReferenceService(
        db,
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    result = getattr(service, method_name)((uuid.uuid4(),))

    assert result == ([] if method_name == "_load_direct_kbs" else set())
    sql = _compiled_criteria(db.queries[0])
    assert "knowledge_bases.lifecycle_state = 'active'" in sql
    assert "knowledge_bases.sync_state != 'source_deleted'" in sql
    if method_name == "_retrieval_ready_ids":
        assert "documents.status = 'completed'" in sql
        assert "document_versions.status = 'ready'" in sql


def test_save_time_collection_query_excludes_source_deleted_parent():
    db = _CriteriaDb()
    service = WorkflowKnowledgeReferenceService(
        db,
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    assert service._load_collections((uuid.uuid4(),)) == []

    sql = _compiled_criteria(db.queries[0])
    assert "knowledge_collections.lifecycle_state = 'active'" in sql
    assert "knowledge_collections.sync_state != 'source_deleted'" in sql


def test_retrieval_ready_ids_extracts_sqlalchemy_row_value():
    knowledge_base_id = uuid.uuid4()
    service = WorkflowKnowledgeReferenceService(
        _CriteriaDb([(knowledge_base_id,)]),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    assert service._retrieval_ready_ids((knowledge_base_id,)) == {
        knowledge_base_id
    }
