from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import check_schedule_dispatch_rollback, review_schedule_dispatch_claim
from scripts.check_schedule_dispatch_rollback import (
    _active_schedule_task_count,
    _observe_stable_drain,
    _workflow_queue_depth,
)


class _Inspector:
    def active(self):
        return {
            "worker-1": [
                {"name": "workflow.execute_scheduled_deployment"},
                {"name": "workflow.execute_by_deployment"},
            ]
        }

    def reserved(self):
        return {
            "worker-1": [
                {
                    "request": {
                        "name": "workflow.execute_scheduled_deployment",
                        "args": ["must-not-be-read"],
                    }
                }
            ]
        }

    def scheduled(self):
        return {"worker-1": []}


class _Control:
    def inspect(self, *, timeout):
        assert timeout == 3.0
        return _Inspector()


class _Celery:
    control = _Control()


def test_rollback_preflight_counts_only_dedicated_schedule_tasks(monkeypatch):
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback.celery_app",
        _Celery(),
    )

    assert _active_schedule_task_count(timeout=3.0) == 2
    assert _active_schedule_task_count(timeout=3.0, include_legacy=True) == 3
    assert (
        _active_schedule_task_count(
            timeout=3.0,
            expected_workers=frozenset({"worker-1"}),
        )
        == 2
    )


def test_rollback_preflight_fails_closed_when_worker_inspection_is_unavailable(
    monkeypatch,
):
    class _UnavailableInspector(_Inspector):
        def active(self):
            return None

    class _UnavailableControl:
        def inspect(self, *, timeout):
            return _UnavailableInspector()

    class _UnavailableCelery:
        control = _UnavailableControl()

    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback.celery_app",
        _UnavailableCelery(),
    )

    try:
        _active_schedule_task_count(timeout=3.0)
    except RuntimeError as exc:
        assert str(exc) == "worker task inspection is unavailable"
    else:
        raise AssertionError("worker inspection must fail closed")


def test_transition_preflight_rejects_partial_worker_response(monkeypatch):
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback.celery_app",
        _Celery(),
    )

    with pytest.raises(RuntimeError, match="worker task inspection is incomplete"):
        _active_schedule_task_count(
            timeout=3.0,
            expected_workers=frozenset({"worker-1", "worker-2"}),
        )


def test_transition_preflight_detects_task_moving_reserved_to_active_during_sweep(
    monkeypatch,
):
    class _MovingInspector:
        def __init__(self):
            self.active_calls = 0
            self.state = "reserved"

        def active(self):
            self.active_calls += 1
            tasks = (
                [{"name": "workflow.execute_scheduled_deployment"}]
                if self.active_calls == 2 and self.state == "active"
                else []
            )
            return {"worker-1": tasks}

        def reserved(self):
            if self.state == "reserved":
                self.state = "active"
            return {"worker-1": []}

        def scheduled(self):
            return {"worker-1": []}

    class _MovingControl:
        def inspect(self, *, timeout):
            assert timeout == 3.0
            return _MovingInspector()

    class _MovingCelery:
        control = _MovingControl()

    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback.celery_app",
        _MovingCelery(),
    )

    assert (
        _active_schedule_task_count(
            timeout=3.0,
            expected_workers=frozenset({"worker-1"}),
        )
        == 1
    )


def test_transition_preflight_validates_worker_set_on_reverse_sweep(monkeypatch):
    class _DisappearingWorkerInspector(_Inspector):
        def __init__(self):
            self.active_calls = 0

        def active(self):
            self.active_calls += 1
            if self.active_calls == 2:
                return {}
            return {"worker-1": []}

        def reserved(self):
            return {"worker-1": []}

        def scheduled(self):
            return {"worker-1": []}

    class _DisappearingWorkerControl:
        def inspect(self, *, timeout):
            return _DisappearingWorkerInspector()

    class _DisappearingWorkerCelery:
        control = _DisappearingWorkerControl()

    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback.celery_app",
        _DisappearingWorkerCelery(),
    )

    with pytest.raises(RuntimeError, match="worker task inspection is incomplete"):
        _active_schedule_task_count(
            timeout=3.0,
            expected_workers=frozenset({"worker-1"}),
        )


