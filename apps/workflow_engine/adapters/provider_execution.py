"""Strategy router for Workflow provider execution."""

from __future__ import annotations

from dataclasses import dataclass, replace

from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionConfigurationError,
    ProviderExecutionPlan,
    ProviderExecutionPreflight,
    ProviderExecutionRequest,
    ProviderExecutionRuntime,
    ProviderInvocationLease,
)


@dataclass(frozen=True, slots=True)
class _RoutedPlanState:
    strategy: ProviderExecutionRuntime
    strategy_plan: ProviderExecutionPlan


class ProviderExecutionRuntimeRouter:
    """Select one server-owned strategy without importing its implementation."""

    def __init__(
        self,
        *,
        legacy_strategy: ProviderExecutionRuntime,
        capability_strategy: ProviderExecutionRuntime,
    ) -> None:
        self._legacy_strategy = legacy_strategy
        self._capability_strategy = capability_strategy

    def preflight(
        self,
        request: ProviderExecutionPreflight,
    ) -> ProviderExecutionPlan:
        capability_required = request.execution_context.get(
            "provider_execution_capability_required",
            False,
        )
        if type(capability_required) is not bool:
            raise ProviderExecutionConfigurationError()
        strategy = (
            self._capability_strategy if capability_required else self._legacy_strategy
        )
        strategy_plan = strategy.preflight(request)
        return replace(
            strategy_plan,
            state=_RoutedPlanState(
                strategy=strategy,
                strategy_plan=strategy_plan,
            ),
        )

    def resolve(
        self,
        request: ProviderExecutionRequest,
    ) -> ProviderInvocationLease:
        state = request.plan.state
        if not isinstance(state, _RoutedPlanState):
            raise ProviderExecutionConfigurationError()
        return state.strategy.resolve(replace(request, plan=state.strategy_plan))


__all__ = ["ProviderExecutionRuntimeRouter"]
