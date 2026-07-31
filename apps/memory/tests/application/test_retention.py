from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.memory.application.retention import (
    PublicReplayRetentionBatch,
    PurgeExpiredPublicSecretReplaysUseCase,
)
from apps.memory.tasks import _drain_expired_public_replays
from apps.shared.celery_app import celery_app


class _Repository:
    def __init__(
        self,
        *,
        idempotency_deleted_count: int = 0,
        secret_replay_deleted_count: int = 0,
        error: Exception | None = None,
    ) -> None:
        self.idempotency_deleted_count = idempotency_deleted_count
        self.secret_replay_deleted_count = secret_replay_deleted_count
        self.error = error
        self.calls = []

    def delete_expired_idempotency_records(self, *, now, limit):
        self.calls.append(("idempotency", now, limit))
        if self.error is not None:
            raise self.error
        return self.idempotency_deleted_count

    def delete_expired_secret_replays(self, *, now, limit):
        self.calls.append(("secret_replay", now, limit))
        if self.error is not None:
            raise self.error
        return self.secret_replay_deleted_count


class _UnitOfWork:
    def __init__(self) -> None:
        self.events = []

    def begin(self) -> None:
        self.events.append("begin")

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


def test_public_replay_retention_gives_parent_and_secret_replay_independent_quotas():
    repository = _Repository(
        idempotency_deleted_count=500,
        secret_replay_deleted_count=3,
    )
    uow = _UnitOfWork()
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)

    deleted = PurgeExpiredPublicSecretReplaysUseCase(
        repository=repository,
        uow=uow,
    ).execute(now=now, limit=500)

    assert deleted == PublicReplayRetentionBatch(
        idempotency_deleted_count=500,
        secret_replay_deleted_count=3,
    )
    assert deleted.deleted_count == 503
    assert repository.calls == [
        ("idempotency", now, 500),
        ("secret_replay", now, 500),
    ]
    assert uow.events == ["begin", "commit"]


def test_secret_replay_retention_rolls_back_and_rejects_unbounded_inputs():
    repository = _Repository(error=RuntimeError("safe synthetic failure"))
    uow = _UnitOfWork()
    use_case = PurgeExpiredPublicSecretReplaysUseCase(
        repository=repository,
        uow=uow,
    )

    with pytest.raises(RuntimeError):
        use_case.execute(now=datetime.now(timezone.utc), limit=1000)
    assert uow.events == ["begin", "rollback"]

    with pytest.raises(ValueError):
        use_case.execute(now=datetime.now(timezone.utc), limit=1001)


class _RetentionUseCase:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.calls = []

    def execute(self, *, now, limit):
        self.calls.append((now, limit))
        return next(self.batches)


def test_retention_task_drains_saturated_batches_until_both_quotas_are_underfilled():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    use_case = _RetentionUseCase(
        [
            PublicReplayRetentionBatch(500, 3),
            PublicReplayRetentionBatch(2, 0),
        ]
    )

    result = _drain_expired_public_replays(
        use_case,
        now=now,
        batch_limit=500,
        max_batches=20,
    )

    assert result == {
        "deleted_count": 505,
        "batch_count": 2,
        "has_more": False,
    }
    assert use_case.calls == [(now, 500), (now, 500)]


def test_retention_task_stops_at_the_bounded_batch_budget():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    use_case = _RetentionUseCase(
        [
            PublicReplayRetentionBatch(500, 500),
            PublicReplayRetentionBatch(500, 500),
        ]
    )

    result = _drain_expired_public_replays(
        use_case,
        now=now,
        batch_limit=500,
        max_batches=2,
    )

    assert result == {
        "deleted_count": 2_000,
        "batch_count": 2,
        "has_more": True,
    }


def test_secret_replay_retention_has_a_memory_owned_periodic_task():
    entry = celery_app.conf.beat_schedule["memory-secret-replay-retention"]

    assert entry == {
        "task": "memory.secret_replay_retention_purge",
        "schedule": 60.0,
        "options": {"queue": "log"},
    }
    assert celery_app.conf.task_routes["memory.*"] == {"queue": "log"}
