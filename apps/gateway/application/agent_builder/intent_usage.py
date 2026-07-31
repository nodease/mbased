"""Safe usage contract for Agent Builder intent LLM calls."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from apps.shared.domain.llm_usage import AGENT_BUILDER_INTENT_RUNTIME_SURFACE


class AgentBuilderIntentUsageSampleError(ValueError):
    """The provider response did not contain trustworthy token usage."""


class AgentBuilderIntentUsageRecordingError(RuntimeError):
    """Durable usage attribution could not be confirmed."""


class AgentBuilderIntentUsageConflictError(AgentBuilderIntentUsageRecordingError):
    """A stable attempt key already points at different billing facts."""


@dataclass(frozen=True, slots=True)
class AgentBuilderIntentUsageContext:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    workflow_id: uuid.UUID
    session_id: uuid.UUID
    request_id: uuid.UUID
    runtime_surface: str = field(
        init=False,
        default=AGENT_BUILDER_INTENT_RUNTIME_SURFACE,
    )


@dataclass(frozen=True, slots=True)
class AgentBuilderIntentUsageSample:
    credential_id: uuid.UUID
    model_id: uuid.UUID
    model_api_id: str
    attempt: int
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class AgentBuilderIntentUsageReservation:
    id: uuid.UUID
    context: AgentBuilderIntentUsageContext
    credential_id: uuid.UUID
    model_id: uuid.UUID
    model_api_id: str
    attempt: int
    input_price_1k: Decimal
    output_price_1k: Decimal


class AgentBuilderIntentUsageRecorder(Protocol):
    def reserve(
        self,
        context: AgentBuilderIntentUsageContext,
        *,
        credential_id: uuid.UUID,
        model_id: uuid.UUID,
        model_api_id: str,
        attempt: int,
    ) -> AgentBuilderIntentUsageReservation: ...

    def record(
        self,
        reservation: AgentBuilderIntentUsageReservation,
        sample: AgentBuilderIntentUsageSample,
    ) -> uuid.UUID: ...

    def cancel(self, reservation: AgentBuilderIntentUsageReservation) -> None: ...


def _token_count(usage: dict[str, Any], primary: str, alias: str) -> int:
    value = usage.get(primary, usage.get(alias))
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentBuilderIntentUsageSampleError("intent usage tokens are invalid")
    return value


def normalize_intent_usage_sample(
    usage: Any,
    *,
    credential_id: uuid.UUID,
    model_id: uuid.UUID,
    model_api_id: str,
    attempt: int,
    latency_ms: int | float,
) -> AgentBuilderIntentUsageSample:
    """Validate a billing-only provider usage mapping."""

    if not isinstance(usage, dict):
        raise AgentBuilderIntentUsageSampleError("intent usage is missing")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise AgentBuilderIntentUsageSampleError("intent usage attempt is invalid")
    if (
        isinstance(latency_ms, bool)
        or not isinstance(latency_ms, (int, float))
        or not math.isfinite(float(latency_ms))
        or latency_ms < 0
    ):
        raise AgentBuilderIntentUsageSampleError("intent usage latency is invalid")
    if not isinstance(model_api_id, str) or not model_api_id.strip():
        raise AgentBuilderIntentUsageSampleError("intent usage model is invalid")

    return AgentBuilderIntentUsageSample(
        credential_id=credential_id,
        model_id=model_id,
        model_api_id=model_api_id.strip(),
        attempt=attempt,
        prompt_tokens=_token_count(usage, "prompt_tokens", "input_tokens"),
        completion_tokens=_token_count(
            usage,
            "completion_tokens",
            "output_tokens",
        ),
        latency_ms=max(0, int(round(float(latency_ms)))),
    )
