from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence
from uuid import UUID

from apps.shared.services.security_alert_policy_reason import (
    normalize_security_alert_policy_reason,
)
from apps.shared.services.security_alert_rule_registry import (
    SecurityAlertRule,
    security_alert_rules_for_action,
)


@dataclass(frozen=True)
class SecurityAlertRuleCandidate:
    rule_id: str
    rule_version: str
    severity: str
    organization_id: UUID
    subject_actor_id: UUID
    detection_key: str
    matched_audit_ids: tuple[UUID, ...]
    policy_reason: str | None = None


@dataclass(frozen=True)
class _EligibleAuditEvent:
    source: Any
    organization_id: UUID
    actor_id: UUID
    action: str
    policy_reason: str | None = None


def build_security_alert_detection_key(
    *,
    organization_id: UUID,
    actor_id: UUID,
    rule_id: str,
    rule_version: str,
    policy_reason: str | None = None,
) -> str:
    payload = json.dumps(
        [
            str(organization_id),
            str(actor_id),
            rule_id,
            rule_version,
            policy_reason,
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_security_alert_rules(
    *,
    current_event: Any,
    window_events: Sequence[Any],
    activation_started_at: datetime,
) -> tuple[SecurityAlertRuleCandidate, ...]:
    current = _eligible_event(current_event, activation_started_at)
    if current is None:
        return ()

    event_time = current_event.occurred_at
    eligible_events = _eligible_window_events(
        window_events,
        current=current,
        event_time=event_time,
        activation_started_at=activation_started_at,
    )

    candidates: list[SecurityAlertRuleCandidate] = []
    for rule in security_alert_rules_for_action(current.action):
        matched_events = _events_since(
            eligible_events,
            action=rule.action,
            started_at=event_time - rule.window,
        )
        if rule.group_by_policy_reason:
            matched_events = [
                event
                for event in matched_events
                if event.policy_reason == current.policy_reason
            ]
        if _rule_threshold_reached(rule, matched_events):
            candidates.append(
                _build_candidate(
                    rule=rule,
                    organization_id=current.organization_id,
                    actor_id=current.actor_id,
                    matched_events=matched_events,
                    policy_reason=(
                        current.policy_reason
                        if rule.group_by_policy_reason
                        else None
                    ),
                )
            )

    return tuple(candidates)


def is_security_alert_event_eligible(
    *,
    event: Any,
    activation_started_at: datetime,
) -> bool:
    return _eligible_event(event, activation_started_at) is not None


def build_security_alert_cooldown_candidates(
    *,
    current_event: Any,
    activation_started_at: datetime,
) -> tuple[SecurityAlertRuleCandidate, ...]:
    current = _eligible_event(current_event, activation_started_at)
    if current is None:
        return ()

    return tuple(
        _build_candidate(
            rule=rule,
            organization_id=current.organization_id,
            actor_id=current.actor_id,
            matched_events=(current,),
            policy_reason=(
                current.policy_reason if rule.group_by_policy_reason else None
            ),
        )
        for rule in security_alert_rules_for_action(current.action)
    )


def _eligible_event(
    event: Any,
    activation_started_at: datetime,
) -> _EligibleAuditEvent | None:
    if event.occurred_at < activation_started_at:
        return None
    if _enum_value(event.actor_type) != "user":
        return None
    if _enum_value(event.category) != "action":
        return None
    if _enum_value(event.status) != "failure":
        return None

    actor_id = _parse_uuid(event.actor_id)
    metadata = event.audit_metadata
    if actor_id is None or not isinstance(metadata, Mapping):
        return None
    organization_id = _parse_uuid(metadata.get("organization_id"))
    if organization_id is None:
        return None

    action = str(event.action)
    if action == "permission.denied":
        if not event.target_type or not event.target_id:
            return None
        return _EligibleAuditEvent(
            source=event,
            organization_id=organization_id,
            actor_id=actor_id,
            action=action,
        )
    if action == "policy.block":
        policy_reason = normalize_security_alert_policy_reason(metadata)
        if policy_reason is None:
            return None
        return _EligibleAuditEvent(
            source=event,
            organization_id=organization_id,
            actor_id=actor_id,
            action=action,
            policy_reason=policy_reason,
        )
    return None


def _eligible_window_events(
    events: Sequence[Any],
    *,
    current: _EligibleAuditEvent,
    event_time: datetime,
    activation_started_at: datetime,
) -> list[_EligibleAuditEvent]:
    events_by_id: dict[Any, _EligibleAuditEvent] = {}
    for event in events:
        eligible = _eligible_event(event, activation_started_at)
        if (
            eligible is not None
            and eligible.organization_id == current.organization_id
            and eligible.actor_id == current.actor_id
            and event.occurred_at <= event_time
        ):
            events_by_id.setdefault(event.id, eligible)
    return list(events_by_id.values())


def _events_since(
    events: Sequence[_EligibleAuditEvent],
    *,
    action: str,
    started_at: datetime,
) -> list[_EligibleAuditEvent]:
    return [
        event
        for event in events
        if event.action == action and event.source.occurred_at >= started_at
    ]


def _rule_threshold_reached(
    rule: SecurityAlertRule,
    events: Sequence[_EligibleAuditEvent],
) -> bool:
    if rule.count_mode == "distinct_targets":
        return len(
            {
                (event.source.target_type, event.source.target_id)
                for event in events
            }
        ) >= rule.threshold
    return len(events) >= rule.threshold


def _build_candidate(
    *,
    rule: SecurityAlertRule,
    organization_id: UUID,
    actor_id: UUID,
    matched_events: Sequence[_EligibleAuditEvent],
    policy_reason: str | None = None,
) -> SecurityAlertRuleCandidate:
    return SecurityAlertRuleCandidate(
        rule_id=rule.rule_id,
        rule_version=rule.version,
        severity=rule.severity,
        organization_id=organization_id,
        subject_actor_id=actor_id,
        detection_key=build_security_alert_detection_key(
            organization_id=organization_id,
            actor_id=actor_id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            policy_reason=policy_reason,
        ),
        matched_audit_ids=tuple(event.source.id for event in matched_events),
        policy_reason=policy_reason,
    )


def _parse_uuid(value: Any) -> UUID | None:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value