@pytest.mark.parametrize("queue_depths", ((1, 0), (0, 1)))
def test_transition_preflight_requires_zero_queue_around_worker_inspection(
    monkeypatch,
    queue_depths,
):
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback._active_schedule_task_count",
        lambda **kwargs: 0,
    )
    observed_queue_depths = iter(queue_depths)
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback._workflow_queue_depth",
        lambda **kwargs: next(observed_queue_depths),
    )

    assert _observe_stable_drain(
        timeout=3.0,
        include_legacy=True,
        expected_workers=frozenset({"worker-1"}),
        stable_observations=2,
        stability_seconds=0.5,
    ) == (0, 1)


def test_transition_preflight_requires_two_stable_zero_observations(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback._active_schedule_task_count",
        lambda **kwargs: calls.append("active") or 0,
    )
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback._workflow_queue_depth",
        lambda **kwargs: calls.append("queue") or 0,
    )
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback.time.sleep",
        lambda seconds: calls.append(("sleep", seconds)),
    )

    assert _observe_stable_drain(
        timeout=3.0,
        include_legacy=True,
        expected_workers=frozenset({"worker-1"}),
        stable_observations=2,
        stability_seconds=0.5,
    ) == (0, 0)
    assert calls == [
        "queue",
        "active",
        "queue",
        "active",
        ("sleep", 0.5),
        "queue",
        "active",
        "queue",
        "active",
    ]


def test_transition_preflight_detects_task_moving_active_after_queue_check(
    monkeypatch,
):
    active_counts = iter((0, 1))
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback._active_schedule_task_count",
        lambda **kwargs: next(active_counts),
    )
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback._workflow_queue_depth",
        lambda **kwargs: 0,
    )

    assert _observe_stable_drain(
        timeout=3.0,
        include_legacy=True,
        expected_workers=frozenset({"worker-1"}),
        stable_observations=2,
        stability_seconds=0.5,
    ) == (1, 0)


@pytest.mark.parametrize("purpose", ("activation", "rollback"))
@pytest.mark.parametrize(
    ("nonterminal_claims", "unreviewed_outcomes"),
    ((1, 0), (0, 1)),
)
def test_transition_preflight_blocks_nonempty_durable_ledger_for_every_purpose(
    monkeypatch,
    capsys,
    purpose,
    nonterminal_claims,
    unreviewed_outcomes,
):
    calls = []
    session = SimpleNamespace(close=lambda: calls.append("close"))
    repository = object()

    class _UseCase:
        def evaluate(self, *, repository: object):
            calls.append(("evaluate", repository))
            return SimpleNamespace(
                ready=False,
                nonterminal_claims=nonterminal_claims,
                unreviewed_outcome_unknown_claims=unreviewed_outcomes,
            )

    monkeypatch.setattr(
        check_schedule_dispatch_rollback,
        "_arguments",
        lambda: SimpleNamespace(
            purpose=purpose,
            inspect_timeout=3.0,
            expected_worker_nodes=frozenset({"worker-1"}),
            stable_observations=2,
            stability_seconds=0.5,
        ),
    )
    monkeypatch.setattr(
        check_schedule_dispatch_rollback,
        "SessionLocal",
        lambda: session,
    )
    monkeypatch.setattr(
        check_schedule_dispatch_rollback,
        "SqlAlchemyScheduleDispatchRepository",
        lambda db: repository if db is session else None,
    )
    monkeypatch.setattr(
        check_schedule_dispatch_rollback,
        "ScheduleRollbackPreflightUseCase",
        _UseCase,
    )
    monkeypatch.setattr(
        check_schedule_dispatch_rollback,
        "_observe_stable_drain",
        lambda **kwargs: (0, 0),
    )

    assert check_schedule_dispatch_rollback.main() == 1
    assert calls == [("evaluate", repository), "close"]
    output = capsys.readouterr().out
    assert f"nonterminal_claims={nonterminal_claims}" in output
    assert f"unreviewed_outcomes={unreviewed_outcomes}" in output


