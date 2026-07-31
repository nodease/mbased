from __future__ import annotations

import uuid
from typing import Protocol

from .browser_access_models import (
    ActiveBrowserAccessSnapshot,
    BrowserAccessPolicy,
    BrowserAccessRevision,
    BrowserAccessRevisionCommand,
    BrowserAccessSourceSnapshot,
)


class DeploymentBrowserAccessRepository(Protocol):
    def lock_source(
        self,
        source_deployment_id: uuid.UUID,
    ) -> BrowserAccessSourceSnapshot | None: ...

    def create_revision(
        self,
        source: BrowserAccessSourceSnapshot,
        *,
        actor_id: uuid.UUID,
        policy: BrowserAccessPolicy,
        is_active: bool,
    ) -> BrowserAccessRevision: ...

    def get_active_by_slug(
        self,
        url_slug: str,
    ) -> ActiveBrowserAccessSnapshot | None: ...


class BrowserAccessActivationGuard(Protocol):
    def enforce(
        self,
        source: BrowserAccessSourceSnapshot,
        *,
        actor_id: uuid.UUID,
    ) -> None: ...


class BrowserAccessAuditRecorder(Protocol):
    def record_revision(
        self,
        command: BrowserAccessRevisionCommand,
        source: BrowserAccessSourceSnapshot,
        revision: BrowserAccessRevision,
        *,
        policy_digest: str,
    ) -> None: ...


class BrowserAccessUnitOfWork(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
