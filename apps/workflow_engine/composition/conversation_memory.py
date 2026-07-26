"""Production composition for the public Conversation Memory worker."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from apps.memory.adapters.security import FernetMemoryContentCipher
from apps.workflow_engine.adapters.conversation_memory_provider import (
    ConversationMemoryProviderAdapter,
    ConversationProviderLimits,
)
from apps.workflow_engine.adapters.conversation_memory_runtime import (
    SqlAlchemyConversationExecutionAdmissionAdapter,
    SqlAlchemyConversationExecutionGraphAdapter,
    SqlAlchemyConversationMemoryRuntimeAdapter,
)
from apps.workflow_engine.adapters.conversation_execution_observer import (
    SqlAlchemyConversationExecutionJournalObserver,
)
from apps.workflow_engine.application.conversation_memory_execution import (
    ExecuteConversationTurnUseCase,
)
from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionRuntime,
)
from apps.workflow_engine.application.provider_usage import ProviderUsageRecorder
from apps.workflow_engine.composition.provider_execution import (
    build_provider_execution_runtime,
    build_provider_usage_recorder,
)

_INPUT_CAP_ENV = "MEMORY_RUNTIME_PROVIDER_INPUT_TOKEN_CAP"
_OUTPUT_CAP_ENV = "MEMORY_RUNTIME_PROVIDER_OUTPUT_TOKEN_CAP"
_COST_CAP_ENV = "MEMORY_RUNTIME_PROVIDER_COST_CAP_MICROUSD"
_INT32_MAX = 2_147_483_647
_INT64_MAX = 9_223_372_036_854_775_807
CONVERSATION_PROVIDER_MAX_TIMEOUT_SECONDS = 180
CONVERSATION_EXECUTION_LEASE_SECONDS = (
    CONVERSATION_PROVIDER_MAX_TIMEOUT_SECONDS + 30
)


class _TiktokenCounter:
    def __init__(self) -> None:
        import tiktoken

        self._encoding = tiktoken.get_encoding("cl100k_base")

    def count(self, value: str) -> int:
        if not isinstance(value, str):
            raise ValueError("conversation context must be text")
        return len(self._encoding.encode(value))


def conversation_provider_limits_from_environment(
    environ: Mapping[str, str],
) -> ConversationProviderLimits:
    return ConversationProviderLimits(
        input_token_cap=_positive_integer(
            environ,
            _INPUT_CAP_ENV,
            maximum=_INT32_MAX,
        ),
        output_token_cap=_positive_integer(
            environ,
            _OUTPUT_CAP_ENV,
            maximum=_INT32_MAX,
        ),
        cost_cap_microusd=_positive_integer(
            environ,
            _COST_CAP_ENV,
            maximum=_INT64_MAX,
        ),
    )


def build_conversation_turn_use_case(
    *,
    session_factory: Callable[[], Session] | None = None,
    content_cipher: Any | None = None,
    token_counter: Any | None = None,
    provider_runtime: ProviderExecutionRuntime | None = None,
    usage_recorder: ProviderUsageRecorder | None = None,
    provider_limits: ConversationProviderLimits | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ExecuteConversationTurnUseCase:
    values = os.environ if environ is None else environ
    if session_factory is None:
        from apps.shared.db.session import SessionLocal

        session_factory = SessionLocal
    limits = provider_limits or conversation_provider_limits_from_environment(
        values
    )
    runtime_clock = clock or (lambda: datetime.now(timezone.utc))
    cipher = content_cipher or FernetMemoryContentCipher.from_environment(values)
    counter = token_counter or _TiktokenCounter()
    runtime = provider_runtime or build_provider_execution_runtime(
        session_factory=session_factory
    )
    recorder = usage_recorder or build_provider_usage_recorder(
        session_factory=session_factory
    )
    return ExecuteConversationTurnUseCase(
        memory=SqlAlchemyConversationMemoryRuntimeAdapter(
            session_factory=session_factory,
            content_cipher=cipher,
            token_counter=counter,
            clock=runtime_clock,
        ),
        admissions=SqlAlchemyConversationExecutionAdmissionAdapter(
            session_factory=session_factory,
        ),
        graphs=SqlAlchemyConversationExecutionGraphAdapter(
            session_factory=session_factory,
        ),
        provider=ConversationMemoryProviderAdapter(
            runtime=runtime,
            usage_recorder=recorder,
            limits=limits,
        ),
        observer=SqlAlchemyConversationExecutionJournalObserver(
            session_factory=session_factory,
        ),
        clock=type("_Clock", (), {"now": staticmethod(runtime_clock)})(),
        worker_capability=values.get(
            "MEMORY_RUNTIME_MINIMUM_WORKER_CAPABILITY",
            "memory-runtime-v1",
        ),
        lease_duration=timedelta(
            seconds=CONVERSATION_EXECUTION_LEASE_SECONDS
        ),
        context_lease_duration=timedelta(seconds=20),
    )


def _positive_integer(
    environ: Mapping[str, str],
    name: str,
    *,
    maximum: int,
) -> int:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        raise RuntimeError(f"{name} is required")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0 or value > maximum or str(value) != raw.strip():
        raise RuntimeError(f"{name} must be a bounded positive integer")
    return value


__all__ = [
    "CONVERSATION_EXECUTION_LEASE_SECONDS",
    "CONVERSATION_PROVIDER_MAX_TIMEOUT_SECONDS",
    "build_conversation_turn_use_case",
    "conversation_provider_limits_from_environment",
]
