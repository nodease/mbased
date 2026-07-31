from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from apps.shared.permissions import KNOWLEDGE_DOMAIN_ACTIONS


DomainSubjectType = Literal["team", "user"]


@dataclass(frozen=True)
class DomainPermissionCommand:
    actor_id: uuid.UUID
    organization_id: uuid.UUID
    subject_type: DomainSubjectType
    subject_id: uuid.UUID
    permission_action: str
    expires_at: datetime | None = None


@dataclass(frozen=True)
class DomainPermissionMutationResult:
    status: Literal["created", "updated", "deleted", "unchanged"]
    permission_id: uuid.UUID | None


@dataclass(frozen=True)
class DomainPermissionProjection:
    permission_id: uuid.UUID
    subject_type: DomainSubjectType
    subject_id: uuid.UUID
    subject_safe_label: str
    permission_action: str
    assigned_at: datetime
    expires_at: datetime | None
    is_expired: bool


class OrganizationManagerRequired(Exception):
    pass


class DomainPermissionSubjectHidden(Exception):
    pass


class DomainPermissionInputInvalid(Exception):
    pass


class DomainPermissionPersistenceFailed(Exception):
    pass


class DomainAuthorizationPort(Protocol):
    def is_organization_manager(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> bool: ...

    def effective_actions(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> set[str]: ...


class DomainPermissionRepositoryPort(Protocol):
    def list_permissions(
        self, organization_id: uuid.UUID
    ) -> list[DomainPermissionProjection]: ...

    def lock_active_subject_for_grant(
        self,
        organization_id: uuid.UUID,
        subject_type: DomainSubjectType,
        subject_id: uuid.UUID,
    ) -> str | None: ...

    def upsert(
        self, command: DomainPermissionCommand
    ) -> tuple[uuid.UUID, bool]: ...

    def revoke(self, command: DomainPermissionCommand) -> uuid.UUID | None: ...


class DomainPermissionAuditPort(Protocol):
    def record(
        self,
        *,
        command: DomainPermissionCommand,
        permission_id: uuid.UUID,
        operation: Literal["created", "updated", "deleted"],
    ) -> None: ...


class UnitOfWorkPort(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class KnowledgeDomainPermissionUseCase:
    def __init__(
        self,
        authorization: DomainAuthorizationPort,
        repository: DomainPermissionRepositoryPort,
        audit: DomainPermissionAuditPort,
        unit_of_work: UnitOfWorkPort,
    ) -> None:
        self.authorization = authorization
        self.repository = repository
        self.audit = audit
        self.unit_of_work = unit_of_work

    def capabilities(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> tuple[set[str], bool]:
        return (
            self.authorization.effective_actions(actor_id, organization_id),
            self.authorization.is_organization_manager(actor_id, organization_id),
        )

    def list_permissions(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> list[DomainPermissionProjection]:
        if not self.authorization.is_organization_manager(actor_id, organization_id):
            self.unit_of_work.rollback()
            raise OrganizationManagerRequired()
        return self.repository.list_permissions(organization_id)

    def grant(
        self, command: DomainPermissionCommand
    ) -> DomainPermissionMutationResult:
        self._authorize_and_validate(command)
        if self.repository.lock_active_subject_for_grant(
            command.organization_id,
            command.subject_type,
            command.subject_id,
        ) is None:
            self.unit_of_work.rollback()
            raise DomainPermissionSubjectHidden()

        try:
            permission_id, created = self.repository.upsert(command)
            operation: Literal["created", "updated"] = (
                "created" if created else "updated"
            )
            self.audit.record(
                command=command,
                permission_id=permission_id,
                operation=operation,
            )
            self.unit_of_work.flush()
            self.unit_of_work.commit()
        except Exception as exc:
            self.unit_of_work.rollback()
            raise DomainPermissionPersistenceFailed() from exc
        return DomainPermissionMutationResult(operation, permission_id)

    def revoke(
        self, command: DomainPermissionCommand
    ) -> DomainPermissionMutationResult:
        self._authorize_and_validate(command, require_future_expiry=False)
        try:
            permission_id = self.repository.revoke(command)
            if permission_id is None:
                self.unit_of_work.rollback()
                return DomainPermissionMutationResult("unchanged", None)
            self.audit.record(
                command=command,
                permission_id=permission_id,
                operation="deleted",
            )
            self.unit_of_work.flush()
            self.unit_of_work.commit()
        except Exception as exc:
            self.unit_of_work.rollback()
            raise DomainPermissionPersistenceFailed() from exc
        return DomainPermissionMutationResult("deleted", permission_id)

    def _authorize_and_validate(
        self,
        command: DomainPermissionCommand,
        *,
        require_future_expiry: bool = True,
    ) -> None:
        if command.permission_action not in KNOWLEDGE_DOMAIN_ACTIONS:
            self.unit_of_work.rollback()
            raise DomainPermissionInputInvalid()
        if command.subject_type not in {"team", "user"}:
            self.unit_of_work.rollback()
            raise DomainPermissionInputInvalid()
        if require_future_expiry and command.expires_at is not None:
            expires_at = command.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                self.unit_of_work.rollback()
                raise DomainPermissionInputInvalid()
        if not self.authorization.is_organization_manager(
            command.actor_id, command.organization_id
        ):
            self.unit_of_work.rollback()
            raise OrganizationManagerRequired()
