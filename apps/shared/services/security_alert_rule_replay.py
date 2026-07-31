from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from apps.shared.services.security_alert_rule_evaluator import (
    evaluate_security_alert_rules,
)
from apps.shared.services.security_alert_rule_registry import (
    SECURITY_ALERT_MAX_WINDOW,
    SECURITY_ALERT_RULES,
)


@dataclass(frozen=True)
class SecurityAlertRuleReplayResult:
    evaluated_event_count: int
    matched_event_count: int
    detection_count: int
    detections_by_rule: dict[str, int]

    def safe_dict(self) -> dict[str, Any]:
        return {
            "dry_run": True,
            "evaluated_event_count": self.evaluated_event_count,
            "matched_event_count": self.matched_event_count,
            "detection_count": self.detection_count,
            "detections_by_rule": dict(sorted(self.detections_by_rule.items())),
        }


def replay_security_alert_rules(
    events: Sequence[Any],
    *,
    activation_started_at: datetime,
    evaluation_started_at: datetime | None = None,
    evaluation_ended_at: datetime | None = None,
) -> SecurityAlertRuleReplayResult:
    """Evaluate stored audit objects without creating alerts or evidence."""
    ordered = sorted(events, key=lambda event: (event.occurred_at, str(event.id)))
    event_times = [event.occurred_at for event in ordered]
    detections_by_rule = {rule.rule_id: 0 for rule in SECURITY_ALERT_RULES}
    evaluated_event_count = 0
    matched_event_count = 0

    for current_event in ordered:
        if (
            evaluation_started_at is not None
            and current_event.occurred_at < evaluation_started_at
        ):
            continue
        if (
            evaluation_ended_at is not None
            and current_event.occurred_at >= evaluation_ended_at
        ):
            continue

        evaluated_event_count += 1
        left = bisect_left(
            event_times,
            current_event.occurred_at - SECURITY_ALERT_MAX_WINDOW,
        )
        right = bisect_right(event_times, current_event.occurred_at)
        candidates = evaluate_security_alert_rules(
            current_event=current_event,
            window_events=ordered[left:right],
            activation_started_at=activation_started_at,
        )
        if candidates:
            matched_event_count += 1
        for candidate in candidates:
            detections_by_rule[candidate.rule_id] += 1

    return SecurityAlertRuleReplayResult(
        evaluated_event_count=evaluated_event_count,
        matched_event_count=matched_event_count,
        detection_count=sum(detections_by_rule.values()),
        detections_by_rule=dict(sorted(detections_by_rule.items())),
    )
