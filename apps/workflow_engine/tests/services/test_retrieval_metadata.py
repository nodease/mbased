import asyncio
import logging
import uuid
from types import SimpleNamespace

import pytest

from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.llm import LLMCredential, LLMModel, LLMProvider
from apps.shared.schemas.rag import ChunkPreview
from apps.workflow_engine.services.llm_service import LLMService
from apps.workflow_engine.services.retrieval import RetrievalService


@pytest.mark.parametrize("use_sync", [True, False])
def test_retrieval_failure_log_does_not_include_raw_exception(
    use_sync,
    caplog,
):
    class FailingDb:
        def query(self, _model):
            raise RuntimeError("private-query-and-resource-detail")

    service = RetrievalService(
        FailingDb(),
        uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    with caplog.at_level(
        logging.ERROR,
        logger="apps.workflow_engine.services.retrieval",
    ):
        with pytest.raises(RuntimeError):
            if use_sync:
                service.search_documents_sync(
                    "private-query",
                    knowledge_base_id=str(uuid.uuid4()),
                )
            else:
                asyncio.run(
                    service.search_documents(
                        "private-query",
                        knowledge_base_id=str(uuid.uuid4()),
                    )
                )

    assert "private-query-and-resource-detail" not in " ".join(caplog.messages)
    assert "error_type=RuntimeError" in " ".join(caplog.messages)


def test_sync_retrieval_requires_organization_before_database_access() -> None:
    class UnexpectedDb:
        def query(self, _model):
            raise AssertionError("organization-less retrieval must not query")

    result = RetrievalService(
        UnexpectedDb(),
        uuid.uuid4(),
        organization_id=None,
    ).search_documents_sync(
        "query",
        knowledge_base_id=str(uuid.uuid4()),
    )

    assert result == []


def test_sync_retrieval_scopes_knowledge_base_lookup_to_organization() -> None:
    criteria = []

    class ScopedQuery:
        def filter(self, *values):
            criteria.extend(values)
            return self

        def first(self):
            return None

    class ScopedDb:
        def query(self, model):
            assert model is KnowledgeBase
            return ScopedQuery()

    knowledge_base_id = str(uuid.uuid4())
    organization_id = uuid.uuid4()
    result = RetrievalService(
        ScopedDb(),
        uuid.uuid4(),
        organization_id=organization_id,
    ).search_documents_sync(
        "query",
        knowledge_base_id=knowledge_base_id,
    )

    assert result == []
    assert _criterion_compares_column(criteria, "id", knowledge_base_id)
    assert _criterion_compares_column(
        criteria,
        "organization_id",
        organization_id,
    )


class _FakeQuery:
    def __init__(self, rows, criteria):
        self.rows = rows
        self.criteria = criteria

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def all(self):
        return self.rows


class _FakeDb:
    def __init__(self, credential_rows, provider_rows, criteria):
        self.credential_rows = credential_rows
        self.provider_rows = provider_rows
        self.criteria = criteria

    def query(self, model):
        if model is LLMCredential:
            return _FakeQuery(self.credential_rows, self.criteria)
        if model is LLMProvider:
            return _FakeQuery(self.provider_rows, self.criteria)
        raise AssertionError(f"unexpected model: {model}")


def _criterion_compares_column(criteria, column_name, value):
    for criterion in criteria:
        left = getattr(criterion, "left", None)
        right = getattr(criterion, "right", None)
        if (
            getattr(left, "name", None) == column_name
            and getattr(right, "value", None) == value
        ):
            return True
    return False


def test_chunk_metadata_uses_hierarchy_columns_for_summary():
    service = RetrievalService(db=None, user_id=None)
    parent_chunk_id = uuid.uuid4()
    chunk = SimpleNamespace(
        metadata_={"page": 2, "classification": "internal", "tags": ["chunk"]},
        parent_chunk_id=parent_chunk_id,
        chunk_level="child",
        section_path=["Policy", "Access"],
        heading="Access rules",
        token_count=123,
    )
    document = SimpleNamespace(
        meta_info={"classification": "confidential", "tags": ["document"]},
        source_type="FILE",
    )

    metadata = service._chunk_metadata(chunk, document)

    assert metadata["classification"] == "confidential"
    assert metadata["tags"] == ["document"]
    assert metadata["page"] == 2
    assert metadata["source_type"] == "FILE"
    assert metadata["parent_chunk_id"] == str(parent_chunk_id)
    assert metadata["token_count"] == 123
    assert metadata["chunk_level"] == "child"
    assert metadata["section_path"] == ["Policy", "Access"]
    assert metadata["heading"] == "Access rules"
    assert service._hierarchy_path(metadata) == ["Policy", "Access"]
    summary = service._metadata_summary(metadata)
    assert summary["classification"] == "confidential"
    assert summary["parent_chunk_id"] == str(parent_chunk_id)
    assert summary["token_count"] == 123
    assert summary["chunk_level"] == "child"
    assert "api_config" not in service._metadata_summary({"api_config": {"key": "x"}})


def test_chunk_metadata_interprets_null_chunk_level_as_flat():
    service = RetrievalService(db=None, user_id=None)
    chunk = SimpleNamespace(metadata_={}, chunk_level=None)

    metadata = service._chunk_metadata(chunk)

    assert metadata["chunk_level"] == "flat"


def test_metadata_summary_preserves_hierarchy_fallback_flag():
    service = RetrievalService(db=None, user_id=None)

    summary = service._metadata_summary(
        {
            "hierarchy_fallback": True,
            "classification": "internal",
            "content": "원문",
        }
    )

    assert summary["hierarchy_fallback"] is True
    assert summary["classification"] == "internal"
    assert "content" not in summary


def test_search_method_labels_hierarchical_paths(monkeypatch):
    monkeypatch.delenv("RAG_CROSS_ENCODER_RERANK_ENABLED", raising=False)
    assert (
        RetrievalService._search_method(
            use_hierarchy=True,
            hybrid_search=True,
            use_rerank=True,
        )
        == "hierarchical_hybrid"
    )
    monkeypatch.setenv("RAG_CROSS_ENCODER_RERANK_ENABLED", "true")
    assert (
        RetrievalService._search_method(
            use_hierarchy=True,
            hybrid_search=True,
            use_rerank=True,
        )
        == "hierarchical_hybrid+rerank"
    )
    assert (
        RetrievalService._search_method(
            use_hierarchy=True,
            hybrid_search=False,
        )
        == "hierarchical"
    )


def test_rewrite_query_passes_active_organization_to_llm(monkeypatch):
    captured = {}
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    class FakeClient:
        async def invoke(self, messages, max_tokens):
            return {"choices": [{"message": {"content": "rewritten"}}]}

    def fake_get_client_for_user(db, user_id, model_id, organization_id=None):
        captured["user_id"] = user_id
        captured["model_id"] = model_id
        captured["organization_id"] = organization_id
        return FakeClient()

    monkeypatch.setattr(LLMService, "get_client_for_user", fake_get_client_for_user)

    service = RetrievalService(
        db=object(),
        user_id=user_id,
        organization_id=organization_id,
    )
    service._get_efficient_rewrite_model = lambda: "rewrite-model"

    assert asyncio.run(service._rewrite_query("original")) == "rewritten"
    assert captured == {
        "user_id": user_id,
        "model_id": "rewrite-model",
        "organization_id": organization_id,
    }


def test_rewrite_model_selection_filters_credentials_by_active_organization():
    criteria = []
    organization_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    service = RetrievalService(
        db=_FakeDb(
            credential_rows=[SimpleNamespace(provider_id=provider_id)],
            provider_rows=[SimpleNamespace(id=provider_id, name="OpenAI")],
            criteria=criteria,
        ),
        user_id=uuid.uuid4(),
        organization_id=organization_id,
    )

    assert (
        service._get_efficient_rewrite_model() == LLMService.EFFICIENT_MODELS["openai"]
    )
    assert _criterion_compares_column(criteria, "organization_id", organization_id)


def test_generate_answer_preserves_references_when_generation_model_missing(
    monkeypatch,
):
    chunk = ChunkPreview(
        content="검색 결과",
        document_id=uuid.uuid4(),
        filename="guide.md",
        similarity_score=0.9,
    )
    service = RetrievalService(
        db=object(), user_id=uuid.uuid4(), organization_id=uuid.uuid4()
    )

    async def fake_search_documents(*args, **kwargs):
        return [chunk]

    service.search_documents = fake_search_documents
    monkeypatch.setattr(
        LLMService,
        "get_client_for_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("missing credential")
        ),
    )

    response = asyncio.run(service.generate_answer("query", "kb-1"))

    assert response.references == [chunk]


