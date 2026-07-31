"""Canonical mixed read model for workflow provider usage and cost.

Rows linked to a provider usage operation are compatibility projections and
must never be counted alongside their canonical operation.  Legacy rows that
have no operation reference remain billable for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from apps.shared.db.models.llm import LLMUsageLog
from apps.shared.db.models.provider_usage import ProviderUsageOperationRecord
from apps.shared.domain.llm_usage import (
    AGENT_BUILDER_INTENT_RUNTIME_SURFACE,
    is_agent_builder_intent_usage,
    is_billable_llm_usage,
)
from sqlalchemy import Numeric, case, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

MICROUSD_PER_USD = Decimal("1000000")
BILLABLE_PROVIDER_USAGE_PURPOSES = frozenset(
    {"main_generation", "memory_summary", "query_embedding"}
)
_UNRESOLVED_STATES = ("provider_started", "outcome_unknown")


@dataclass(frozen=True, slots=True)
class ProviderUsageAggregate:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    call_count: int = 0
    total_cost: Decimal = Decimal("0")
    agent_builder_cost: Decimal = Decimal("0")
    unresolved_provider_call_count: int = 0

    @property
    def workflow_execution_cost(self) -> Decimal:
        return self.total_cost - self.agent_builder_cost

    @property
    def usage_data_complete(self) -> bool:
        return self.unresolved_provider_call_count == 0


def summarize_usage_records(
    *,
    workflow_id: Any,
    organization_id: Any | None,
    start_at: datetime,
    end_at: datetime,
    legacy_usage_logs: Iterable[Any],
    provider_operations: Iterable[Any],
) -> ProviderUsageAggregate:
    """Pure equivalent of the SQL read model for unit tests and in-memory fakes."""

    prompt_tokens = 0
    completion_tokens = 0
    call_count = 0
    total_cost = Decimal("0")
    agent_builder_cost = Decimal("0")
    unresolved_count = 0

    for usage in legacy_usage_logs:
        if (
            usage.workflow_id != workflow_id
            or getattr(usage, "provider_usage_operation_id", None) is not None
            or not start_at <= usage.created_at < end_at
            or not _legacy_organization_matches(usage, organization_id)
            or not is_billable_llm_usage(
                getattr(usage, "runtime_surface", None),
                getattr(usage, "status", "success"),
            )
        ):
            continue
        prompt_tokens += int(usage.prompt_tokens or 0)
        completion_tokens += int(usage.completion_tokens or 0)
        call_count += 1
        usage_cost = _decimal(usage.total_cost)
        total_cost += usage_cost
        if is_agent_builder_intent_usage(
            getattr(usage, "runtime_surface", None)
        ):
            agent_builder_cost += usage_cost

    for operation in provider_operations:
        if (
            operation.workflow_id != workflow_id
            or operation.purpose not in BILLABLE_PROVIDER_USAGE_PURPOSES
            or operation.provider_started_at is None
            or not start_at <= operation.provider_started_at < end_at
            or (
                organization_id is not None
                and operation.organization_id != organization_id
            )
        ):
            continue
        if operation.state in _UNRESOLVED_STATES:
            unresolved_count += 1
            continue
        if operation.state != "succeeded":
            continue
        if (
            operation.prompt_tokens is None
            or operation.completion_tokens is None
            or operation.total_cost_microusd is None
        ):
            raise ValueError("canonical provider usage is incomplete")
        prompt_tokens += int(operation.prompt_tokens)
        completion_tokens += int(operation.completion_tokens)
        call_count += 1
        total_cost += Decimal(operation.total_cost_microusd) / MICROUSD_PER_USD

    return ProviderUsageAggregate(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        call_count=call_count,
        total_cost=total_cost,
        agent_builder_cost=agent_builder_cost,
        unresolved_provider_call_count=unresolved_count,
    )


def read_workflow_usage_aggregate(
    db: Session,
    *,
    workflow_id: Any,
    organization_id: Any | None,
    start_at: datetime,
    end_at: datetime,
) -> ProviderUsageAggregate:
    if hasattr(db, "usage_logs"):
        return summarize_usage_records(
            workflow_id=workflow_id,
            organization_id=organization_id,
            start_at=start_at,
            end_at=end_at,
            legacy_usage_logs=db.usage_logs,
            provider_operations=getattr(db, "provider_usage_operations", ()),
        )

    aggregate = provider_usage_aggregate_subquery(
        organization_id=organization_id,
        start_at=start_at,
        end_at=end_at,
    )
    row = db.execute(
        select(
            aggregate.c.prompt_tokens,
            aggregate.c.completion_tokens,
            aggregate.c.call_count,
            aggregate.c.total_cost,
            aggregate.c.agent_builder_cost,
            aggregate.c.unresolved_provider_call_count,
        ).where(aggregate.c.workflow_id == workflow_id)
    ).one_or_none()
    return ProviderUsageAggregate() if row is None else aggregate_from_row(row)


def provider_usage_aggregate_subquery(
    *,
    organization_id: Any | None,
    start_at: datetime,
    end_at: datetime,
):
    """Return per-workflow legacy + canonical usage without projection duplicates."""

    legacy_conditions = [
        LLMUsageLog.provider_usage_operation_id.is_(None),
        LLMUsageLog.created_at >= start_at,
        LLMUsageLog.created_at < end_at,
        _billable_legacy_condition(),
    ]
    provider_conditions = [
        ProviderUsageOperationRecord.provider_started_at >= start_at,
        ProviderUsageOperationRecord.provider_started_at < end_at,
        ProviderUsageOperationRecord.purpose.in_(
            BILLABLE_PROVIDER_USAGE_PURPOSES
        ),
    ]
    if organization_id is not None:
        legacy_conditions.append(
            or_(
                LLMUsageLog.organization_id == organization_id,
                LLMUsageLog.organization_id.is_(None),
            )
        )
        provider_conditions.append(
            ProviderUsageOperationRecord.organization_id == organization_id
        )

    legacy = select(
        LLMUsageLog.workflow_id.label("workflow_id"),
        func.coalesce(LLMUsageLog.prompt_tokens, 0).label("prompt_tokens"),
        func.coalesce(LLMUsageLog.completion_tokens, 0).label(
            "completion_tokens"
        ),
        literal(1).label("call_count"),
        func.coalesce(LLMUsageLog.total_cost, 0).label("total_cost"),
        case(
            (
                LLMUsageLog.runtime_surface
                == AGENT_BUILDER_INTENT_RUNTIME_SURFACE,
                func.coalesce(LLMUsageLog.total_cost, 0),
            ),
            else_=0,
        ).label("agent_builder_cost"),
        literal(0).label("unresolved_provider_call_count"),
    ).where(*legacy_conditions)

    canonical_cost = cast(
        ProviderUsageOperationRecord.total_cost_microusd,
        Numeric(24, 6),
    ) / literal(MICROUSD_PER_USD)
    succeeded = select(
        ProviderUsageOperationRecord.workflow_id.label("workflow_id"),
        ProviderUsageOperationRecord.prompt_tokens.label("prompt_tokens"),
        ProviderUsageOperationRecord.completion_tokens.label(
            "completion_tokens"
        ),
        literal(1).label("call_count"),
        canonical_cost.label("total_cost"),
        cast(literal(0), Numeric(24, 6)).label("agent_builder_cost"),
        literal(0).label("unresolved_provider_call_count"),
    ).where(
        *provider_conditions,
        ProviderUsageOperationRecord.state == "succeeded",
    )
    unresolved = select(
        ProviderUsageOperationRecord.workflow_id.label("workflow_id"),
        literal(0).label("prompt_tokens"),
        literal(0).label("completion_tokens"),
        literal(0).label("call_count"),
        cast(literal(0), Numeric(24, 6)).label("total_cost"),
        cast(literal(0), Numeric(24, 6)).label("agent_builder_cost"),
        literal(1).label("unresolved_provider_call_count"),
    ).where(
        *provider_conditions,
        ProviderUsageOperationRecord.state.in_(_UNRESOLVED_STATES),
    )

    usage_rows = union_all(legacy, succeeded, unresolved).subquery(
        "provider_usage_rows"
    )
    return (
        select(
            usage_rows.c.workflow_id,
            func.coalesce(func.sum(usage_rows.c.prompt_tokens), 0).label(
                "prompt_tokens"
            ),
            func.coalesce(func.sum(usage_rows.c.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(usage_rows.c.call_count), 0).label(
                "call_count"
            ),
            func.coalesce(func.sum(usage_rows.c.total_cost), 0).label(
                "total_cost"
            ),
            func.coalesce(func.sum(usage_rows.c.agent_builder_cost), 0).label(
                "agent_builder_cost"
            ),
            func.coalesce(
                func.sum(usage_rows.c.unresolved_provider_call_count), 0
            ).label("unresolved_provider_call_count"),
        )
        .group_by(usage_rows.c.workflow_id)
        .subquery("provider_usage_aggregate")
    )


def summarize_usage_by_execution_subject(
    *,
    organization_id: Any,
    eligible_workflow_ids: set[Any],
    user_ids: set[Any],
    start_at: datetime,
    end_at: datetime,
    legacy_usage_logs: Iterable[Any],
    provider_operations: Iterable[Any],
) -> dict[Any, ProviderUsageAggregate]:
    mutable = {
        user_id: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "call_count": 0,
            "total_cost": Decimal("0"),
            "agent_builder_cost": Decimal("0"),
            "unresolved_provider_call_count": 0,
        }
        for user_id in user_ids
    }
    for usage in legacy_usage_logs:
        user_id = getattr(usage, "user_id", None)
        if (
            user_id not in mutable
            or usage.workflow_id not in eligible_workflow_ids
            or getattr(usage, "provider_usage_operation_id", None) is not None
            or usage.organization_id not in (None, organization_id)
            or not start_at <= usage.created_at < end_at
            or not is_billable_llm_usage(
                getattr(usage, "runtime_surface", None),
                getattr(usage, "status", "success"),
            )
        ):
            continue
        target = mutable[user_id]
        target["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
        target["completion_tokens"] += int(
            getattr(usage, "completion_tokens", 0) or 0
        )
        target["call_count"] += 1
        cost = _decimal(usage.total_cost)
        target["total_cost"] += cost
        if is_agent_builder_intent_usage(
            getattr(usage, "runtime_surface", None)
        ):
            target["agent_builder_cost"] += cost

    for operation in provider_operations:
        user_id = getattr(operation, "execution_subject_id", None)
        if (
            getattr(operation, "execution_subject_kind", None) != "user"
            or user_id not in mutable
            or operation.workflow_id not in eligible_workflow_ids
            or operation.organization_id != organization_id
            or operation.purpose not in BILLABLE_PROVIDER_USAGE_PURPOSES
            or operation.provider_started_at is None
            or not start_at <= operation.provider_started_at < end_at
        ):
            continue
        target = mutable[user_id]
        if operation.state in _UNRESOLVED_STATES:
            target["unresolved_provider_call_count"] += 1
            continue
        if operation.state != "succeeded":
            continue
        if (
            operation.prompt_tokens is None
            or operation.completion_tokens is None
            or operation.total_cost_microusd is None
        ):
            raise ValueError("canonical provider usage is incomplete")
        target["prompt_tokens"] += int(operation.prompt_tokens)
        target["completion_tokens"] += int(operation.completion_tokens)
        target["call_count"] += 1
        target["total_cost"] += (
            Decimal(operation.total_cost_microusd) / MICROUSD_PER_USD
        )

    return {
        user_id: ProviderUsageAggregate(**values)
        for user_id, values in mutable.items()
    }


def provider_usage_subject_aggregate_subquery(
    *,
    organization_id: Any,
    start_at: datetime,
    end_at: datetime,
):
    """Return per-user/per-workflow usage using explicit execution subject."""

    legacy = select(
        LLMUsageLog.user_id.label("attribution_user_id"),
        LLMUsageLog.workflow_id.label("workflow_id"),
        func.coalesce(LLMUsageLog.prompt_tokens, 0).label("prompt_tokens"),
        func.coalesce(LLMUsageLog.completion_tokens, 0).label(
            "completion_tokens"
        ),
        literal(1).label("call_count"),
        func.coalesce(LLMUsageLog.total_cost, 0).label("total_cost"),
        case(
            (
                LLMUsageLog.runtime_surface
                == AGENT_BUILDER_INTENT_RUNTIME_SURFACE,
                func.coalesce(LLMUsageLog.total_cost, 0),
            ),
            else_=0,
        ).label("agent_builder_cost"),
        literal(0).label("unresolved_provider_call_count"),
    ).where(
        LLMUsageLog.provider_usage_operation_id.is_(None),
        LLMUsageLog.user_id.isnot(None),
        or_(
            LLMUsageLog.organization_id == organization_id,
            LLMUsageLog.organization_id.is_(None),
        ),
        LLMUsageLog.created_at >= start_at,
        LLMUsageLog.created_at < end_at,
        _billable_legacy_condition(),
    )

    provider_conditions = (
        ProviderUsageOperationRecord.organization_id == organization_id,
        ProviderUsageOperationRecord.execution_subject_kind == "user",
        ProviderUsageOperationRecord.execution_subject_id.isnot(None),
        ProviderUsageOperationRecord.purpose.in_(
            BILLABLE_PROVIDER_USAGE_PURPOSES
        ),
        ProviderUsageOperationRecord.provider_started_at >= start_at,
        ProviderUsageOperationRecord.provider_started_at < end_at,
    )
    canonical_cost = cast(
        ProviderUsageOperationRecord.total_cost_microusd,
        Numeric(24, 6),
    ) / literal(MICROUSD_PER_USD)
    succeeded = select(
        ProviderUsageOperationRecord.execution_subject_id.label(
            "attribution_user_id"
        ),
        ProviderUsageOperationRecord.workflow_id.label("workflow_id"),
        ProviderUsageOperationRecord.prompt_tokens.label("prompt_tokens"),
        ProviderUsageOperationRecord.completion_tokens.label(
            "completion_tokens"
        ),
        literal(1).label("call_count"),
        canonical_cost.label("total_cost"),
        cast(literal(0), Numeric(24, 6)).label("agent_builder_cost"),
        literal(0).label("unresolved_provider_call_count"),
    ).where(
        *provider_conditions,
        ProviderUsageOperationRecord.state == "succeeded",
    )
    unresolved = select(
        ProviderUsageOperationRecord.execution_subject_id.label(
            "attribution_user_id"
        ),
        ProviderUsageOperationRecord.workflow_id.label("workflow_id"),
        literal(0).label("prompt_tokens"),
        literal(0).label("completion_tokens"),
        literal(0).label("call_count"),
        cast(literal(0), Numeric(24, 6)).label("total_cost"),
        cast(literal(0), Numeric(24, 6)).label("agent_builder_cost"),
        literal(1).label("unresolved_provider_call_count"),
    ).where(
        *provider_conditions,
        ProviderUsageOperationRecord.state.in_(_UNRESOLVED_STATES),
    )
    usage_rows = union_all(legacy, succeeded, unresolved).subquery(
        "provider_usage_subject_rows"
    )
    return (
        select(
            usage_rows.c.attribution_user_id,
            usage_rows.c.workflow_id,
            func.coalesce(func.sum(usage_rows.c.prompt_tokens), 0).label(
                "prompt_tokens"
            ),
            func.coalesce(func.sum(usage_rows.c.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(usage_rows.c.call_count), 0).label(
                "call_count"
            ),
            func.coalesce(func.sum(usage_rows.c.total_cost), 0).label(
                "total_cost"
            ),
            func.coalesce(func.sum(usage_rows.c.agent_builder_cost), 0).label(
                "agent_builder_cost"
            ),
            func.coalesce(
                func.sum(usage_rows.c.unresolved_provider_call_count), 0
            ).label("unresolved_provider_call_count"),
        )
        .group_by(
            usage_rows.c.attribution_user_id,
            usage_rows.c.workflow_id,
        )
        .subquery("provider_usage_subject_aggregate")
    )


def provider_usage_model_subject_rows_subquery(
    *,
    user_id: Any,
    start_at: datetime,
    end_at: datetime,
):
    """Return billable model rows attributed to one explicit user subject."""

    legacy = select(
        LLMUsageLog.model_id.label("model_id"),
        (
            func.coalesce(LLMUsageLog.prompt_tokens, 0)
            + func.coalesce(LLMUsageLog.completion_tokens, 0)
        ).label("total_tokens"),
        func.coalesce(LLMUsageLog.total_cost, 0).label("total_cost"),
    ).where(
        LLMUsageLog.provider_usage_operation_id.is_(None),
        LLMUsageLog.user_id == user_id,
        LLMUsageLog.created_at >= start_at,
        LLMUsageLog.created_at < end_at,
        _billable_legacy_condition(),
    )
    canonical_cost = cast(
        ProviderUsageOperationRecord.total_cost_microusd,
        Numeric(24, 6),
    ) / literal(MICROUSD_PER_USD)
    canonical = select(
        ProviderUsageOperationRecord.model_id.label("model_id"),
        (
            ProviderUsageOperationRecord.prompt_tokens
            + ProviderUsageOperationRecord.completion_tokens
        ).label("total_tokens"),
        canonical_cost.label("total_cost"),
    ).where(
        ProviderUsageOperationRecord.execution_subject_kind == "user",
        ProviderUsageOperationRecord.execution_subject_id == user_id,
        ProviderUsageOperationRecord.purpose.in_(
            BILLABLE_PROVIDER_USAGE_PURPOSES
        ),
        ProviderUsageOperationRecord.state == "succeeded",
        ProviderUsageOperationRecord.provider_started_at >= start_at,
        ProviderUsageOperationRecord.provider_started_at < end_at,
    )
    return union_all(legacy, canonical).subquery(
        "provider_usage_model_subject_rows"
    )


def aggregate_from_row(row: Any) -> ProviderUsageAggregate:
    return ProviderUsageAggregate(
        prompt_tokens=int(row.prompt_tokens or 0),
        completion_tokens=int(row.completion_tokens or 0),
        call_count=int(row.call_count or 0),
        total_cost=_decimal(row.total_cost),
        agent_builder_cost=_decimal(row.agent_builder_cost),
        unresolved_provider_call_count=int(
            row.unresolved_provider_call_count or 0
        ),
    )


def _legacy_organization_matches(usage: Any, organization_id: Any | None) -> bool:
    return organization_id is None or usage.organization_id in (
        None,
        organization_id,
    )


def _billable_legacy_condition():
    return or_(
        LLMUsageLog.runtime_surface.is_(None),
        LLMUsageLog.runtime_surface != AGENT_BUILDER_INTENT_RUNTIME_SURFACE,
        LLMUsageLog.status == "success",
    )


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


__all__ = [
    "BILLABLE_PROVIDER_USAGE_PURPOSES",
    "ProviderUsageAggregate",
    "aggregate_from_row",
    "provider_usage_aggregate_subquery",
    "provider_usage_model_subject_rows_subquery",
    "provider_usage_subject_aggregate_subquery",
    "read_workflow_usage_aggregate",
    "summarize_usage_by_execution_subject",
    "summarize_usage_records",
]
