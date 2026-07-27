from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from apps.memory.domain.conversation import (
    ConversationMemoryEntry,
    ConversationPurgeJob,
    ConversationSession,
    ConversationTurn,
    MemoryTurnDispatchJob,
)


class ConversationMemoryRepositoryPort(Protocol):
    def add_session(self, session: ConversationSession) -> None: ...

    def lock_session(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> ConversationSession | None: ...

    def save_session(self, session: ConversationSession) -> None: ...

    def find_turn_by_request(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        idempotency_key_hash: str,
    ) -> ConversationTurn | None: ...

    def add_turn(self, turn: ConversationTurn) -> None: ...

    def lock_turn(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
    ) -> ConversationTurn | None: ...

    def save_turn(self, turn: ConversationTurn) -> None: ...

    def add_entry(self, entry: ConversationMemoryEntry) -> None: ...

    def get_entry(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        entry_id: uuid.UUID,
    ) -> ConversationMemoryEntry | None: ...

    def save_entry(self, entry: ConversationMemoryEntry) -> None: ...

    def add_dispatch_job(self, job: MemoryTurnDispatchJob) -> None: ...
    def list_due_dispatch_jobs(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[MemoryTurnDispatchJob, ...]: ...

    def lock_dispatch_job(
        self,
        *,
        organization_id: uuid.UUID,
        dispatch_id: uuid.UUID,
    ) -> MemoryTurnDispatchJob | None: ...

    def save_dispatch_job(self, job: MemoryTurnDispatchJob) -> None: ...

    def add_purge_job(self, job: ConversationPurgeJob) -> None: ...


class MemoryUnitOfWorkPort(Protocol):
    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
