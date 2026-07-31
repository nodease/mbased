from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import and_
from sqlalchemy.dialects import postgresql


@dataclass(frozen=True)
class _AuditEvent:
    id: UUID
    occurred_at: datetime
    actor_id: UUID
    actor_type: str
    category: str
    action: str
    target_type: str | None
    target_id: str | None
    status: str
    audit_metadata: dict[str, Any]


_NOW = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _denial(*, index: int, actor_id: UUID, organization_id: UUID) -> _AuditEvent:
    return _AuditEvent(
        id=uuid4(),
        occurred_at=_NOW + timedelta(seconds=index * 30),
        actor_id=actor_id,
        actor_type="user",
        category="action",
        action="permission.denied",
        target_type="workflow",
        target_id=str(uuid4()),
        status="failure",
        audit_metadata={"organization_id": str(organization_id)},
    )


def test_replay_reports_rule_matches_without_mutating_events():
    from apps.shared.services.security_alert_rule_replay import (
        replay_security_alert_rules,
    )

    actor_id = uuid4()
    organization_id = uuid4()
    events = [
        _denial(index=index, actor_id=actor_id, organization_id=organization_id)
        for index in range(5)
    ]
    before = [event.audit_metadata.copy() for event in events]

    result = replay_security_alert_rules(
        events,
        activation_started_at=_NOW - timedelta(minutes=1),
        evaluation_started_at=_NOW,
        evaluation_ended_at=_NOW + timedelta(minutes=5),
    )

    assert result.evaluated_event_count == 5
    assert result.matched_event_count == 1
    assert result.detection_count == 2
    assert result.detections_by_rule == {
        "multi_resource_permission_probe": 1,
        "repeated_permission_denied": 1,
        "repeated_policy_block": 0,
    }
    assert result.safe_dict() == {
        "dry_run": True,
        "evaluated_event_count": 5,
        "matched_event_count": 1,
        "detection_count": 2,
        "detections_by_rule": {
            "multi_resource_permission_probe": 1,
            "repeated_permission_denied": 1,
            "repeated_policy_block": 0,
        },
    }
    assert [event.audit_metadata for event in events] == before


def test_replay_uses_lookback_events_but_only_counts_requested_period():
    from apps.shared.services.security_alert_rule_replay import (
        replay_security_alert_rules,
    )

    actor_id = uuid4()
    organization_id = uuid4()
    events = [
        _denial(index=index, actor_id=actor_id, organization_id=organization_id)
        for index in range(5)
    ]
    evaluation_started_at = events[-1].occurred_at

    result = replay_security_alert_rules(
        events,
        activation_started_at=_NOW - timedelta(minutes=1),
        evaluation_started_at=evaluation_started_at,
        evaluation_ended_at=evaluation_started_at + timedelta(seconds=1),
    )

    assert result.evaluated_event_count == 1
    assert result.matched_event_count == 1
    assert result.detection_count == 2


def test_replay_cli_can_run_directly_from_repository_root():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/replay_security_alert_rules.py",
            "--help",
        ],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--organization-id" in result.stdout


def test_replay_event_partition_query_uses_half_open_bounds_and_overflow_probe():
    from scripts import replay_security_alert_rules as command

    organization_id = uuid4()
    started_at = _NOW - timedelta(minutes=10)
    ended_at = _NOW

    class _Query:
        criteria = ()
        ordering = ()
        limit_value = None

        def filter(self, *criteria):
            self.criteria = criteria
            return self

        def order_by(self, *ordering):
            self.ordering = ordering
            return self

        def limit(self, value):
            self.limit_value = value
            return self

        def all(self):
            return []

    query = _Query()

    class _Session:
        def query(self, model):
            assert model is command.AuditLog
            return query

    rows = command._load_events(
        _Session(),
        organization_id=organization_id,
        start_at=started_at,
        end_at=ended_at,
        limit=100,
    )

    compiled = and_(*query.criteria).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert rows == []
    assert "audit_logs.occurred_at >=" in sql
    assert "audit_logs.occurred_at <" in sql
    assert compiled.params["occurred_at_1"] == started_at
    assert compiled.params["occurred_at_2"] == ended_at
    assert compiled.params["param_1"] == str(organization_id)
    assert compiled.params["action_1"] == ["permission.denied", "policy.block"]
    assert query.limit_value == 101
    assert [str(column) for column in query.ordering] == [
        "AuditLog.occurred_at",
        "AuditLog.id",
    ]