@pytest.mark.parametrize("purpose", ("activation", "rollback"))
def test_transition_preflight_accepts_only_empty_ledger_and_stable_drain(
    monkeypatch,
    purpose,
):
    calls = []
    session = SimpleNamespace(close=lambda: calls.append("close"))

    class _UseCase:
        def evaluate(self, *, repository: object):
            calls.append("evaluate")
            return SimpleNamespace(
                ready=True,
                nonterminal_claims=0,
                unreviewed_outcome_unknown_claims=0,
            )

    monkeypatch.setattr(
        check_schedule_dispatch_rollback,
        "_arguments",
        lambda: SimpleNamespace(
            purpose=purpose,
            inspect_timeout=3.0,
            expected_worker_nodes=frozenset({"worker-1"}),
            stable_observations=2,
            stability_seconds=0.5,
        ),
    )
    monkeypatch.setattr(
        check_schedule_dispatch_rollback,
        "SessionLocal",
        lambda: session,
    )
    monkeypatch.setattr(
        check_schedule_dispatch_rollback,
        "SqlAlchemyScheduleDispatchRepository",
        lambda db: object(),
    )
    monkeypatch.setattr(
        check_schedule_dispatch_rollback,
        "ScheduleRollbackPreflightUseCase",
        _UseCase,
    )
    monkeypatch.setattr(
        check_schedule_dispatch_rollback,
        "_observe_stable_drain",
        lambda **kwargs: calls.append(("drain", kwargs["include_legacy"])) or (0, 0),
    )

    assert check_schedule_dispatch_rollback.main() == 0
    assert calls == ["evaluate", ("drain", purpose == "activation"), "close"]


def test_outcome_review_cli_emits_signal_only_after_success(monkeypatch):
    calls = []
    session = type("Session", (), {"close": lambda self: calls.append("close")})()
    result = type(
        "Result",
        (),
        {
            "claim_id": "00000000-0000-0000-0000-000000000001",
            "resolution": "confirmed_completed",
        },
    )()

    class _UseCase:
        def review(self, **kwargs):
            calls.append("review")
            return result

    monkeypatch.setattr(
        review_schedule_dispatch_claim,
        "_arguments",
        lambda: type(
            "Args",
            (),
            {
                "claim_id": result.claim_id,
                "resolution": result.resolution,
                "operation_correlation_id": "github-run:42",
            },
        )(),
    )
    monkeypatch.setattr(review_schedule_dispatch_claim, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        review_schedule_dispatch_claim,
        "ScheduleOutcomeReviewUseCase",
        _UseCase,
    )
    monkeypatch.setattr(
        review_schedule_dispatch_claim,
        "emit_schedule_dispatch_signal",
        lambda _logger, event, **kwargs: calls.append((event, kwargs)),
    )

    assert review_schedule_dispatch_claim.main() == 0
    assert calls == [
        "review",
        "close",
        (
            "schedule_claim_outcome_reviewed_total",
            {
                "status": "dead_lettered",
                "reason": "confirmed_completed",
            },
        ),
    ]


def test_transition_preflight_counts_workflow_priority_queues_without_reading_payload(
    monkeypatch,
):
    class _RedisClient:
        def __init__(self):
            self.closed = False

        def scan_iter(self, *, match):
            assert match == b"workflow*"
            return iter(
                [
                    b"workflow",
                    b"workflow\x06\x163",
                    b"workflow-result",
                ]
            )

        def type(self, key):
            return b"list"

        def llen(self, key):
            return {b"workflow": 2, b"workflow\x06\x163": 1}[key]

        def close(self):
            self.closed = True

    client = _RedisClient()
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback.redis.Redis.from_url",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(
        "scripts.check_schedule_dispatch_rollback.celery_app.conf.broker_url",
        "redis://localhost/0",
    )

    assert _workflow_queue_depth(timeout=3.0) == 3
    assert client.closed is True
