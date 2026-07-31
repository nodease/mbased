from __future__ import annotations

from dataclasses import replace

import pytest

from apps.workflow_engine.adapters.provider_execution import (
    ProviderExecutionRuntimeRouter,
)
from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionConfigurationError,
    ProviderExecutionPlan,
    ProviderExecutionPreflight,
    ProviderExecutionRequest,
)


class _Strategy:
    def __init__(self, name: str) -> None:
        self.name = name
        self.preflight_calls = 0
        self.resolve_calls = 0
        self.state = object()
        self.lease = object()

    def preflight(self, request):
        self.preflight_calls += 1
        return ProviderExecutionPlan(
            fixed_model_id=request.configured_model_id,
            allow_legacy_memory_summary=self.name == "legacy",
            state=self.state,
        )

    def resolve(self, request):
        assert request.plan.state is self.state
        self.resolve_calls += 1
        return self.lease


def _preflight(*, capability_required: bool) -> ProviderExecutionPreflight:
    return ProviderExecutionPreflight(
        node_id="llm-1",
        configured_model_id="gpt-safe",
        auto_model_routing=False,
        fallback_model_id=None,
        knowledge_enabled=False,
        memory_summary_requested=False,
        client_override=None,
        execution_context={
            "provider_execution_capability_required": capability_required,
        },
        runtime_control=None,
    )


@pytest.mark.parametrize(
    ("capability_required", "selected_name"),
    [(False, "legacy"), (True, "capability")],
)
def test_router_returns_plan_to_the_server_selected_strategy(
    capability_required,
    selected_name,
):
    legacy = _Strategy("legacy")
    capability = _Strategy("capability")
    router = ProviderExecutionRuntimeRouter(
        legacy_strategy=legacy,
        capability_strategy=capability,
    )

    plan = router.preflight(_preflight(capability_required=capability_required))
    lease = router.resolve(
        ProviderExecutionRequest(
            plan=plan,
            model_id="gpt-safe",
            messages=(),
            parameters={},
        )
    )

    selected = capability if selected_name == "capability" else legacy
    other = legacy if selected is capability else capability
    assert lease is selected.lease
    assert selected.preflight_calls == 1
    assert selected.resolve_calls == 1
    assert other.preflight_calls == 0
    assert other.resolve_calls == 0


def test_router_rejects_a_plan_not_created_by_it():
    router = ProviderExecutionRuntimeRouter(
        legacy_strategy=_Strategy("legacy"),
        capability_strategy=_Strategy("capability"),
    )
    plan = ProviderExecutionPlan(
        fixed_model_id="gpt-safe",
        allow_legacy_memory_summary=False,
    )

    with pytest.raises(ProviderExecutionConfigurationError):
        router.resolve(
            ProviderExecutionRequest(
                plan=replace(plan, state=object()),
                model_id="gpt-safe",
                messages=(),
                parameters={},
            )
        )


@pytest.mark.parametrize("flag", [None, 0, 1, "false", "true"])
def test_router_rejects_malformed_capability_activation_flag(flag):
    router = ProviderExecutionRuntimeRouter(
        legacy_strategy=_Strategy("legacy"),
        capability_strategy=_Strategy("capability"),
    )
    request = replace(
        _preflight(capability_required=False),
        execution_context={"provider_execution_capability_required": flag},
    )

    with pytest.raises(ProviderExecutionConfigurationError):
        router.preflight(request)
