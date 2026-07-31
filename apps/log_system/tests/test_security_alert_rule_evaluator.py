from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest


@dataclass(frozen=True)
class _AuditEvent:
    id: UUID
    occurred_at: datetime
    actor_id: UUID | None
    actor_type: str
    category: str
    action: str
    target_type: str | None
    target_id: str | None
    status: str
    audit_metadata: dict[str, Any]


_NOW = datetime(2026, 7, 12, 0, 10, tzinfo=timezone.utc)
_DEFAULT_TARGET = object()


def _event(
    *,
    offset_seconds: int,
    actor_id: UUID,
    organization_id: UUID,
    action: str = "permission.denied",
    target_type: str | None = "workflow",
    target_id: str | None | object = _DEFAULT_TARGET,
    metadata: dict[str, Any] | None = None,
) -> _AuditEvent:
    audit_metadata = {"organization_id": str(organization_id)}
    if metadata:
        audit_metadata.update(metadata)
    return _AuditEvent(
        id=uuid4(),
        occurred_at=_NOW + timedelta(seconds=offset_seconds),
        actor_id=actor_id,
        actor_type="user",
        category="action",
        action=action,
        target_type=target_type,
        target_id=str(uuid4()) if target_id is _DEFAULT_TARGET else target_id,
        status="failure",
        audit_metadata=audit_metadata,
    )


def _series(count: int, **event_kwargs) -> list[_AuditEvent]:
    actor_id = uuid4()
    organization_id = uuid4()
    return [
        _event(
            offset_seconds=index * 30,
            actor_id=actor_id,
            organization_id=organization_id,
            **event_kwargs,
        )
        for index in range(count)
    ]


def _evaluate(events: list[_AuditEvent]):
    return _evaluate_at(
        current_event=events[-1],
        window_events=events,
        activation_started_at=_NOW - timedelta(minutes=1),
    )


def _evaluate_at(
    *,
    current_event: _AuditEvent,
    window_events: list[_AuditEvent],
    activation_started_at: datetime,
):
    module = importlib.import_module(
        "apps.shared.services.security_alert_rule_evaluator"
    )
    return module.evaluate_security_alert_rules(
        current_event=current_event,
        window_events=window_events,
        activation_started_at=activation_started_at,
    )


def _candidate(candidates, rule_id: str):
    return next(
        (candidate for candidate in candidates if candidate.rule_id == rule_id),
        None,
    )


def test_sal_tc_w001_repeated_permission_denied_triggers_on_fifth_event_only():
    actor_id = uuid4()
    organization_id = uuid4()
    target_id = str(uuid4())
    events = [
        _event(
            offset_seconds=index * 30,
            actor_id=actor_id,
            organization_id=organization_id,
            target_id=target_id,
        )
        for index in range(5)
    ]

    assert _candidate(
        _evaluate(events[:4]), "repeated_permission_denied"
    ) is None

    candidate = _candidate(_evaluate(events), "repeated_permission_denied")
    assert candidate is not None
    assert candidate.rule_version == "v1"
    assert candidate.severity == "medium"
    assert candidate.organization_id == organization_id
    assert candidate.subject_actor_id == actor_id


def test_permission_denied_builds_cooldown_candidates_below_threshold():
    actor_id = uuid4()
    organization_id = uuid4()
    event = _event(
        offset_seconds=0,
        actor_id=actor_id,
        organization_id=organization_id,
    )
    module = importlib.import_module(
        "apps.shared.services.security_alert_rule_evaluator"
    )

    candidates = module.build_security_alert_cooldown_candidates(
        current_event=event,
        activation_started_at=_NOW - timedelta(minutes=1),
    )

    assert {candidate.rule_id for candidate in candidates} == {
        "repeated_permission_denied",
        "multi_resource_permission_probe",
    }
    assert all(
        candidate.matched_audit_ids == (event.id,) for candidate in candidates
    )


def test_sal_tc_w002_multi_resource_probe_counts_only_distinct_safe_targets():
    actor_id = uuid4()
    organization_id = uuid4()
    target_ids = [str(uuid4()) for _ in range(5)]
    first_four = [
        _event(
            offset_seconds=index * 30,
            actor_id=actor_id,
            organization_id=organization_id,
            target_id=target_id,
        )
        for index, target_id in enumerate(target_ids[:4])
    ]
    same_target_retry = _event(
        offset_seconds=150,
        actor_id=actor_id,
        organization_id=organization_id,
        target_id=target_ids[0],
    )
    fifth_distinct = _event(
        offset_seconds=180,
        actor_id=actor_id,
        organization_id=organization_id,
        target_id=target_ids[4],
    )

    assert _candidate(
        _evaluate([*first_four, same_target_retry]),
        "multi_resource_permission_probe",
    ) is None

    candidate = _candidate(
        _evaluate([*first_four, same_target_retry, fifth_distinct]),
        "multi_resource_permission_probe",
    )
    assert candidate is not None
    assert candidate.rule_version == "v1"
    assert candidate.severity == "high"