def test_replay_cli_is_read_only_and_prints_only_safe_aggregates(
    monkeypatch,
    capsys,
):
    from scripts import replay_security_alert_rules as command

    actor_id = uuid4()
    organization_id = uuid4()
    events = [
        _denial(index=index, actor_id=actor_id, organization_id=organization_id)
        for index in range(5)
    ]

    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def limit(self, value):
            assert value == 101
            return self

        def all(self):
            return self.rows

    class _Session:
        def __init__(self):
            self.closed = False
            self.results = [[], events]

        def query(self, model):
            return _Query(self.results.pop(0))

        def close(self):
            self.closed = True

    session = _Session()
    monkeypatch.setattr(command, "SessionLocal", lambda: session)

    exit_code = command.main(
        [
            "--organization-id",
            str(organization_id),
            "--start-at",
            _NOW.isoformat(),
            "--end-at",
            (_NOW + timedelta(minutes=5)).isoformat(),
            "--limit",
            "100",
        ]
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert session.closed is True
    assert payload == {
        "detection_count": 2,
        "detections_by_rule": {
            "multi_resource_permission_probe": 1,
            "repeated_permission_denied": 1,
            "repeated_policy_block": 0,
        },
        "dry_run": True,
        "evaluated_event_count": 5,
        "loaded_event_count": 5,
        "matched_event_count": 1,
        "truncated": False,
    }
    assert str(actor_id) not in output
    assert all(event.target_id not in output for event in events)


@pytest.mark.parametrize("overflow_partition", ["lookback", "evaluation"])
def test_replay_cli_fails_closed_when_any_partition_exceeds_limit(
    monkeypatch,
    capsys,
    overflow_partition,
):
    from scripts import replay_security_alert_rules as command

    actor_id = uuid4()
    organization_id = uuid4()
    limit = 5
    lookback_count = limit + 1 if overflow_partition == "lookback" else 1
    evaluation_count = limit + 1 if overflow_partition == "evaluation" else 1
    lookback_events = [
        replace(
            _denial(
                index=index,
                actor_id=actor_id,
                organization_id=organization_id,
            ),
            occurred_at=_NOW - timedelta(minutes=5) + timedelta(seconds=index),
        )
        for index in range(lookback_count)
    ]
    evaluation_events = [
        _denial(
            index=index,
            actor_id=actor_id,
            organization_id=organization_id,
        )
        for index in range(evaluation_count)
    ]

    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def limit(self, value):
            assert value == limit + 1
            return self

        def all(self):
            return self.rows

    class _Session:
        def __init__(self):
            self.closed = False
            self.results = [lookback_events, evaluation_events]

        def query(self, model):
            return _Query(self.results.pop(0))

        def close(self):
            self.closed = True

    session = _Session()
    monkeypatch.setattr(command, "SessionLocal", lambda: session)

    def unexpected_replay(*args, **kwargs):
        raise AssertionError("partial replay must not run")

    monkeypatch.setattr(command, "replay_security_alert_rules", unexpected_replay)

    exit_code = command.main(
        [
            "--organization-id",
            str(organization_id),
            "--start-at",
            _NOW.isoformat(),
            "--end-at",
            (_NOW + timedelta(minutes=5)).isoformat(),
            "--limit",
            str(limit),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert session.closed is True
    assert json.loads(output) == {
        "dry_run": True,
        "error": "security_alert.replay_limit_exceeded",
        "truncated": True,
    }
    assert str(actor_id) not in output