def test_search_documents_sync_filters_hybrid_rerank_results_by_threshold(
    monkeypatch,
):
    kb_id = uuid.uuid4()

    class FakeClient:
        def embed_sync(self, query):
            return [0.1, 0.2]

    class FakeQuery:
        def __init__(self, model):
            self.model = model

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            if self.model is KnowledgeBase:
                return SimpleNamespace(id=kb_id, embedding_model="embedding-model")
            if self.model is LLMModel:
                return SimpleNamespace(type="embedding", is_active=True)
            return None

    class FakeDb:
        def query(self, model):
            return FakeQuery(model)

    def fake_chunk(content):
        return SimpleNamespace(
            id=uuid.uuid4(),
            content=content,
            metadata_={},
            parent_chunk_id=None,
            chunk_level="flat",
            token_count=1,
        )

    doc = SimpleNamespace(
        id=uuid.uuid4(),
        filename="policy.md",
        meta_info={},
        source_type="FILE",
    )
    low_chunk = fake_chunk("낮은 점수 근거")
    high_chunk = fake_chunk("충분한 점수 근거")

    service = RetrievalService(
        db=FakeDb(),
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(service, "_has_valid_hierarchy", lambda *_args: False)
    monkeypatch.setattr(
        service,
        "_vector_search",
        lambda *_args, **_kwargs: [(low_chunk, doc, 0.2), (high_chunk, doc, 0.1)],
    )
    monkeypatch.setattr(service, "_keyword_search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        service,
        "_rrf_fusion",
        lambda *_args, **_kwargs: [
            {"score": 0.2, "chunk": low_chunk, "doc": doc},
            {"score": 0.1, "chunk": high_chunk, "doc": doc},
        ],
    )
    monkeypatch.setattr(
        service,
        "_rerank",
        lambda *_args, **_kwargs: [
            {
                "score": 0.2,
                "rerank_score": 0.49,
                "chunk": low_chunk,
                "doc": doc,
            },
            {
                "score": 0.1,
                "rerank_score": 0.91,
                "chunk": high_chunk,
                "doc": doc,
            },
        ],
    )
    monkeypatch.setattr(service, "_decrypt_content", lambda content: content)
    monkeypatch.setattr(
        LLMService,
        "get_client_for_user",
        lambda *args, **kwargs: FakeClient(),
    )

    result = service.search_documents_sync(
        "policy",
        knowledge_base_id=str(kb_id),
        threshold=0.5,
    )

    assert [chunk.content for chunk in result] == ["충분한 점수 근거"]
    assert result[0].score == 0.91
    assert result[0].rank == 1


def test_retrieve_context_passes_top_k_as_keyword(monkeypatch):
    captured = {}
    service = RetrievalService(db=object(), user_id=uuid.uuid4())

    async def fake_search_documents(
        query,
        *,
        knowledge_base_id,
        top_k,
        metadata_filter=None,
        hierarchy_mode="auto",
        **_kwargs,
    ):
        captured.update(
            {
                "query": query,
                "knowledge_base_id": knowledge_base_id,
                "top_k": top_k,
                "metadata_filter": metadata_filter,
                "hierarchy_mode": hierarchy_mode,
            }
        )
        return [SimpleNamespace(content="context")]

    monkeypatch.setattr(service, "search_documents", fake_search_documents)

    result = asyncio.run(
        service.retrieve_context(
            "policy",
            "kb-1",
            top_k=7,
            metadata_filter={"classification": ["internal"]},
            hierarchy_mode="flat",
        )
    )

    assert result == "context"
    assert captured == {
        "query": "policy",
        "knowledge_base_id": "kb-1",
        "top_k": 7,
        "metadata_filter": {"classification": ["internal"]},
        "hierarchy_mode": "flat",
    }
