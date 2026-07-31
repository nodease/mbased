from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from apps.gateway.application.deployment.schedule_errors import (
    ScheduleConfigurationError,
)
from apps.gateway.application.deployment.schedule_models import (
    SchedulePublishBatch,
    SchedulePublishRequest,
    SchedulePublishResult,
)
from apps.gateway.composition.deployment import (
    build_schedule_dispatch_dependencies,
    build_schedule_next_fire_calculator,
)
from apps.gateway.services.scheduler_service import SchedulerService
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
)
from apps.shared.domain.schedule_dispatch import ScheduleDispatchSettings

NOW = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)


class _Scalar:
    def scalar_one(self):
        return NOW


class _Db:
    def __init__(self):
        self.commits = 0

    def execute(self, _statement):
        return _Scalar()

    def commit(self):
        self.commits += 1


class _Publisher:
    def __init__(self, *, fails=False):
        self.fails = fails
        self.requests = []

    def publish(self, request):
        self.requests.append(request)
        if self.fails:
            raise RuntimeError("provider detail must not escape")


def _service(*, mode="claim", publisher=None, maintenance_enabled=True):
    return SchedulerService(
        runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
        settings=ScheduleDispatchSettings(mode=mode),
        session_factory=lambda: (_ for _ in ()).throw(
            AssertionError("unexpected session")
        ),
        publisher=publisher or _Publisher(),
        dependency_builder=build_schedule_dispatch_dependencies,
        next_fire=build_schedule_next_fire_calculator(),
        start_background=False,
        maintenance_enabled=maintenance_enabled,
    )


def test_disabled_mode_is_a_kill_switch_without_legacy_fallback_scheduler():
    service = _service(mode="disabled")

    assert service.scheduler is None
    service.run_tick_once()


def test_disabled_mode_runs_only_visibility_and_retention_maintenance(monkeypatch):
    service = _service(mode="disabled")
    calls = []
    monkeypatch.setattr(service, "_recover_critical", lambda: calls.append("recover"))
    monkeypatch.setattr(
        service,
        "_maintain_visibility",
        lambda: calls.append("visibility"),
    )
    monkeypatch.setattr(
        service,
        "_cleanup_terminal_claims",
        lambda: calls.append("cleanup"),
    )
    monkeypatch.setattr(
        service,
        "_observe_claim_ages",
        lambda: calls.append("claim_age"),
    )

    service.run_tick_once()

    assert calls == ["visibility", "cleanup", "claim_age"]


def test_disabled_mode_skips_maintenance_when_schema_is_not_ready(monkeypatch):
    service = _service(mode="disabled", maintenance_enabled=False)
    calls = []
    monkeypatch.setattr(
        service,
        "_maintain_visibility",
        lambda: calls.append("visibility"),
    )

    service.run_tick_once()

    assert calls == []


def test_add_schedule_validates_and_sets_cursor_without_commit():
    service = _service()
    db = _Db()
    schedule = SimpleNamespace(
        cron_expression="0 * * * *",
        timezone="UTC",
        next_run_at=None,
        configuration_error_code="schedule_configuration_invalid",
    )

    service.add_schedule(schedule, db)

    assert schedule.next_run_at == datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
    assert schedule.configuration_error_code is None
    assert db.commits == 0


def test_add_schedule_rejects_invalid_configuration_without_commit():
    service = _service()
    db = _Db()
    schedule = SimpleNamespace(
        cron_expression="invalid",
        timezone="UTC",
        next_run_at=None,
    )

    with pytest.raises(ScheduleConfigurationError):
        service.add_schedule(schedule, db)

    assert db.commits == 0


def test_configuration_preflight_is_wired_to_publish_preparation_only():
    service = _service()
    session = SimpleNamespace(close=lambda: None)
    configuration_preflight = object()
    dependencies = SimpleNamespace(
        repository=object(),
        budget=object(),
        configuration_preflight=configuration_preflight,
        audit=object(),
        uow=object(),
    )
    calls = {}

    class _OccurrenceUseCase:
        def claim_due_occurrences(self, **kwargs):
            calls["occurrence"] = kwargs
            return 0

    class _DispatchUseCase:
        def prepare_publish_batch(self, **kwargs):
            calls["dispatch"] = kwargs
            return SchedulePublishBatch(requests=())

    service.session_factory = lambda: session
    service.dependency_builder = lambda _db: dependencies
    service.occurrence_use_case = _OccurrenceUseCase()
    service.dispatch_use_case = _DispatchUseCase()

    service._claim_due_occurrences()
    service._prepare_publish_batch()

    assert "configuration_preflight" not in calls["occurrence"]
    assert calls["dispatch"]["configuration_preflight"] is configuration_preflight


