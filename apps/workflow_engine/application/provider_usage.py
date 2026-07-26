"""Application ports for durable provider usage lifecycle recording."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionAttribution,
)


@dataclass(frozen=True, slots=True)
class ProviderUsageRecord:
    attribution: ProviderExecutionAttribution
    usage: Mapping[str, Any]
    workflow_id: uuid.UUID | None
    workflow_run_id: uuid.UUID | None
    node_id: str
    cost_optimizer_candidate_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProviderUsageIntent:
    attribution: ProviderExecutionAttribution
    workflow_id: uuid.UUID | None
    workflow_run_id: uuid.UUID | None
    node_id: str
    cost_optimizer_candidate_id: uuid.UUID | None = None


class ProviderUsageRuntimeError(RuntimeError):
    """Safe fail-closed error; ``code`` is suitable for runtime state only."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProviderUsageAttempt(Protocol):
    @property
    def durable(self) -> bool: ...

    @property
    def operation_reference(self) -> str | None: ...

    def mark_provider_started(self) -> None: ...

    def record_success(self, *, usage: Mapping[str, Any], latency_ms: int) -> float: ...

    def record_definitive_failure(self, *, reason_code: str) -> None: ...

    def mark_outcome_unknown(self, *, reason_code: str) -> None: ...


class ProviderUsageRecorder(Protocol):
    def begin(self, request: ProviderUsageIntent) -> ProviderUsageAttempt: ...

    def record(self, request: ProviderUsageRecord) -> float: ...

    def resume_checkpoint(
        self,
        *,
        organization_id: uuid.UUID,
        provider_attempt_id: uuid.UUID,
        operation_reference: str,
    ) -> None: ...

    def reconcile_reference_terminal(
        self,
        *,
        organization_id: uuid.UUID,
        provider_attempt_id: uuid.UUID,
        operation_reference: str | None,
    ) -> str: ...


__all__ = [
    "ProviderUsageAttempt",
    "ProviderUsageIntent",
    "ProviderUsageRecord",
    "ProviderUsageRecorder",
    "ProviderUsageRuntimeError",
]
