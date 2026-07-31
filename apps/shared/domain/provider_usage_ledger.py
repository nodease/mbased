"""Provider usage ledger domain contracts.

The ledger records the durable boundary around one provider attempt.  It keeps
only typed identities, immutable policy/capability snapshots, and aggregate
usage measurements; provider request/response payloads never belong here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum

from apps.shared.domain.provider_execution_capability import (
    CapabilityPurpose,
    PrincipalKind,
    ProviderExecutionBinding,
    RuntimeIdentityContext,
)

_LEDGER_PRICE_QUANTUM = Decimal("0.000000001")
_LEDGER_PRICE_MAX = Decimal("99999999999.999999999")
_MICROUSD_PER_USD = Decimal(1_000_000)
_TOKENS_PER_PRICE_UNIT = Decimal(1_000)


def _canonical_ledger_price(value: str, *, name: str) -> str:
    try:
        price = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative decimal") from exc
    if not price.is_finite() or price < 0:
        raise ValueError(f"{name} must be a non-negative decimal")
    try:
        canonical = price.quantize(_LEDGER_PRICE_QUANTUM)
    except InvalidOperation as exc:
        raise ValueError(f"{name} does not fit the ledger price column") from exc
    if canonical != price or canonical > _LEDGER_PRICE_MAX:
        raise ValueError(f"{name} does not fit the ledger price column")
    return format(canonical, "f")


class ProviderUsageState(str, Enum):
    INTENT = "intent"
    PROVIDER_STARTED = "provider_started"
    SUCCEEDED = "succeeded"
    FAILED_DEFINITIVE = "failed_definitive"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ProviderUsageLedgerError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProviderUsageOperationKey:
    """Canonical identity from ADR-0064; deliberately has no retry counter."""

    organization_id: uuid.UUID
    provider_attempt_id: uuid.UUID
    purpose: CapabilityPurpose

    @classmethod
    def from_binding(
        cls, binding: ProviderExecutionBinding
    ) -> "ProviderUsageOperationKey":
        return cls(
            organization_id=binding.organization_id,
            provider_attempt_id=binding.provider_attempt_id,
            purpose=binding.purpose,
        )


@dataclass(frozen=True, slots=True)
class ProviderUsageMeasurement:
    prompt_tokens: int
    completion_tokens: int
    total_cost_microusd: int
    latency_ms: int

    def __post_init__(self) -> None:
        values = (
            self.prompt_tokens,
            self.completion_tokens,
            self.total_cost_microusd,
            self.latency_ms,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("usage values must be non-negative integers")
        if any(value < 0 for value in values):
            raise ValueError("usage values must be non-negative integers")


@dataclass(frozen=True, slots=True)
class ProviderUsageIntentSnapshot:
    """Safe immutable inputs used for admission, billing, and audit attribution."""

    binding: ProviderExecutionBinding
    capability_id: uuid.UUID
    capability_revision: int
    policy_id: uuid.UUID
    policy_revision: int
    provider_id: uuid.UUID
    model_id: uuid.UUID
    model_api_id: str
    credential_id: uuid.UUID
    identities: RuntimeIdentityContext
    permission_revision: str
    relation_revision: str
    egress_revision: str
    pricing_revision: str
    input_price_per_1k: str
    output_price_per_1k: str
    input_token_cap: int
    output_token_cap: int
    cost_cap_microusd: int
    admitted_input_tokens: int
    admitted_output_tokens: int
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.capability_revision < 1 or self.policy_revision < 1:
            raise ValueError("capability and policy revisions must be positive")
        if not self.model_api_id or len(self.model_api_id) > 255:
            raise ValueError("model API id is invalid")
        for value, name in (
            (self.permission_revision, "permission_revision"),
            (self.relation_revision, "relation_revision"),
            (self.egress_revision, "egress_revision"),
            (self.pricing_revision, "pricing_revision"),
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be a SHA-256 digest")
        for value, name in (
            (self.input_price_per_1k, "input_price_per_1k"),
            (self.output_price_per_1k, "output_price_per_1k"),
        ):
            object.__setattr__(
                self,
                name,
                _canonical_ledger_price(value, name=name),
            )
        integer_values = (
            self.input_token_cap,
            self.output_token_cap,
            self.cost_cap_microusd,
            self.admitted_input_tokens,
            self.admitted_output_tokens,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in integer_values
        ):
            raise ValueError("usage caps and admitted values must be non-negative integers")
        if self.admitted_input_tokens > self.input_token_cap:
            raise ValueError("admitted input tokens exceed the capability cap")
        if self.admitted_output_tokens > self.output_token_cap:
            raise ValueError("admitted output tokens exceed the capability cap")
        if (
            self.binding.purpose is CapabilityPurpose.QUERY_EMBEDDING
            and (self.output_token_cap != 0 or self.admitted_output_tokens != 0)
        ):
            raise ValueError("query embedding output usage must be zero")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("capability expiry must be timezone-aware")


_OUTCOME_UNKNOWN_REASONS = frozenset(
    {
        "provider_call_failed",
        "provider_timeout",
        "provider_usage_invalid",
        "stale_provider_started",
        "terminal_record_failed",
    }
)
_DEFINITIVE_FAILURE_REASONS = frozenset(
    {
        "provider_not_sent",
        "provider_rejected",
    }
)


def _require_aware_timestamp(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ledger timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ProviderUsageOperation:
    id: uuid.UUID
    key: ProviderUsageOperationKey
    snapshot: ProviderUsageIntentSnapshot
    state: ProviderUsageState
    state_version: int
    intent_created_at: datetime
    provider_started_at: datetime | None = None
    terminal_at: datetime | None = None
    reason_code: str | None = None
    measurement: ProviderUsageMeasurement | None = None
    usage_revision: int = 0

    @classmethod
    def intent(
        cls,
        *,
        operation_id: uuid.UUID,
        snapshot: ProviderUsageIntentSnapshot,
        now: datetime,
    ) -> "ProviderUsageOperation":
        _require_aware_timestamp(now)
        if (
            snapshot.identities.billing_principal.kind
            is not PrincipalKind.ORGANIZATION
            or snapshot.identities.billing_principal.reference_id
            != snapshot.binding.organization_id
        ):
            raise ValueError("billing principal must match the binding organization")
        return cls(
            id=operation_id,
            key=ProviderUsageOperationKey.from_binding(snapshot.binding),
            snapshot=snapshot,
            state=ProviderUsageState.INTENT,
            state_version=1,
            intent_created_at=now,
        )

    def require_same_intent(self, snapshot: ProviderUsageIntentSnapshot) -> None:
        if self.snapshot != snapshot:
            raise ProviderUsageLedgerError("provider_usage.intent_conflict")

    def mark_provider_started(self, *, now: datetime) -> "ProviderUsageOperation":
        _require_aware_timestamp(now)
        if self.state is not ProviderUsageState.INTENT:
            raise ProviderUsageLedgerError("provider_usage.start_not_allowed")
        return replace(
            self,
            state=ProviderUsageState.PROVIDER_STARTED,
            state_version=self.state_version + 1,
            provider_started_at=now,
        )

    def record_success(
        self,
        *,
        measurement: ProviderUsageMeasurement,
        now: datetime,
    ) -> "ProviderUsageOperation":
        _require_aware_timestamp(now)
        if self.state is ProviderUsageState.SUCCEEDED:
            if self.measurement == measurement:
                return self
            raise ProviderUsageLedgerError("provider_usage.outcome_conflict")
        if self.state is not ProviderUsageState.PROVIDER_STARTED:
            raise ProviderUsageLedgerError("provider_usage.outcome_not_allowed")
        if (
            self.snapshot.binding.purpose is CapabilityPurpose.QUERY_EMBEDDING
            and measurement.completion_tokens != 0
        ):
            raise ProviderUsageLedgerError("provider_usage.measurement_invalid")
        return self._succeed(measurement=measurement, now=now)

    def mark_outcome_unknown(
        self,
        *,
        reason_code: str,
        now: datetime,
    ) -> "ProviderUsageOperation":
        _require_aware_timestamp(now)
        if reason_code not in _OUTCOME_UNKNOWN_REASONS:
            raise ValueError("outcome-unknown reason code is not safe")
        if self.state is ProviderUsageState.OUTCOME_UNKNOWN:
            if self.reason_code == reason_code:
                return self
            raise ProviderUsageLedgerError("provider_usage.outcome_conflict")
        if self.state is not ProviderUsageState.PROVIDER_STARTED:
            raise ProviderUsageLedgerError("provider_usage.outcome_not_allowed")
        return replace(
            self,
            state=ProviderUsageState.OUTCOME_UNKNOWN,
            state_version=self.state_version + 1,
            terminal_at=now,
            reason_code=reason_code,
        )

    def reconcile_success(
        self,
        *,
        measurement: ProviderUsageMeasurement,
        now: datetime,
    ) -> "ProviderUsageOperation":
        _require_aware_timestamp(now)
        if self.state is ProviderUsageState.SUCCEEDED:
            if self.measurement == measurement:
                return self
            raise ProviderUsageLedgerError("provider_usage.outcome_conflict")
        if self.state is not ProviderUsageState.OUTCOME_UNKNOWN:
            raise ProviderUsageLedgerError("provider_usage.reconciliation_not_allowed")
        return self._succeed(measurement=measurement, now=now)

    def reconcile_definitive_failure(
        self,
        *,
        reason_code: str,
        now: datetime,
    ) -> "ProviderUsageOperation":
        _require_aware_timestamp(now)
        if reason_code not in _DEFINITIVE_FAILURE_REASONS:
            raise ValueError("definitive failure reason code is not safe")
        if self.state is ProviderUsageState.FAILED_DEFINITIVE:
            if self.reason_code == reason_code:
                return self
            raise ProviderUsageLedgerError("provider_usage.outcome_conflict")
        if self.state is not ProviderUsageState.OUTCOME_UNKNOWN:
            raise ProviderUsageLedgerError("provider_usage.reconciliation_not_allowed")
        return replace(
            self,
            state=ProviderUsageState.FAILED_DEFINITIVE,
            state_version=self.state_version + 1,
            terminal_at=now,
            reason_code=reason_code,
        )

    def record_definitive_failure(
        self,
        *,
        reason_code: str,
        now: datetime,
    ) -> "ProviderUsageOperation":
        _require_aware_timestamp(now)
        if reason_code not in _DEFINITIVE_FAILURE_REASONS:
            raise ValueError("definitive failure reason code is not safe")
        if self.state is ProviderUsageState.FAILED_DEFINITIVE:
            if self.reason_code == reason_code:
                return self
            raise ProviderUsageLedgerError("provider_usage.outcome_conflict")
        if self.state is not ProviderUsageState.PROVIDER_STARTED:
            raise ProviderUsageLedgerError("provider_usage.outcome_not_allowed")
        return replace(
            self,
            state=ProviderUsageState.FAILED_DEFINITIVE,
            state_version=self.state_version + 1,
            terminal_at=now,
            reason_code=reason_code,
        )

    def apply_correction(
        self,
        *,
        measurement: ProviderUsageMeasurement,
        expected_usage_revision: int,
    ) -> "ProviderUsageOperation":
        if self.state is not ProviderUsageState.SUCCEEDED:
            raise ProviderUsageLedgerError("provider_usage.correction_not_allowed")
        if expected_usage_revision != self.usage_revision:
            raise ProviderUsageLedgerError("provider_usage.correction_conflict")
        self._validate_measurement(measurement)
        return replace(
            self,
            state_version=self.state_version + 1,
            measurement=measurement,
            usage_revision=self.usage_revision + 1,
        )

    def _succeed(
        self,
        *,
        measurement: ProviderUsageMeasurement,
        now: datetime,
    ) -> "ProviderUsageOperation":
        self._validate_measurement(measurement)
        return replace(
            self,
            state=ProviderUsageState.SUCCEEDED,
            state_version=self.state_version + 1,
            terminal_at=now,
            reason_code=None,
            measurement=measurement,
            usage_revision=1,
        )

    def _validate_measurement(
        self,
        measurement: ProviderUsageMeasurement,
    ) -> None:
        expected_cost_microusd = int(
            (
                (
                    Decimal(measurement.prompt_tokens)
                    * Decimal(self.snapshot.input_price_per_1k)
                    + Decimal(measurement.completion_tokens)
                    * Decimal(self.snapshot.output_price_per_1k)
                )
                * _MICROUSD_PER_USD
                / _TOKENS_PER_PRICE_UNIT
            ).to_integral_value(rounding=ROUND_HALF_UP)
        )
        if (
            measurement.prompt_tokens > self.snapshot.admitted_input_tokens
            or measurement.completion_tokens
            > self.snapshot.admitted_output_tokens
            or measurement.total_cost_microusd != expected_cost_microusd
            or expected_cost_microusd > self.snapshot.cost_cap_microusd
        ):
            raise ProviderUsageLedgerError("provider_usage.measurement_invalid")


__all__ = [
    "ProviderUsageIntentSnapshot",
    "ProviderUsageLedgerError",
    "ProviderUsageMeasurement",
    "ProviderUsageOperation",
    "ProviderUsageOperationKey",
    "ProviderUsageState",
]
