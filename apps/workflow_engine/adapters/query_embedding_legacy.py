"""Legacy user-scoped query embedding behind the target application port."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from apps.shared.domain.embedding_model_binding import EmbeddingModelBinding
from apps.workflow_engine.application.query_embedding_execution import (
    QueryEmbeddingConfigurationError,
    QueryEmbeddingPlan,
    QueryEmbeddingPreflight,
    QueryEmbeddingProviderRequest,
    QueryEmbeddingProviderResult,
)
from apps.workflow_engine.services.llm_service import LLMService
from sqlalchemy.orm import Session


class LegacyQueryEmbeddingAdapter:
    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def preflight(self, request: QueryEmbeddingPreflight) -> QueryEmbeddingPlan:
        if (
            request.execution_context.get(
                "provider_execution_capability_required",
                False,
            )
            is not False
            or not isinstance(request.organization_id, uuid.UUID)
            or not isinstance(request.legacy_credential_user_id, uuid.UUID)
        ):
            raise QueryEmbeddingConfigurationError()
        return QueryEmbeddingPlan(
            capability_required=False,
            organization_id=request.organization_id,
            node_id=request.node_id,
            state=(request.organization_id, request.legacy_credential_user_id),
        )

    def invoke(
        self,
        request: QueryEmbeddingProviderRequest,
    ) -> QueryEmbeddingProviderResult:
        state = request.plan.state
        if (
            not isinstance(state, tuple)
            or len(state) != 2
            or not isinstance(state[0], uuid.UUID)
            or not isinstance(state[1], uuid.UUID)
            or state[0] != request.plan.organization_id
            or not isinstance(request.model_binding, EmbeddingModelBinding)
            or not isinstance(request.query, str)
            or not request.query
        ):
            raise QueryEmbeddingConfigurationError()
        db = self._session_factory()
        if db is None:
            raise QueryEmbeddingConfigurationError()
        try:
            client = LLMService.get_client_for_model_binding(
                db,
                state[1],
                request.model_binding,
                organization_id=state[0],
            )
        except Exception:
            raise QueryEmbeddingConfigurationError() from None
        finally:
            db.close()

        started_at = time.monotonic()
        try:
            vector = tuple(float(value) for value in client.embed_sync(request.query))
        except Exception:
            raise QueryEmbeddingConfigurationError() from None
        latency_ms = max(0, int((time.monotonic() - started_at) * 1000))
        return QueryEmbeddingProviderResult(
            vector=vector,
            input_tokens=len(request.query.encode("utf-8")),
            latency_ms=latency_ms,
        )


__all__ = ["LegacyQueryEmbeddingAdapter"]
