import asyncio
import uuid
from types import SimpleNamespace

import pytest

from apps.gateway.services.llm_service import LLMService
from apps.gateway.services.retrieval import RetrievalService
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.llm import LLMModel


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.conditions = []

    def filter(self, *conditions):
        self.conditions.extend(conditions)
        return self

    def all(self):
        return self.rows


class _FakeDb:
    def __init__(self, *, kb_rows, model_rows):
        self.kb_query = _FakeQuery(kb_rows)
        self.model_query = _FakeQuery(model_rows)
        self.model_query_count = 0

    def query(self, model):
        if model is KnowledgeBase:
            return self.kb_query
        if model is LLMModel:
            self.model_query_count += 1
            return self.model_query
        raise AssertionError(f"unexpected model lookup: {model}")


def _model(identifier: str):
    return SimpleNamespace(
        id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_id_for_api_call=identifier,
        type="embedding",
        is_active=True,
    )


@pytest.mark.parametrize(
    ("kb_model_ids", "expected_models"),
    [
        (("embedding-a", "embedding-a"), ["embedding-a", "embedding-a"]),
        (("embedding-a", "embedding-b"), ["embedding-a", "embedding-b"]),
    ],
)
def test_search_documents_batches_authorized_kb_models_once(
    monkeypatch,
    kb_model_ids,
    expected_models,
):
    kb_ids = [uuid.uuid4(), uuid.uuid4()]
    hidden_kb_id = uuid.uuid4()
    model_rows = [_model(identifier) for identifier in dict.fromkeys(kb_model_ids)]
    db = _FakeDb(
        kb_rows=[
            SimpleNamespace(id=kb_id, embedding_model=model_id)
            for kb_id, model_id in zip(kb_ids, kb_model_ids, strict=True)
        ],
        model_rows=[
            *model_rows,
            _model("embedding-hidden"),
        ],
    )
    client_bindings = []
    collected_kb_ids = []

    class _FakeClient:
        async def embed(self, query):
            return [0.1]

    def fake_get_client(_db, _user_id, binding, *, organization_id=None):
        client_bindings.append((binding.model_identifier, organization_id))
        return _FakeClient()

    async def fake_collect(_all_candidates, *, knowledge_base_id, **_kwargs):
        collected_kb_ids.append(knowledge_base_id)

    monkeypatch.setattr(
        LLMService,
        "get_client_for_model_binding",
        fake_get_client,
        raising=False,
    )
    monkeypatch.setattr(
        LLMService,
        "get_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("batch-loaded model must not be queried by identifier again")
        ),
    )

    organization_id = uuid.uuid4()
    service = RetrievalService(
        db=db,
        user_id=uuid.uuid4(),
        organization_id=organization_id,
    )
    monkeypatch.setattr(service, "_has_valid_hierarchy", lambda *_: False)
    monkeypatch.setattr(service, "_collect_kb_candidates", fake_collect)

    result = asyncio.run(
        service.search_documents(
            "policy",
            knowledge_base_ids=[*(str(kb_id) for kb_id in kb_ids), str(hidden_kb_id)],
            hybrid_search=False,
            use_rerank=False,
        )
    )

    assert result == []
    assert db.model_query_count == 1
    assert [model for model, _organization in client_bindings] == expected_models
    assert all(
        organization == organization_id for _model, organization in client_bindings
    )
    assert collected_kb_ids == [str(kb_id) for kb_id in kb_ids]
    assert "embedding-hidden" not in [model for model, _ in client_bindings]


def test_search_documents_skips_model_projection_without_authorized_kbs(
    monkeypatch,
):
    hidden_kb_id = uuid.uuid4()
    db = _FakeDb(kb_rows=[], model_rows=[_model("embedding-hidden")])

    monkeypatch.setattr(
        LLMService,
        "get_client_for_model_binding",
        lambda *_args, **_kwargs: pytest.fail(
            "hidden KB must not resolve an embedding client"
        ),
        raising=False,
    )

    service = RetrievalService(
        db=db,
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        service,
        "_collect_kb_candidates",
        lambda *_args, **_kwargs: pytest.fail(
            "hidden KB must not reach retrieval"
        ),
    )

    result = asyncio.run(
        service.search_documents(
            "policy",
            knowledge_base_id=str(hidden_kb_id),
            hybrid_search=False,
            use_rerank=False,
        )
    )

    assert result == []
    assert db.model_query_count == 0


def test_search_documents_rejects_mismatched_explicit_model_without_lookup(
    monkeypatch,
):
    kb_id = uuid.uuid4()
    db = _FakeDb(
        kb_rows=[
            SimpleNamespace(id=kb_id, embedding_model="embedding-expected")
        ],
        model_rows=[],
    )
    explicit_model = SimpleNamespace(
        id=uuid.uuid4(),
        model_id_for_api_call="embedding-other",
        type="embedding",
        is_active=True,
    )

    monkeypatch.setattr(
        LLMService,
        "get_client_for_model",
        lambda *_args, **_kwargs: pytest.fail(
            "mismatched explicit model must not resolve a client"
        ),
    )

    service = RetrievalService(
        db=db,
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        service,
        "_collect_kb_candidates",
        lambda *_args, **_kwargs: pytest.fail(
            "mismatched explicit model must not reach retrieval"
        ),
    )

    result = asyncio.run(
        service.search_documents(
            "policy",
            knowledge_base_id=str(kb_id),
            hybrid_search=False,
            use_rerank=False,
            embedding_model=explicit_model,
        )
    )

    assert result == []
    assert db.model_query_count == 0
