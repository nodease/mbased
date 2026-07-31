"""Bounded PostgreSQL projection for authorized RAG candidate model bindings."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import MappingProxyType
from typing import Mapping

from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.domain.embedding_model_binding import EmbeddingModelBinding
from apps.shared.domain.knowledge_runtime_candidates import (
    MAX_RUNTIME_DIRECT_KB_REFERENCES,
)
from apps.shared.services.retrieval_embedding_model_projection import (
    EmbeddingModelProjectionError,
    load_embedding_model_projection,
)
from apps.workflow_engine.application.query_embedding_execution import (
    QueryEmbeddingConfigurationError,
)
from sqlalchemy.orm import Session


class PostgresQueryEmbeddingModelProjection:
    """Materialize scalar bindings and close the session before provider I/O."""

    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def project(
        self,
        *,
        organization_id: uuid.UUID,
        knowledge_base_ids: tuple[uuid.UUID, ...],
    ) -> Mapping[uuid.UUID, EmbeddingModelBinding]:
        if (
            not isinstance(organization_id, uuid.UUID)
            or not knowledge_base_ids
            or len(knowledge_base_ids) > MAX_RUNTIME_DIRECT_KB_REFERENCES
            or any(not isinstance(value, uuid.UUID) for value in knowledge_base_ids)
        ):
            raise QueryEmbeddingConfigurationError()
        db = self._session_factory()
        if db is None:
            raise QueryEmbeddingConfigurationError()
        try:
            rows = (
                db.query(KnowledgeBase.id, KnowledgeBase.embedding_model)
                .filter(
                    KnowledgeBase.id.in_(knowledge_base_ids),
                    KnowledgeBase.organization_id == organization_id,
                    KnowledgeBase.lifecycle_state == "active",
                )
                .all()
            )
            identifiers = [row[1] for row in rows]
            model_projection = load_embedding_model_projection(
                db,
                identifiers,
                max_models=MAX_RUNTIME_DIRECT_KB_REFERENCES,
            )
            projected = {
                row[0]: model_projection[row[1]]
                for row in rows
                if row[1] in model_projection
            }
            return MappingProxyType(projected)
        except QueryEmbeddingConfigurationError:
            raise
        except EmbeddingModelProjectionError as exc:
            raise QueryEmbeddingConfigurationError() from exc
        except Exception as exc:
            raise QueryEmbeddingConfigurationError() from exc
        finally:
            db.close()


__all__ = ["PostgresQueryEmbeddingModelProjection"]