def test_tick_publishes_after_prepare_and_records_success(monkeypatch):
    publisher = _Publisher()
    service = _service(publisher=publisher)
    request = SchedulePublishRequest(
        claim_id=__import__("uuid").uuid4(),
        task_id="schedule:test",
        lease_owner="owner",
    )
    order = []
    monkeypatch.setattr(service, "_recover_critical", lambda: order.append("recover"))
    monkeypatch.setattr(
        service,
        "_reconcile_uninitialized",
        lambda: order.append("reconcile"),
    )
    monkeypatch.setattr(
        service,
        "_claim_due_occurrences",
        lambda: order.append("claim"),
    )
    monkeypatch.setattr(
        service,
        "_prepare_publish_batch",
        lambda: order.append("prepare") or SchedulePublishBatch(requests=(request,)),
    )
    monkeypatch.setattr(
        service,
        "_record_publish_result",
        lambda _request, *, accepted: (
            order.append(f"result:{accepted}") or SchedulePublishResult(changed=True)
        ),
    )
    monkeypatch.setattr(
        service, "_maintain_visibility", lambda: order.append("visibility")
    )
    monkeypatch.setattr(
        service, "_cleanup_terminal_claims", lambda: order.append("cleanup")
    )

    service.run_tick_once()

    assert order == [
        "recover",
        "reconcile",
        "claim",
        "prepare",
        "result:True",
        "visibility",
        "cleanup",
    ]
    assert publisher.requests == [request]


def test_publish_provider_failure_is_recorded_as_failed_without_raising(
    monkeypatch,
    caplog,
):
    publisher = _Publisher(fails=True)
    service = _service(publisher=publisher)
    request = SchedulePublishRequest(
        claim_id=__import__("uuid").uuid4(),
        task_id="schedule:test",
        lease_owner="owner",
    )
    results = []
    monkeypatch.setattr(service, "_recover_critical", lambda: None)
    monkeypatch.setattr(service, "_reconcile_uninitialized", lambda: None)
    monkeypatch.setattr(service, "_claim_due_occurrences", lambda: None)
    monkeypatch.setattr(
        service,
        "_prepare_publish_batch",
        lambda: SchedulePublishBatch(requests=(request,)),
    )
    monkeypatch.setattr(
        service,
        "_record_publish_result",
        lambda _request, *, accepted: (
            results.append(accepted) or SchedulePublishResult(changed=True)
        ),
    )
    monkeypatch.setattr(service, "_maintain_visibility", lambda: None)
    monkeypatch.setattr(service, "_cleanup_terminal_claims", lambda: None)

    service.run_tick_once()

    assert results == [False]
    assert str(request.claim_id) not in "\n".join(caplog.messages)
    assert "provider detail must not escape" not in "\n".join(caplog.messages)


def test_tick_emits_committed_dead_letter_reasons(monkeypatch):
    service = _service()
    signals = []
    monkeypatch.setattr(service, "_recover_critical", lambda: None)
    monkeypatch.setattr(service, "_reconcile_uninitialized", lambda: None)
    monkeypatch.setattr(service, "_claim_due_occurrences", lambda: None)
    monkeypatch.setattr(
        service,
        "_prepare_publish_batch",
        lambda: SchedulePublishBatch(
            requests=(),
            enqueue_attempts_exhausted=2,
            budget_evaluation_failed=1,
        ),
    )
    monkeypatch.setattr(service, "_maintain_visibility", lambda: None)
    monkeypatch.setattr(service, "_cleanup_terminal_claims", lambda: None)
    monkeypatch.setattr(service, "_observe_claim_ages", lambda: None)
    monkeypatch.setattr(
        "apps.gateway.services.scheduler_service.emit_schedule_dispatch_signal",
        lambda _logger, event, **kwargs: signals.append((event, kwargs)),
    )

    service.run_tick_once()

    assert signals == [
        (
            "schedule_claim_dead_letter_total",
            {
                "value": 2,
                "status": "dead_lettered",
                "reason": "enqueue_attempts_exhausted",
                "mode": "claim",
            },
        ),
        (
            "schedule_claim_dead_letter_total",
            {
                "value": 1,
                "status": "dead_lettered",
                "reason": "budget_evaluation_failed",
                "mode": "claim",
            },
        ),
    ]


