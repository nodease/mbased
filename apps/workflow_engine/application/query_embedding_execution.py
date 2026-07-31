"""Application boundary for one Workflow RAG query-embedding fan-out."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from apps.shared.domain.embedding_model_binding import EmbeddingModelBinding
from apps.workflow_engine.domain.execution import NodeExecutionControl


class QueryEmbeddingConfigurationError(ValueError):
    """Redaction-safe failure for an unavailable query-embedding path."""

    code = "query_embedding.configuration_required"


@dataclass(frozen=True, slots=True)
class QueryEmbeddingPreflight:
    node_id: str
    organization_id: uuid.UUID
    legacy_credential_user_id: uuid.UUID | None
    execution_context: Mapping[str, Any]
    runtime_control: NodeExecutionControl | None


@dataclass(frozen=True, slots=True)
class QueryEmbeddingPlan:
    capability_required: bool
    organization_id: uuid.UUID
    node_id: str
    state: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.capability_required, bool)
            or not isinstance(self.organization_id, uuid.UUID)
            or not isinstance(self.node_id, str)
            or not self.node_id
        ):
            raise QueryEmbeddingConfigurationError()


@dataclass(frozen=True, slots=True)
class QueryEmbeddingProviderResult:
    vector: tuple[float, ...] = field(repr=False)
    input_tokens: int
    latency_ms: int = 0

    def __post_init__(self) -> None:
        if not self.vector or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in self.vector
        ):
            raise QueryEmbeddingConfigurationError()
        object.__setattr__(self, "vector", tuple(float(value) for value in self.vector))
        for value in (self.input_tokens, self.latency_ms):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise QueryEmbeddingConfigurationError()


@dataclass(frozen=True, slots=True)
class QueryEmbeddingProviderRequest:
    plan: QueryEmbeddingPlan
    model_binding: EmbeddingModelBinding
    query: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class QueryEmbeddingExecutionRequest:
    plan: QueryEmbeddingPlan
    organization_id: uuid.UUID
    node_id: str
    knowledge_base_ids: tuple[str, ...] = field(repr=False)
    failure_policy: str
    query: str = field(repr=False)
    deadline_guard: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True, slots=True)
class QueryEmbeddingExecutionResult:
    vectors_by_knowledge_base: Mapping[str, tuple[float, ...]] = field(repr=False)
    bindings_by_knowledge_base: Mapping[str, EmbeddingModelBinding] = field(
        repr=False
    )
    failed_count: int = field(repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.failed_count, bool)
            or not isinstance(self.failed_count, int)
            or self.failed_count < 0
        ):
            raise QueryEmbeddingConfigurationError()
        object.__setattr__(
            self,
            "vectors_by_knowledge_base",
            MappingProxyType(dict(self.vectors_by_knowledge_base)),
        )
        object.__setattr__(
            self,
            "bindings_by_knowledge_base",
            MappingProxyType(dict(self.bindings_by_knowledge_base)),
        )


class QueryEmbeddingModelProjectionPort(Protocol):
    def project(
        self,
        *,
        organization_id: uuid.UUID,
        knowledge_base_ids: tuple[uuid.UUID, ...],
    ) -> Mapping[uuid.UUID, EmbeddingModelBinding]: ...


class QueryEmbeddingProviderRuntime(Protocol):
    def preflight(self, request: QueryEmbeddingPreflight) -> QueryEmbeddingPlan: ...

    def invoke(
        self,
        request: QueryEmbeddingProviderRequest,
    ) -> QueryEmbeddingProviderResult: ...


class QueryEmbeddingExecutionRuntime(Protocol):
    def preflight(self, request: QueryEmbeddingPreflight) -> QueryEmbeddingPlan: ...

    def execute(
        self,
        request: QueryEmbeddingExecutionRequest,
    ) -> QueryEmbeddingExecutionResult: ...


class QueryEmbeddingExecutionService:
    """Group authorized KB candidates by model and invoke each model once."""

    def __init__(
        self,
        *,
        model_projection: QueryEmbeddingModelProjectionPort,
        provider_runtime: QueryEmbeddingProviderRuntime,
    ) -> None:
        self._model_projection = model_projection
        self._provider_runtime = provider_runtime

    def preflight(self, request: QueryEmbeddingPreflight) -> QueryEmbeddingPlan:
        return self._provider_runtime.preflight(request)

    def execute(
        self,
        request: QueryEmbeddingExecutionRequest,
    ) -> QueryEmbeddingExecutionResult:
        if (
            not isinstance(request.plan, QueryEmbeddingPlan)
            or request.plan.organization_id != request.organization_id
            or request.plan.node_id != request.node_id
        ):
            raise QueryEmbeddingConfigurationError()
        if request.failure_policy not in {"fail_node", "safe_no_result"}:
            raise QueryEmbeddingConfigurationError()
        if (
            not isinstance(request.organization_id, uuid.UUID)
            or not isinstance(request.node_id, str)
            or not request.node_id
            or not isinstance(request.query, str)
            or not request.query
        ):
            raise QueryEmbeddingConfigurationError()

        if request.deadline_guard is not None and not callable(
            request.deadline_guard
        ):
            raise QueryEmbeddingConfigurationError()
        canonical_ids: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        invalid_count = 0
        for value in request.knowledge_base_ids:
            try:
                identifier = uuid.UUID(str(value))
            except (TypeError, ValueError):
                invalid_count += 1
                continue
            if identifier in seen:
                continue
            seen.add(identifier)
            canonical_ids.append(identifier)
        if not canonical_ids:
            return QueryEmbeddingExecutionResult({}, {}, invalid_count)

        try:
            projected = self._model_projection.project(
                organization_id=request.organization_id,
                knowledge_base_ids=tuple(canonical_ids),
            )
        except Exception:
            if request.failure_policy == "fail_node":
                raise QueryEmbeddingConfigurationError() from None
            return QueryEmbeddingExecutionResult(
                {},
                {},
                invalid_count + len(canonical_ids),
            )

        groups: dict[EmbeddingModelBinding, list[uuid.UUID]] = {}
        failed_count = invalid_count
        for knowledge_base_id in canonical_ids:
            binding = projected.get(knowledge_base_id)
            if not isinstance(binding, EmbeddingModelBinding):
                failed_count += 1
                continue
            groups.setdefault(binding, []).append(knowledge_base_id)

        vectors: dict[str, tuple[float, ...]] = {}
        bindings: dict[str, EmbeddingModelBinding] = {}
        for binding, knowledge_base_ids in groups.items():
            if request.deadline_guard is not None:
                request.deadline_guard()
            try:
                result = self._provider_runtime.invoke(
                    QueryEmbeddingProviderRequest(
                        plan=request.plan,
                        model_binding=binding,
                        query=request.query,
                    )
                )
                if not isinstance(result, QueryEmbeddingProviderResult):
                    raise QueryEmbeddingConfigurationError()
            except Exception:
                if request.failure_policy == "fail_node":
                    raise QueryEmbeddingConfigurationError() from None
                failed_count += len(knowledge_base_ids)
                continue
            for knowledge_base_id in knowledge_base_ids:
                key = str(knowledge_base_id)
                vectors[key] = result.vector
                bindings[key] = binding

        return QueryEmbeddingExecutionResult(vectors, bindings, failed_count)


__all__ = [
    "QueryEmbeddingConfigurationError",
    "QueryEmbeddingExecutionRequest",
    "QueryEmbeddingExecutionResult",
    "QueryEmbeddingExecutionRuntime",
    "QueryEmbeddingExecutionService",
    "QueryEmbeddingModelProjectionPort",
    "QueryEmbeddingPlan",
    "QueryEmbeddingPreflight",
    "QueryEmbeddingProviderRequest",
    "QueryEmbeddingProviderResult",
    "QueryEmbeddingProviderRuntime",
]
