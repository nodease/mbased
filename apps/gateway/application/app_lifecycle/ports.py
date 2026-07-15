from __future__ import annotations

from typing import Protocol
from uuid import UUID

from .models import (
    ActiveResourceDeletion,
    DeleteAppCommand,
    LockedApp,
    LockedWorkflow,
)


class AppLifecycleRepositoryPort(Protocol):
    def lock_app(self, app_id: UUID) -> LockedApp | None: ...

    def lock_connected_workflows(
        self,
        app: LockedApp,
        *,
        limit: int,
    ) -> tuple[LockedWorkflow, ...]: ...

    def has_active_blocker(
        self,
        app: LockedApp,
        workflows: tuple[LockedWorkflow, ...],
    ) -> bool: ...

    def delete_active_resources(
        self,
        app: LockedApp,
        workflows: tuple[LockedWorkflow, ...],
    ) -> ActiveResourceDeletion: ...


class AppLifecycleAuthorizationPort(Protocol):
    def can_manage(self, actor_id: UUID, app: LockedApp) -> bool: ...

    def has_organization_scope(self, actor_id: UUID, app: LockedApp) -> bool: ...


class AppLifecycleAuditPort(Protocol):
    def record_delete(
        self,
        command: DeleteAppCommand,
        app: LockedApp,
        workflows: tuple[LockedWorkflow, ...],
        deletion: ActiveResourceDeletion,
    ) -> None: ...


class UnitOfWorkPort(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
