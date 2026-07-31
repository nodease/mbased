from __future__ import annotations

import uuid

import pytest

from apps.shared.domain.embedding_model_binding import EmbeddingModelBinding
from apps.workflow_engine.adapters.query_embedding_model_projection import (
    PostgresQueryEmbeddingModelProjection,
)
from apps.workflow_engine.application.query_embedding_execution import (
    QueryEmbeddingConfigurationError,
)


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def query(self, *_columns):
        return _Query(self._rows)

    def close(self):
        self.closed = True


def test_projection_returns_only_scalar_bindings_and_closes_session(monkeypatch):
    kb_id = uuid.uuid4()
    session = _Session([(kb_id, "embed-safe")])
    binding = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="embed-safe",
    )
    monkeypatch.setattr(
        "apps.workflow_engine.adapters.query_embedding_model_projection."
        "load_embedding_model_projection",
        lambda *_args, **_kwargs: {"embed-safe": binding},
    )
    projection = PostgresQueryEmbeddingModelProjection(
        session_factory=lambda: session
    )

    result = projection.project(
        organization_id=uuid.uuid4(),
        knowledge_base_ids=(kb_id,),
    )

    assert result == {kb_id: binding}
    assert session.closed is True
    assert not hasattr(result[kb_id], "__table__")


def test_projection_failure_closes_session_and_returns_safe_error(monkeypatch):
    kb_id = uuid.uuid4()
    session = _Session([(kb_id, "embed-safe")])
    monkeypatch.setattr(
        "apps.workflow_engine.adapters.query_embedding_model_projection."
        "load_embedding_model_projection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db details")),
    )
    projection = PostgresQueryEmbeddingModelProjection(
        session_factory=lambda: session
    )

    with pytest.raises(QueryEmbeddingConfigurationError):
        projection.project(
            organization_id=uuid.uuid4(),
            knowledge_base_ids=(kb_id,),
        )

    assert session.closed is True
