from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Literal

SecurityAlertCountMode = Literal["events", "distinct_targets"]


@dataclass(frozen=True)
class SecurityAlertRule:
    rule_id: str
    version: str
    action: str
    window: timedelta
    threshold: int
    severity: str
    count_mode: SecurityAlertCountMode = "events"
    group_by_policy_reason: bool = False


SECURITY_ALERT_RULES = (
    SecurityAlertRule(
        rule_id="repeated_permission_denied",
        version="v1",
        action="permission.denied",
        window=timedelta(minutes=5),
        threshold=5,
        severity="medium",
    ),
    SecurityAlertRule(
        rule_id="multi_resource_permission_probe",
        version="v1",
        action="permission.denied",
        window=timedelta(minutes=10),
        threshold=5,
        severity="high",
        count_mode="distinct_targets",
    ),
    SecurityAlertRule(
        rule_id="repeated_policy_block",
        version="v1",
        action="policy.block",
        window=timedelta(minutes=10),
        threshold=3,
        severity="high",
        group_by_policy_reason=True,
    ),
)

_RULES_BY_ID = MappingProxyType(
    {rule.rule_id: rule for rule in SECURITY_ALERT_RULES}
)
SECURITY_ALERT_MAX_WINDOW = max(rule.window for rule in SECURITY_ALERT_RULES)


def get_security_alert_rule(rule_id: str) -> SecurityAlertRule | None:
    return _RULES_BY_ID.get(rule_id)


def security_alert_rules_for_action(action: str) -> tuple[SecurityAlertRule, ...]:
    return tuple(rule for rule in SECURITY_ALERT_RULES if rule.action == action)