@pytest.mark.parametrize(
    ("metadata", "expected_reason"),
    [
        (
            {"policy_reason": "rag.pii_evidence_detected"},
            "rag.pii_evidence_detected",
        ),
        (
            {"policy_result": {"reason_code": "pii_policy_blocked"}},
            "rag.pii_evidence_detected",
        ),
        (
            {"policy_reason": "access_management.stale_state"},
            "access_management.stale_state",
        ),
    ],
)
def test_sal_tc_w003_repeated_policy_block_normalizes_and_groups_by_reason(
    metadata,
    expected_reason,
):
    actor_id = uuid4()
    organization_id = uuid4()
    events = [
        _event(
            offset_seconds=index * 60,
            actor_id=actor_id,
            organization_id=organization_id,
            action="policy.block",
            target_type="organization_membership",
            metadata=metadata,
        )
        for index in range(3)
    ]

    candidate = _candidate(_evaluate(events), "repeated_policy_block")

    assert candidate is not None
    assert candidate.rule_version == "v1"
    assert candidate.severity == "high"
    assert candidate.policy_reason == expected_reason


@pytest.mark.parametrize(
    "events",
    [
        _series(
            3,
            action="policy.block",
            metadata={"policy_reason": "budget.exceeded"},
        ),
        _series(5, action="auth.permission_denied"),
        _series(5, metadata={"organization_id": "not-a-uuid"}),
        _series(5, target_type=None, target_id=None),
    ],
    ids=["budget", "auth", "invalid-organization", "unsafe-target"],
)
def test_sal_tc_w004_ineligible_events_do_not_create_candidates(events):
    assert _evaluate(events) == ()


def test_sal_tc_u008_policy_reason_counts_are_isolated():
    actor_id = uuid4()
    organization_id = uuid4()
    stale_events = [
        _event(
            offset_seconds=index * 30,
            actor_id=actor_id,
            organization_id=organization_id,
            action="policy.block",
            metadata={"policy_reason": "access_management.stale_state"},
        )
        for index in range(2)
    ]
    pii_events = [
        _event(
            offset_seconds=60 + index * 30,
            actor_id=actor_id,
            organization_id=organization_id,
            action="policy.block",
            metadata={"policy_reason": "rag.pii_evidence_detected"},
        )
        for index in range(3)
    ]

    assert _candidate(
        _evaluate([*stale_events, *pii_events[:2]]),
        "repeated_policy_block",
    ) is None

    candidate = _candidate(
        _evaluate([*stale_events, *pii_events]),
        "repeated_policy_block",
    )
    assert candidate is not None
    assert candidate.policy_reason == "rag.pii_evidence_detected"


def test_sal_tc_u009_detection_key_isolates_every_key_dimension():
    module = importlib.import_module(
        "apps.shared.services.security_alert_rule_evaluator"
    )
    organization_id = uuid4()
    actor_id = uuid4()
    base = {
        "organization_id": organization_id,
        "actor_id": actor_id,
        "rule_id": "repeated_policy_block",
        "rule_version": "v1",
        "policy_reason": "rag.pii_evidence_detected",
    }

    base_key = module.build_security_alert_detection_key(**base)
    changed_keys = {
        module.build_security_alert_detection_key(
            **{**base, "organization_id": uuid4()}
        ),
        module.build_security_alert_detection_key(**{**base, "actor_id": uuid4()}),
        module.build_security_alert_detection_key(
            **{**base, "rule_id": "repeated_permission_denied"}
        ),
        module.build_security_alert_detection_key(
            **{**base, "rule_version": "v2"}
        ),
        module.build_security_alert_detection_key(
            **{**base, "policy_reason": "access_management.stale_state"}
        ),
    }

    assert len(base_key) <= 255
    assert base_key not in changed_keys
    assert len(changed_keys) == 5


def test_sal_tc_u010_window_includes_both_edges_and_ignores_outside_events():
    actor_id = uuid4()
    organization_id = uuid4()
    target_id = str(uuid4())
    current_event = _event(
        offset_seconds=0,
        actor_id=actor_id,
        organization_id=organization_id,
        target_id=target_id,
    )
    exact_start = _event(
        offset_seconds=-300,
        actor_id=actor_id,
        organization_id=organization_id,
        target_id=target_id,
    )
    inside_events = [
        _event(
            offset_seconds=offset,
            actor_id=actor_id,
            organization_id=organization_id,
            target_id=target_id,
        )
        for offset in (-240, -120, -1)
    ]
    just_before_start = _event(
        offset_seconds=-301,
        actor_id=actor_id,
        organization_id=organization_id,
        target_id=target_id,
    )
    after_current = _event(
        offset_seconds=1,
        actor_id=actor_id,
        organization_id=organization_id,
        target_id=target_id,
    )
    window_events = [
        after_current,
        *inside_events,
        just_before_start,
        current_event,
        exact_start,
    ]

    candidate = _candidate(
        _evaluate_at(
            current_event=current_event,
            window_events=window_events,
            activation_started_at=_NOW - timedelta(minutes=10),
        ),
        "repeated_permission_denied",
    )
    assert candidate is not None

    without_start_edge = [
        event for event in window_events if event.id != exact_start.id
    ]
    assert _candidate(
        _evaluate_at(
            current_event=current_event,
            window_events=without_start_edge,
            activation_started_at=_NOW - timedelta(minutes=10),
        ),
        "repeated_permission_denied",
    ) is None