def test_publish_result_failure_does_not_block_remaining_batch(
    monkeypatch,
    caplog,
):
    publisher = _Publisher()
    service = _service(publisher=publisher)
    requests = tuple(
        SchedulePublishRequest(
            claim_id=__import__("uuid").uuid4(),
            task_id=f"schedule:test-{index}",
            lease_owner="owner",
        )
        for index in range(2)
    )
    recorded = []
    monkeypatch.setattr(service, "_recover_critical", lambda: None)
    monkeypatch.setattr(service, "_reconcile_uninitialized", lambda: None)
    monkeypatch.setattr(service, "_claim_due_occurrences", lambda: None)
    monkeypatch.setattr(
        service,
        "_prepare_publish_batch",
        lambda: SchedulePublishBatch(requests=requests),
    )

    def record(request, *, accepted):
        recorded.append((request.claim_id, accepted))
        if request is requests[0]:
            raise RuntimeError("storage detail must not escape")
        return SchedulePublishResult(changed=True)

    monkeypatch.setattr(service, "_record_publish_result", record)
    monkeypatch.setattr(service, "_maintain_visibility", lambda: None)
    monkeypatch.setattr(service, "_cleanup_terminal_claims", lambda: None)

    service.run_tick_once()

    assert publisher.requests == list(requests)
    assert recorded == [
        (requests[0].claim_id, True),
        (requests[1].claim_id, True),
    ]
    messages = "\n".join(caplog.messages)
    assert all(str(request.claim_id) not in messages for request in requests)
    assert "storage detail must not escape" not in messages


def test_drain_mode_skips_new_occurrence_claiming(monkeypatch):
    service = _service(mode="drain")
    calls = []
    monkeypatch.setattr(service, "_recover_critical", lambda: calls.append("recover"))
    monkeypatch.setattr(
        service,
        "_reconcile_uninitialized",
        lambda: calls.append("unexpected"),
    )
    monkeypatch.setattr(
        service,
        "_claim_due_occurrences",
        lambda: calls.append("unexpected"),
    )
    monkeypatch.setattr(
        service,
        "_prepare_publish_batch",
        lambda: SchedulePublishBatch(requests=()),
    )
    monkeypatch.setattr(
        service, "_maintain_visibility", lambda: calls.append("visibility")
    )
    monkeypatch.setattr(
        service, "_cleanup_terminal_claims", lambda: calls.append("cleanup")
    )
    monkeypatch.setattr(
        service, "_observe_claim_ages", lambda: calls.append("claim_age")
    )

    service.run_tick_once()

    assert calls == ["recover", "visibility", "cleanup", "claim_age"]


def test_optional_maintenance_failures_do_not_block_dispatch(monkeypatch):
    service = _service(mode="claim")
    calls = []
    monkeypatch.setattr(service, "_recover_critical", lambda: calls.append("recover"))
    monkeypatch.setattr(service, "_reconcile_uninitialized", lambda: None)
    monkeypatch.setattr(service, "_claim_due_occurrences", lambda: None)
    monkeypatch.setattr(
        service,
        "_prepare_publish_batch",
        lambda: calls.append("dispatch") or SchedulePublishBatch(requests=()),
    )
    monkeypatch.setattr(
        service,
        "_maintain_visibility",
        lambda: (_ for _ in ()).throw(RuntimeError("private detail")),
    )
    monkeypatch.setattr(
        service,
        "_cleanup_terminal_claims",
        lambda: (_ for _ in ()).throw(RuntimeError("private detail")),
    )
    monkeypatch.setattr(
        service,
        "_observe_claim_ages",
        lambda: (_ for _ in ()).throw(RuntimeError("private detail")),
    )

    service.run_tick_once()

    assert calls == ["recover", "dispatch"]
