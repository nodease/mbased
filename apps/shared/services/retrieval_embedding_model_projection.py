from __future__ import annotations

from collections import defaultdict
from types import MappingProxyType
from typing import Iterable, Mapping

from apps.shared.db.models.llm import LLMModel
from apps.shared.domain.embedding_model_binding import EmbeddingModelBinding
from apps.shared.domain.knowledge_runtime_candidates import (
    MAX_RUNTIME_DIRECT_KB_REFERENCES,
)
from sqlalchemy.orm import Session


class EmbeddingModelProjectionError(RuntimeError):
    """Safe failure raised before retrieval or provider I/O."""


def _normalize_identifiers(
    model_identifiers: Iterable[str | None],
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in model_identifiers:
        if not isinstance(value, str):
            continue
        identifier = value.strip()
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        normalized.append(identifier)
    return normalized


def load_embedding_model_projection(
    db: Session,
    model_identifiers: Iterable[str | None],
    *,
    max_models: int = MAX_RUNTIME_DIRECT_KB_REFERENCES,
) -> Mapping[str, EmbeddingModelBinding]:
    """Resolve authorized KB model identifiers with one bounded catalog query."""

    identifiers = _normalize_identifiers(model_identifiers)
    if not identifiers:
        return MappingProxyType({})
    if len(identifiers) > max_models:
        raise EmbeddingModelProjectionError(
            "embedding_model_projection_limit_exceeded"
        )

    try:
        rows = (
            db.query(LLMModel)
            .filter(LLMModel.model_id_for_api_call.in_(identifiers))
            .all()
        )
    except Exception as exc:
        raise EmbeddingModelProjectionError(
            "embedding_model_projection_unavailable"
        ) from exc

    rows_by_identifier: dict[str, list[LLMModel]] = defaultdict(list)
    for row in rows:
        identifier = getattr(row, "model_id_for_api_call", None)
        if identifier in identifiers:
            rows_by_identifier[identifier].append(row)

    bindings: dict[str, EmbeddingModelBinding] = {}
    for identifier in identifiers:
        matching_rows = rows_by_identifier.get(identifier, [])
        if len(matching_rows) != 1:
            continue
        model = matching_rows[0]
        if (
            not getattr(model, "is_active", False)
            or getattr(model, "type", None) != "embedding"
        ):
            continue
        bindings[identifier] = EmbeddingModelBinding(
            model_id=model.id,
            provider_id=model.provider_id,
            model_identifier=identifier,
        )

    return MappingProxyType(bindings)


__all__ = [
    "EmbeddingModelBinding",
    "EmbeddingModelProjectionError",
    "load_embedding_model_projection",
]
