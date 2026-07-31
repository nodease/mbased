"""Bounded retention commands owned by the Conversation Memory domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from apps.memory.application.ports import MemoryUnitOfWorkPort


class PublicSecretReplayRetentionRepositoryPort(Protocol):
    def delete_expired_idempotency_records(
        self, *, now: datetime, limit: int
    ) -> int: ...

    def delete_expired_secret_replays(self, *, now: datetime, limit: int) -> int: ...


@dataclass(frozen=True, slots=True)
class PublicReplayRetentionBatch:
    idempotency_deleted_count: int
    secret_replay_deleted_count: int

    def __post_init__(self) -> None:
        if self.idempotency_deleted_count < 0 or self.secret_replay_deleted_count < 0:
            raise ValueError("retention delete counts must be non-negative")

    @property
    def deleted_count(self) -> int:
        return self.idempotency_deleted_count + self.secret_replay_deleted_count

    def has_more(self, *, limit: int) -> bool:
        return (
            self.idempotency_deleted_count >= limit
            or self.secret_replay_deleted_count >= limit
        )


class PurgeExpiredPublicSecretReplaysUseCase:
    def __init__(
        self,
        *,
        repository: PublicSecretReplayRetentionRepositoryPort,
        uow: MemoryUnitOfWorkPort,
    ) -> None:
        self.repository = repository
        self.uow = uow

    def execute(self, *, now: datetime, limit: int = 500) -> PublicReplayRetentionBatch:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("retention time must be timezone-aware")
        if not 1 <= limit <= 1000:
            raise ValueError("retention batch limit must be between 1 and 1000")
        self.uow.begin()
        try:
            idempotency_deleted_count = (
                self.repository.delete_expired_idempotency_records(
                    now=now,
                    limit=limit,
                )
            )
            secret_replay_deleted_count = self.repository.delete_expired_secret_replays(
                now=now,
                limit=limit,
            )
            self.uow.commit()
            return PublicReplayRetentionBatch(
                idempotency_deleted_count=idempotency_deleted_count,
                secret_replay_deleted_count=secret_replay_deleted_count,
            )
        except Exception:
            self.uow.rollback()
            raise


__all__ = [
    "PublicReplayRetentionBatch",
    "PublicSecretReplayRetentionRepositoryPort",
    "PurgeExpiredPublicSecretReplaysUseCase",
]
