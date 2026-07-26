from __future__ import annotations

from apps.workflow_engine.adapters.conversation_memory_provider import (
    ConversationMemoryProviderAdapter,
    ConversationProviderLimits,
)
from apps.workflow_engine.adapters.workflow_budget import (
    DisposableWorkflowBudgetDecisionAdapter,
)
from apps.workflow_engine.adapters.conversation_execution_observer import (
    SqlAlchemyConversationExecutionJournalObserver,
)
from apps.workflow_engine.application.conversation_memory_execution import (
    ExecuteConversationTurnUseCase,
)
from apps.workflow_engine.composition.conversation_memory import (
    CONVERSATION_EXECUTION_LEASE_SECONDS,
    CONVERSATION_PROVIDER_MAX_TIMEOUT_SECONDS,
    build_conversation_turn_use_case,
)


class _Cipher:
    def protect(
        self, value, *, associated_data
    ):  # pragma: no cover - construction only
        raise AssertionError("not invoked while composing")

    def reveal(self, protected, *, associated_data):  # pragma: no cover
        raise AssertionError("not invoked while composing")


class _TokenCounter:
    def count(self, value):  # pragma: no cover
        raise AssertionError("not invoked while composing")


class _ProviderRuntime:
    pass


class _UsageRecorder:
    pass


def test_production_composition_builds_real_memory_admission_and_provider_adapters() -> (
    None
):
    use_case = build_conversation_turn_use_case(
        session_factory=lambda: None,
        content_cipher=_Cipher(),
        token_counter=_TokenCounter(),
        provider_runtime=_ProviderRuntime(),
        usage_recorder=_UsageRecorder(),
        provider_limits=ConversationProviderLimits(
            input_token_cap=65_536,
            output_token_cap=4_096,
            cost_cap_microusd=1_000_000,
        ),
        environ={},
    )

    assert isinstance(use_case, ExecuteConversationTurnUseCase)
    assert use_case.memory.__class__.__module__.startswith(
        "apps.workflow_engine.adapters."
    )
    assert use_case.admissions.__class__.__module__.startswith(
        "apps.workflow_engine.adapters."
    )
    assert use_case.graphs.__class__.__module__.startswith(
        "apps.workflow_engine.adapters."
    )
    assert isinstance(use_case.provider, ConversationMemoryProviderAdapter)
    assert isinstance(
        use_case.budget,
        DisposableWorkflowBudgetDecisionAdapter,
    )
    assert isinstance(
        use_case.observer,
        SqlAlchemyConversationExecutionJournalObserver,
    )
    assert (
        use_case.lease_duration.total_seconds() == CONVERSATION_EXECUTION_LEASE_SECONDS
    )
    assert (
        use_case.lease_duration.total_seconds()
        > CONVERSATION_PROVIDER_MAX_TIMEOUT_SECONDS
    )


def test_default_composition_requires_all_server_owned_provider_caps() -> None:
    import pytest

    with pytest.raises(
        RuntimeError,
        match="MEMORY_RUNTIME_PROVIDER_INPUT_TOKEN_CAP is required",
    ):
        build_conversation_turn_use_case(
            session_factory=lambda: None,
            content_cipher=_Cipher(),
            token_counter=_TokenCounter(),
            provider_runtime=_ProviderRuntime(),
            usage_recorder=_UsageRecorder(),
            environ={},
        )


def test_default_composition_rejects_malformed_provider_caps() -> None:
    import pytest

    with pytest.raises(
        RuntimeError,
        match="MEMORY_RUNTIME_PROVIDER_OUTPUT_TOKEN_CAP",
    ):
        build_conversation_turn_use_case(
            session_factory=lambda: None,
            content_cipher=_Cipher(),
            token_counter=_TokenCounter(),
            provider_runtime=_ProviderRuntime(),
            usage_recorder=_UsageRecorder(),
            environ={
                "MEMORY_RUNTIME_PROVIDER_INPUT_TOKEN_CAP": "65536",
                "MEMORY_RUNTIME_PROVIDER_OUTPUT_TOKEN_CAP": "0",
                "MEMORY_RUNTIME_PROVIDER_COST_CAP_MICROUSD": "1000000",
            },
        )
