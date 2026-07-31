from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from apps.log_system import provider_usage_tasks


def test_provider_usage_reconciliation_is_registered_on_log_queue() -> None:
    task_name = "provider_usage.reconcile"

    assert task_name in provider_usage_tasks.celery_app.tasks
    route = provider_usage_tasks.celery_app.amqp.router.route({}, task_name)
    assert route["queue"].name == "log"
    schedules = [
        entry
        for entry in (
            provider_usage_tasks.celery_app.conf.beat_schedule or {}
        ).values()
        if entry.get("task") == task_name
    ]
    assert schedules == [
        {
            "task": task_name,
            "schedule": 60.0,
            "options": {"queue": "log"},
        }
    ]


def test_provider_usage_reconciliation_runs_stale_fence_before_projection(
    monkeypatch,
) -> None:
    events: list[tuple] = []

    class Session:
        def rollback(self):
            events.append(("rollback",))

        def close(self):
            events.append(("close",))

    class Service:
        def reconcile_stale_provider_started(
            self,
            db,
            *,
            stale_after,
            limit,
        ):
            events.append(("stale", db, stale_after, limit))
            return ("operation-1",)

        def reconcile_pending_projections(self, db, *, limit):
            events.append(("projection", db, limit))
            return SimpleNamespace(
                claimed_count=2,
                projected_count=1,
                failed_count=1,
            )

    session = Session()
    monkeypatch.setattr(provider_usage_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        provider_usage_tasks,
        "ProviderUsageLedgerService",
        Service,
    )

    result = provider_usage_tasks.reconcile_provider_usage.run(limit=25)

    assert events == [
        ("stale", session, timedelta(minutes=15), 25),
        ("projection", session, 25),
        ("close",),
    ]
    assert result == {
        "stale_started_count": 1,
        "projection_claimed_count": 2,
        "projection_projected_count": 1,
        "projection_failed_count": 1,
    }
