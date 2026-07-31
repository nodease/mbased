import builtins
import logging
import pathlib
import sys
import uuid
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PARENT_OF_ROOT = ROOT.parent
for p in [ROOT, PARENT_OF_ROOT]:
    if str(p) not in sys.path:
        sys.path.append(str(p))

from apps.shared.db.models.knowledge import KnowledgeBase  # noqa: E402
from apps.shared.db.models.llm import LLMModel  # noqa: E402
from apps.shared.services.retrieval_embedding_model_projection import (  # noqa: E402
    EmbeddingModelBinding,
)
from apps.workflow_engine.services.llm_service import LLMService  # noqa: E402
from apps.workflow_engine.services.retrieval import RetrievalService  # noqa: E402


def test_rerank_is_disabled_by_default_and_does_not_load_model(monkeypatch):
    monkeypatch.delenv("RAG_CROSS_ENCODER_RERANK_ENABLED", raising=False)

    def fail_model_lookup():
        raise AssertionError("disabled reranker must not load CrossEncoder")

    monkeypatch.setattr(
        RetrievalService,
        "_get_cross_encoder_model",
        staticmethod(fail_model_lookup),
    )
    service = RetrievalService(db=None, user_id=None)
    candidates = [
        {"chunk": SimpleNamespace(content="first")},
        {"chunk": SimpleNamespace(content="second")},
    ]

    assert service._rerank("query", candidates, top_k=1) == candidates[:1]


def test_rerank_missing_dependency_warns_and_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("RAG_CROSS_ENCODER_RERANK_ENABLED", "true")
    original_import = builtins.__import__
    RetrievalService._cross_encoder_model = None
    RetrievalService._cross_encoder_model_name = None

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sentence_transformers":
            raise ImportError("No module named 'sentence_transformers'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    service = RetrievalService(db=None, user_id=None)
    candidates = [
        {"chunk": SimpleNamespace(content="first")},
        {"chunk": SimpleNamespace(content="second")},
    ]

    with caplog.at_level(
        logging.WARNING,
        logger="apps.workflow_engine.services.retrieval",
    ):
        result = service._rerank("query", candidates, top_k=1)

    assert result == candidates[:1]
    assert any(
        "Reranker dependency unavailable" in record.message
        for record in caplog.records
    )
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_rerank_enabled_reuses_cross_encoder_model(monkeypatch):
    monkeypatch.setenv("RAG_CROSS_ENCODER_RERANK_ENABLED", "true")
    monkeypatch.setenv("RAG_CROSS_ENCODER_MODEL", "cross-encoder/test-model")
    RetrievalService._cross_encoder_model = None
    RetrievalService._cross_encoder_model_name = None
    original_import = builtins.__import__
    init_calls = []

    class FakeCrossEncoder:
        def __init__(self, model_name, max_length):
            init_calls.append((model_name, max_length))

        def predict(self, pairs):
            return [0.1, 0.9][: len(pairs)]

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sentence_transformers":
            return SimpleNamespace(CrossEncoder=FakeCrossEncoder)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    service = RetrievalService(db=None, user_id=None)
    candidates = [
        {"chunk": SimpleNamespace(content="first")},
        {"chunk": SimpleNamespace(content="second")},
    ]

    first = service._rerank("query", candidates, top_k=1)
    second = service._rerank("query", candidates, top_k=1)

    assert first == [candidates[1]]
    assert second == [candidates[1]]
    assert init_calls == [("cross-encoder/test-model", 512)]


def test_rerank_enabled_decrypts_chunk_content_before_model_prediction(monkeypatch):
    monkeypatch.setenv("RAG_CROSS_ENCODER_RERANK_ENABLED", "true")
    predicted_pairs = []

    class FakeCrossEncoder:
        def predict(self, pairs):
            predicted_pairs.extend(pairs)
            return [0.9]

    service = RetrievalService(db=None, user_id=None)
    monkeypatch.setattr(service, "_get_cross_encoder_model", FakeCrossEncoder)
    monkeypatch.setattr(
        service,
        "_decrypt_content",
        lambda content: "플랫폼개발팀 첫 주 일정" if content == "ciphertext" else content,
    )

    candidate = {"chunk": SimpleNamespace(content="ciphertext")}

    assert service._rerank("첫주차 일정", [candidate], top_k=1) == [candidate]
    assert predicted_pairs == [("첫주차 일정", "플랫폼개발팀 첫 주 일정")]


def test_sync_search_threshold_uses_score_when_rerank_falls_back(monkeypatch):
    kb_id = uuid.uuid4()
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    class FakeQuery:
        def __init__(self, model):
            self.model = model

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            if self.model is KnowledgeBase:
                return SimpleNamespace(id=kb_id, embedding_model="text-embedding-test")
            if self.model is LLMModel:
                return SimpleNamespace(type="embedding")
            return None

    class FakeDb:
        def query(self, model):
            return FakeQuery(model)

    class FakeClient:
        def embed_sync(self, query):
            return [0.1, 0.2]

    chunk = SimpleNamespace(
        id=uuid.uuid4(),
        content="개발 직군 신입 보상 밴드 근거",
        metadata_={},
        parent_chunk_id=None,
        chunk_level="flat",
        token_count=12,
    )
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        filename="compensation.md",
        meta_info={},
        source_type="FILE",
    )

    monkeypatch.setattr(
        LLMService,
        "get_client_for_user",
        lambda *args, **kwargs: FakeClient(),
    )

    service = RetrievalService(FakeDb(), user_id, organization_id=organization_id)
    monkeypatch.setattr(service, "_has_valid_hierarchy", lambda *_: False)
    monkeypatch.setattr(
        service,
        "_vector_search",
        lambda *args, **kwargs: [(chunk, doc, 0.1)],
    )
    monkeypatch.setattr(service, "_keyword_search", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        service,
        "_rerank",
        lambda _query, candidates, _top_k, **_kwargs: candidates,
    )

    result = service.search_documents_sync(
        "개발팀 신입 연봉 기준",
        knowledge_base_id=str(kb_id),
        threshold=0.3,
        hybrid_search=True,
        use_rerank=True,
    )

    assert [chunk.filename for chunk in result] == ["compensation.md"]
    assert result[0].score > 0