def test_same_audit_id_is_counted_once_per_rule():
    actor_id = uuid4()
    organization_id = uuid4()
    target_id = str(uuid4())
    unique_events = [
        _event(
            offset_seconds=index * 30,
            actor_id=actor_id,
            organization_id=organization_id,
            target_id=target_id,
        )
        for index in range(5)
    ]

    duplicated_window = [*unique_events[:4], unique_events[0]]
    assert _candidate(
        _evaluate_at(
            current_event=unique_events[3],
            window_events=duplicated_window,
            activation_started_at=_NOW - timedelta(minutes=1),
        ),
        "repeated_permission_denied",
    ) is None

    assert _candidate(
        _evaluate(unique_events),
        "repeated_permission_denied",
    ) is not None


def test_repeated_permission_candidate_contains_only_its_five_minute_evidence():
    module = importlib.import_module(
        "apps.shared.services.security_alert_rule_evaluator"
    )
    actor_id = uuid4()
    organization_id = uuid4()
    target_id = str(uuid4())
    matched_events = [
        _event(
            offset_seconds=offset,
            actor_id=actor_id,
            organization_id=organization_id,
            target_id=target_id,
        )
        for offset in (-240, -180, -120, -60, 0)
    ]
    outside_window = _event(
        offset_seconds=-301,
        actor_id=actor_id,
        organization_id=organization_id,
        target_id=target_id,
    )
    other_organization = _event(
        offset_seconds=-30,
        actor_id=actor_id,
        organization_id=uuid4(),
        target_id=target_id,
    )
    other_actor = _event(
        offset_seconds=-20,
        actor_id=uuid4(),
        organization_id=organization_id,
        target_id=target_id,
    )

    candidate = _candidate(
        _evaluate_at(
            current_event=matched_events[-1],
            window_events=[
                outside_window,
                other_organization,
                *reversed(matched_events),
                other_actor,
            ],
            activation_started_at=_NOW - timedelta(minutes=10),
        ),
        "repeated_permission_denied",
    )

    assert candidate is not None
    assert set(candidate.matched_audit_ids) == {
        event.id for event in matched_events
    }
    assert candidate.detection_key == module.build_security_alert_detection_key(
        organization_id=organization_id,
        actor_id=actor_id,
        rule_id="repeated_permission_denied",
        rule_version="v1",
    )


def test_policy_candidate_evidence_does_not_include_other_reason():
    actor_id = uuid4()
    organization_id = uuid4()
    matched_events = [
        _event(
            offset_seconds=offset,
            actor_id=actor_id,
            organization_id=organization_id,
            action="policy.block",
            metadata={"policy_reason": "rag.pii_evidence_detected"},
        )
        for offset in (-120, -60, 0)
    ]
    other_reason_events = [
        _event(
            offset_seconds=offset,
            actor_id=actor_id,
            organization_id=organization_id,
            action="policy.block",
            metadata={"policy_reason": "access_management.stale_state"},
        )
        for offset in (-110, -50, -10)
    ]

    candidate = _candidate(
        _evaluate_at(
            current_event=matched_events[-1],
            window_events=[*other_reason_events, *matched_events],
            activation_started_at=_NOW - timedelta(minutes=10),
        ),
        "repeated_policy_block",
    )

    assert candidate is not None
    assert set(candidate.matched_audit_ids) == {
        event.id for event in matched_events
    }


def test_multi_resource_candidate_evidence_excludes_unsafe_target():
    actor_id = uuid4()
    organization_id = uuid4()
    safe_events = [
        _event(
            offset_seconds=index * 30,
            actor_id=actor_id,
            organization_id=organization_id,
            target_id=str(uuid4()),
        )
        for index in range(5)
    ]
    same_target_retry = _event(
        offset_seconds=100,
        actor_id=actor_id,
        organization_id=organization_id,
        target_id=safe_events[0].target_id,
    )
    unsafe_target = _event(
        offset_seconds=110,
        actor_id=actor_id,
        organization_id=organization_id,
        target_type=None,
        target_id=None,
    )

    candidate = _candidate(
        _evaluate_at(
            current_event=safe_events[-1],
            window_events=[*safe_events, same_target_retry, unsafe_target],
            activation_started_at=_NOW - timedelta(minutes=1),
        ),
        "multi_resource_permission_probe",
    )

    assert candidate is not None
    assert set(candidate.matched_audit_ids) == {
        event.id for event in [*safe_events, same_target_retry]
    }
