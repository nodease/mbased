"""Server-controlled strategy router for Workflow query embedding."""

from __future__ import annotations

from dataclasses import dataclass, replace

from apps.workflow_engine.application.query_embedding_execution import (
    QueryEmbeddingConfigurationError,
    QueryEmbeddingPlan,
    QueryEmbeddingPreflight,
    QueryEmbeddingProviderRequest,
    QueryEmbeddingProviderResult,
    QueryEmbeddingProviderRuntime,
)


@dataclass(frozen=True, slots=True)
class _RoutedQueryEmbeddingPlanState:
    strategy: QueryEmbeddingProviderRuntime
    strategy_plan: QueryEmbeddingPlan


class QueryEmbeddingExecutionRuntimeRouter:
    def __init__(
        self,
        *,
        legacy_strategy: QueryEmbeddingProviderRuntime,
        capability_strategy: QueryEmbeddingProviderRuntime,
    ) -> None:
        self._legacy_strategy = legacy_strategy
        self._capability_strategy = capability_strategy

    def preflight(self, request: QueryEmbeddingPreflight) -> QueryEmbeddingPlan:
        required = request.execution_context.get(
            "provider_execution_capability_required",
            False,
        )
        if type(required) is not bool:
            raise QueryEmbeddingConfigurationError()
        strategy = self._capability_strategy if required else self._legacy_strategy
        strategy_plan = strategy.preflight(request)
        return replace(
            strategy_plan,
            state=_RoutedQueryEmbeddingPlanState(
                strategy=strategy,
                strategy_plan=strategy_plan,
            ),
        )

    def invoke(
        self,
        request: QueryEmbeddingProviderRequest,
    ) -> QueryEmbeddingProviderResult:
        state = request.plan.state
        if not isinstance(state, _RoutedQueryEmbeddingPlanState):
            raise QueryEmbeddingConfigurationError()
        return state.strategy.invoke(replace(request, plan=state.strategy_plan))


__all__ = ["QueryEmbeddingExecutionRuntimeRouter"]