def test_sync_search_uses_precomputed_model_binding_without_model_lookup(
    monkeypatch,
):
    kb_id = uuid.uuid4()
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    vectors = []
    binding = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="text-embedding-test",
    )

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return SimpleNamespace(id=kb_id, embedding_model="text-embedding-test")

    class FakeDb:
        def query(self, model):
            if model is LLMModel:
                raise AssertionError("precomputed binding must skip model lookup")
            assert model is KnowledgeBase
            return FakeQuery()

    chunk = SimpleNamespace(
        id=uuid.uuid4(),
        content="개발팀 커밋 컨벤션",
        metadata_={},
        parent_chunk_id=None,
        chunk_level="flat",
        token_count=8,
    )
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        filename="commit-convention.md",
        meta_info={},
        source_type="FILE",
    )

    def fail_client_lookup(*_args, **_kwargs):
        raise AssertionError("precomputed query_vector must not request an embed client")

    monkeypatch.setattr(LLMService, "get_client_for_user", fail_client_lookup)

    service = RetrievalService(FakeDb(), user_id, organization_id=organization_id)
    monkeypatch.setattr(service, "_has_valid_hierarchy", lambda *_: False)

    def fake_vector_search(query_vector, *_args, **_kwargs):
        vectors.append(query_vector)
        return [(chunk, doc, 0.05)]

    monkeypatch.setattr(service, "_vector_search", fake_vector_search)

    result = service.search_documents_sync(
        "commit convention",
        knowledge_base_id=str(kb_id),
        threshold=0,
        hybrid_search=False,
        use_rerank=False,
        query_vector=[0.3, 0.7],
        embedding_model_binding=binding,
    )

    assert vectors == [[0.3, 0.7]]
    assert [chunk.filename for chunk in result] == ["commit-convention.md"]


def test_sync_search_rejects_mismatched_precomputed_model_binding(monkeypatch):
    kb_id = uuid.uuid4()
    binding = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="embedding-other",
    )

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return SimpleNamespace(id=kb_id, embedding_model="embedding-expected")

    class FakeDb:
        def query(self, model):
            if model is LLMModel:
                raise AssertionError("mismatched binding must skip model lookup")
            assert model is KnowledgeBase
            return FakeQuery()

    service = RetrievalService(
        FakeDb(),
        uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        service,
        "_vector_search",
        lambda *_args, **_kwargs: pytest.fail(
            "mismatched binding must not reach vector search"
        ),
    )

    assert (
        service.search_documents_sync(
            "query",
            knowledge_base_id=str(kb_id),
            query_vector=[0.3, 0.7],
            embedding_model_binding=binding,
        )
        == []
    )


def test_sync_search_rejects_non_embedding_model_before_vector_search(monkeypatch):
    kb_id = uuid.uuid4()
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    class FakeQuery:
        def __init__(self, model):
            self.model = model

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            if self.model is KnowledgeBase:
                return SimpleNamespace(id=kb_id, embedding_model="gpt-5.4-mini")
            if self.model is LLMModel:
                return SimpleNamespace(type="chat")
            return None

    class FakeDb:
        def query(self, model):
            return FakeQuery(model)

    service = RetrievalService(FakeDb(), user_id, organization_id=organization_id)
    monkeypatch.setattr(service, "_has_valid_hierarchy", lambda *_: False)
    monkeypatch.setattr(
        service,
        "_vector_search",
        lambda *_args, **_kwargs: pytest.fail("non-embedding model must not search"),
    )
    monkeypatch.setattr(
        LLMService,
        "get_client_for_user",
        lambda *_args, **_kwargs: pytest.fail("non-embedding model must not embed"),
    )

    assert (
        service.search_documents_sync(
            "질문",
            knowledge_base_id=str(kb_id),
            query_vector=[0.1, 0.2],
        )
        == []
    )
