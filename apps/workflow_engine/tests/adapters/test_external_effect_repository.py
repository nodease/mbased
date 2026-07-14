from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from apps.workflow_engine.adapters.db import (
    external_effect_repository as repository_module,
)
from apps.workflow_engine.adapters.db.external_effect_repository import (
    SQLAlchemyEffectAttemptRepository,
)
from apps.workflow_engine.application.external_effect import AcquireKind


def test_database_now_uses_postgresql_wall_clock() -> None:
    statements = []

    class _Result:
        def scalar_one(self):
            return datetime(2026, 1, 1, tzinfo=timezone.utc)

    class _Session:
        def execute(self, statement):
            statements.append(str(statement))
            return _Result()

    SQLAlchemyEffectAttemptRepository._database_now(_Session())

    assert statements == ["SELECT clock_timestamp()"]


def test_effect_admission_uses_app_workflow_lifecycle_lock() -> None:
    source = __import__("inspect").getsource(
        SQLAlchemyEffectAttemptRepository.acquire
    )

    assert "lock_app_workflow_for_admission" in source
    assert "external_effect.stopped" in source


def test_wait_does_not_sleep_past_task_deadline(monkeypatch) -> None:
    elapsed = [0.0]
    sleeps: list[float] = []
    database_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    record = SimpleNamespace(
        status="prepared",
        claim_expires_at=database_start + timedelta(seconds=30),
    )
    repository = object.__new__(SQLAlchemyEffectAttemptRepository)
    repository._read_by_id = lambda _record: (
        record,
        database_start + timedelta(seconds=elapsed[0]),
    )

    monkeypatch.setattr(repository_module.time, "monotonic", lambda: elapsed[0])

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        elapsed[0] += seconds

    monkeypatch.setattr(repository_module.time, "sleep", sleep)

    result = repository.wait_for_resolution(record, deadline=0.25)

    assert result.kind is AcquireKind.WAIT
    assert elapsed[0] == 0.25
    assert max(sleeps) <= 0.15


def test_wait_does_not_sleep_past_database_claim_expiry(monkeypatch) -> None:
    elapsed = [0.0]
    database_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    record = SimpleNamespace(
        status="prepared",
        claim_expires_at=database_start + timedelta(seconds=0.05),
    )
    repository = object.__new__(SQLAlchemyEffectAttemptRepository)
    repository._read_by_id = lambda _record: (
        record,
        database_start + timedelta(seconds=elapsed[0]),
    )

    monkeypatch.setattr(repository_module.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(
        repository_module.time,
        "sleep",
        lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
    )

    result = repository.wait_for_resolution(record, deadline=1.0)

    assert result.kind is AcquireKind.WAIT
    assert elapsed[0] == 0.05
