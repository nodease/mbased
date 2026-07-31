from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID


ResourceType = Literal["workflow", "llm_credential"]
GranteeType = Literal["team", "user"]
MutationOperation = Literal["upsert", "delete"]
MutationStatus = Literal["created", "updated", "deleted", "unchanged"]


@dataclass(frozen=True)
class PermissionMutationCommand:
    actor_id: UUID
    organization_id: UUID
    resource_type: ResourceType
    resource_id: UUID
    grantee_type: GranteeType
    grantee_id: UUID
    operation: MutationOperation
    auth_state: str | None
    assigned_at: datetime


@dataclass(frozen=True)
class PermissionProjection:
    permission_id: UUID
    organization_id: UUID
    resource_type: ResourceType
    resource_id: UUID
    grantee_type: GranteeType
    grantee_id: UUID
    auth_state: str
    assigned_by: UUID
    assigned_at: datetime
    options: dict[str, Any]
    flags: int

    def safe_snapshot(self) -> dict[str, Any]:
        return {
            "grantee_organization_id": self.organization_id,
            _grantee_field(self.grantee_type): self.grantee_id,
            _resource_field(self.resource_type): self.resource_id,
            "auth_state": self.auth_state,
        }

    def response_payload(self) -> dict[str, Any]:
        return {
            "id": self.permission_id,
            "grantee_organization_id": self.organization_id,
            _resource_field(self.resource_type): self.resource_id,
            _grantee_field(self.grantee_type): self.grantee_id,
            "auth_state": self.auth_state,
            "assigned_by": self.assigned_by,
            "assigned_at": self.assigned_at,
            "options": self.options,
            "flags": self.flags,
        }


@dataclass(frozen=True)
class PermissionMutationResult:
    status: MutationStatus
    permission: PermissionProjection
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class PermissionMutationNotFound(Exception):
    pass


class PermissionMutationPersistenceFailed(Exception):
    pass


class PermissionMutationRepositoryPort(Protocol):
    def mutate(
        self, command: PermissionMutationCommand
    ) -> PermissionMutationResult: ...


class PermissionMutationAuditPort(Protocol):
    def record(
        self,
        command: PermissionMutationCommand,
        result: PermissionMutationResult,
    ) -> None: ...


class UnitOfWorkPort(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ResourcePermissionMutationUseCase:
    def __init__(
        self,
        repository: PermissionMutationRepositoryPort,
        audit: PermissionMutationAuditPort,
        unit_of_work: UnitOfWorkPort,
    ) -> None:
        self.repository = repository
        self.audit = audit
        self.unit_of_work = unit_of_work

    def execute(
        self, command: PermissionMutationCommand
    ) -> PermissionMutationResult:
        try:
            result = self.repository.mutate(command)
            if result.status != "unchanged":
                self.audit.record(command, result)
                self.unit_of_work.flush()
            self.unit_of_work.commit()
            return result
        except PermissionMutationNotFound:
            self.unit_of_work.rollback()
            raise
        except Exception as exc:
            self.unit_of_work.rollback()
            raise PermissionMutationPersistenceFailed() from exc

    def execute_many(
        self, commands: list[PermissionMutationCommand]
    ) -> list[PermissionMutationResult]:
        results: list[PermissionMutationResult] = []
        try:
            for command in commands:
                result = self.repository.mutate(command)
                if result.status != "unchanged":
                    self.audit.record(command, result)
                    self.unit_of_work.flush()
                results.append(result)
            self.unit_of_work.commit()
            return results
        except PermissionMutationNotFound:
            self.unit_of_work.rollback()
            raise
        except Exception as exc:
            self.unit_of_work.rollback()
            raise PermissionMutationPersistenceFailed() from exc


def audit_target_type(command: PermissionMutationCommand) -> str:
    resource_name = {
        "workflow": "workflow",
        "llm_credential": "llm",
    }[command.resource_type]
    return f"{command.grantee_type}_{resource_name}_permission"


def _resource_field(resource_type: ResourceType) -> str:
    return {
        "workflow": "workflow_id",
        "llm_credential": "llm_credential_id",
    }[resource_type]


def _grantee_field(grantee_type: GranteeType) -> str:
    return {"team": "team_id", "user": "user_id"}[grantee_type]
