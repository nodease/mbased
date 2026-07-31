import uuid
from types import SimpleNamespace

import pytest
from apps.shared.db.models.llm import LLMModel
from apps.shared.services.retrieval_embedding_model_projection import (
    EmbeddingModelProjectionError,
    load_embedding_model_projection,
)


class _ModelQuery:
    def __init__(self, rows, *, error: Exception | None = None):
        self.rows = rows
        self.error = error
        self.conditions = []

    def filter(self, *conditions):
        self.conditions.extend(conditions)
        return self

    def all(self):
        if self.error is not None:
            raise self.error
        return self.rows


class _FakeSession:
    def __init__(self, rows=(), *, error: Exception | None = None):
        self.query_calls = []
        self.query_object = _ModelQuery(list(rows), error=error)

    def query(self, model):
        self.query_calls.append(model)
        assert model is LLMModel
        return self.query_object


def _model(
    identifier: str,
    *,
    active: bool = True,
    model_type: str = "embedding",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_id_for_api_call=identifier,
        is_active=active,
        type=model_type,
        model_metadata={"raw": "must-not-be-projected"},
        provider=SimpleNamespace(name="must-not-be-projected"),
    )


def test_empty_projection_skips_model_query():
    db = _FakeSession()

    projection = load_embedding_model_projection(db, [None, "", "   "])

    assert dict(projection) == {}
    assert db.query_calls == []


def test_projection_deduplicates_identifiers_and_queries_once():
    first = _model("embedding-a")
    second = _model("embedding-b")
    db = _FakeSession([second, first])

    projection = load_embedding_model_projection(
        db,
        [" embedding-a ", "embedding-b", "embedding-a"],
    )

    assert db.query_calls == [LLMModel]
    assert len(db.query_object.conditions) == 1
    assert set(db.query_object.conditions[0].right.value) == {
        "embedding-a",
        "embedding-b",
    }
    assert list(projection) == ["embedding-a", "embedding-b"]
    assert projection["embedding-a"].model_id == first.id
    assert projection["embedding-a"].id == first.id
    assert projection["embedding-a"].provider_id == first.provider_id
    assert projection["embedding-a"].model_identifier == "embedding-a"
    assert projection["embedding-a"].model_id_for_api_call == "embedding-a"
    assert not hasattr(projection["embedding-a"], "model_metadata")
    assert not hasattr(projection["embedding-a"], "provider")

    with pytest.raises(TypeError):
        projection["embedding-c"] = projection["embedding-a"]


def test_projection_excludes_missing_inactive_non_embedding_and_ambiguous_rows():
    valid = _model("embedding-valid")
    inactive = _model("embedding-inactive", active=False)
    wrong_type = _model("chat-model", model_type="chat")
    duplicate_a = _model("embedding-ambiguous")
    duplicate_b = _model("embedding-ambiguous")
    db = _FakeSession([valid, inactive, wrong_type, duplicate_a, duplicate_b])

    projection = load_embedding_model_projection(
        db,
        [
            "embedding-valid",
            "embedding-missing",
            "embedding-inactive",
            "chat-model",
            "embedding-ambiguous",
        ],
    )

    assert list(projection) == ["embedding-valid"]


def test_projection_rejects_unbounded_identifier_input_before_query():
    db = _FakeSession()

    with pytest.raises(EmbeddingModelProjectionError) as exc_info:
        load_embedding_model_projection(
            db,
            [f"embedding-{index}" for index in range(21)],
        )

    assert str(exc_info.value) == "embedding_model_projection_limit_exceeded"
    assert db.query_calls == []


def test_projection_wraps_database_failure_without_identifier_details():
    db = _FakeSession(error=RuntimeError("statement included embedding-private"))

    with pytest.raises(EmbeddingModelProjectionError) as exc_info:
        load_embedding_model_projection(db, ["embedding-private"])

    assert str(exc_info.value) == "embedding_model_projection_unavailable"
    assert "embedding-private" not in str(exc_info.value)
