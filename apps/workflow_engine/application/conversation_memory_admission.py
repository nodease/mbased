"""Durable, content-free admission and fencing for Conversation execution."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CONVERSATION_EXECUTION_ADMISSION_RETENTION = timedelta(days=8)
MAX_ADMISSION_RETENTION_PURGE_BATCH = 500


class ConversationExecutionState(StrEnum):
    ADMITTED = "admitted"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ConversationExecutionConflictError(RuntimeError):
    code = "memory.execution_admission_conflict"


class ConversationExecutionFenceError(RuntimeError):
    code = "memory.execution_fence_conflict"


@dataclass(slots=True)
class ConversationExecutionAdmission:
    id: uuid.UUID
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    workflow_id: uuid.UUID
    app_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int
    request_fingerprint: str
    memory_contract_version: str
    mapping_version: str
    memory_policy_version: str
    storage_generation: int
    minimum_worker_capability: str
    execution_id: uuid.UUID
    state: ConversationExecutionState
    version: int
    lease_owner: str | None
    lease_generation: int
    lease_deadline: datetime | None
    attempt_id: uuid.UUID | None
    result_entry_id: uuid.UUID | None
    result_digest: str | None
    safe_failure_reason: str | None
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None


    retention_expires_at: datetime | None
    @classmethod
    def admit(
        cls,
        command: "AdmitConversationExecutionCommand",
    ) -> "ConversationExecutionAdmission":
        _validate_admit(command)
        return cls(
            id=command.admission_id,
            organization_id=command.organization_id,
            dispatch_id=command.dispatch_id,
            session_id=command.session_id,
            turn_id=command.turn_id,
            workflow_id=command.workflow_id,
            app_id=command.app_id,
            deployment_id=command.deployment_id,
            deployment_version=command.deployment_version,
            request_fingerprint=command.request_fingerprint,
            memory_contract_version=command.memory_contract_version,
            mapping_version=command.mapping_version,
            memory_policy_version=command.memory_policy_version,
            storage_generation=command.storage_generation,
            minimum_worker_capability=command.minimum_worker_capability,
            execution_id=uuid.uuid5(command.admission_id, "conversation-execution-v1"),
            state=ConversationExecutionState.ADMITTED,
            version=1,
            lease_owner=None,
            lease_generation=0,
            lease_deadline=None,
            attempt_id=None,
            result_entry_id=None,
            result_digest=None,
            safe_failure_reason=None,
            created_at=command.now,
            updated_at=command.now,
            terminal_at=None,
            retention_expires_at=None,
        )

    def ensure_same_admission(self, command: "AdmitConversationExecutionCommand") -> None:
        _validate_admit(command)
        if self.identity != _command_identity(command):
            raise ConversationExecutionConflictError()

    @property
    def identity(self) -> tuple[object, ...]:
        return (
            self.organization_id,
            self.dispatch_id,
            self.session_id,
            self.turn_id,
            self.workflow_id,
            self.app_id,
            self.deployment_id,
            self.deployment_version,
            self.request_fingerprint,
            self.memory_contract_version,
            self.mapping_version,
            self.memory_policy_version,
            self.storage_generation,
            self.minimum_worker_capability,
        )

    @property
    def terminal(self) -> bool:
        return self.state in {
            ConversationExecutionState.COMPLETED,
            ConversationExecutionState.FAILED,
            ConversationExecutionState.OUTCOME_UNKNOWN,
        }

    def claim(
        self,
        *,
        owner: str,
        attempt_id: uuid.UUID,
        lease_deadline: datetime,
        now: datetime,
    ) -> int:
        if not _SAFE_VERSION.fullmatch(owner) or lease_deadline <= now:
            raise ConversationExecutionFenceError()
        if self.terminal:
            raise ConversationExecutionConflictError()
        if (
            self.state is ConversationExecutionState.LEASED
            and self.lease_deadline is not None
            and now < self.lease_deadline
        ):
            if self.lease_owner == owner and self.attempt_id == attempt_id:
                return self.lease_generation
            raise ConversationExecutionFenceError()
        self.state = ConversationExecutionState.LEASED
        self.version += 1
        self.lease_generation += 1
        self.lease_owner = owner
        self.lease_deadline = lease_deadline
        self.attempt_id = attempt_id
        self.updated_at = now
        return self.lease_generation

    def finish(
        self,
        *,
        owner: str,
        lease_generation: int,
        outcome: Literal["completed", "failed", "outcome_unknown"],
        result_entry_id: uuid.UUID | None,
        result_digest: str | None,
        safe_failure_reason: str | None,
        now: datetime,
    ) -> bool:
        target = ConversationExecutionState(outcome)
        if target is ConversationExecutionState.COMPLETED:
            if result_entry_id is None or not _valid_sha256(result_digest):
                raise ConversationExecutionConflictError()
            if safe_failure_reason is not None:
                raise ConversationExecutionConflictError()
        elif (
            result_entry_id is not None
            or result_digest is not None
            or not isinstance(safe_failure_reason, str)
            or not _SAFE_VERSION.fullmatch(safe_failure_reason)
        ):
            raise ConversationExecutionConflictError()
        if self.terminal:
            if (
                self.state is target
                and self.lease_owner == owner
                and self.lease_generation == lease_generation
                and self.result_entry_id == result_entry_id
                and self.result_digest == result_digest
                and self.safe_failure_reason == safe_failure_reason
            ):
                if (
                    self.terminal_at is None
                    or self.retention_expires_at is None
                    or self.retention_expires_at <= self.terminal_at
                ):
                    raise ConversationExecutionConflictError()
                return True
            raise ConversationExecutionConflictError()
        self.require_fence(owner=owner, lease_generation=lease_generation, now=now)
        self.state = target
        self.version += 1
        self.result_entry_id = result_entry_id
        self.result_digest = result_digest
        self.safe_failure_reason = safe_failure_reason
        self.updated_at = now
        self.terminal_at = now

        self.retention_expires_at = now + CONVERSATION_EXECUTION_ADMISSION_RETENTION
        return False
    def require_fence(self, *, owner: str, lease_generation: int, now: datetime) -> None:
        if (
            self.state is not ConversationExecutionState.LEASED
            or self.lease_owner != owner
            or self.lease_generation != lease_generation
            or self.lease_deadline is None
            or now >= self.lease_deadline
        ):
            raise ConversationExecutionFenceError()


@dataclass(frozen=True, slots=True)
class AdmitConversationExecutionCommand:
    admission_id: uuid.UUID
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    workflow_id: uuid.UUID
    app_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int
    request_fingerprint: str
    memory_contract_version: str
    mapping_version: str
    memory_policy_version: str
    storage_generation: int
    minimum_worker_capability: str
    now: datetime


@dataclass(frozen=True, slots=True)
class AdmitConversationExecutionResult:
    admission_id: uuid.UUID
    execution_id: uuid.UUID
    state: ConversationExecutionState
    replayed: bool
    safe_failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimConversationExecutionCommand:
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    owner: str
    attempt_id: uuid.UUID
    lease_deadline: datetime
    now: datetime


@dataclass(frozen=True, slots=True)
class ClaimConversationExecutionResult:
    admission_id: uuid.UUID
    execution_id: uuid.UUID
    attempt_id: uuid.UUID
    lease_generation: int


@dataclass(frozen=True, slots=True)
class FinishConversationExecutionCommand:
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    owner: str
    lease_generation: int
    outcome: Literal["completed", "failed", "outcome_unknown"]
    result_entry_id: uuid.UUID | None
    result_digest: str | None
    safe_failure_reason: str | None
    now: datetime


@dataclass(frozen=True, slots=True)
class FinishConversationExecutionResult:
    admission_id: uuid.UUID
    execution_id: uuid.UUID
    state: ConversationExecutionState
    replayed: bool


@dataclass(frozen=True, slots=True)
class PurgeExpiredConversationExecutionAdmissionsCommand:
    now: datetime
    limit: int = MAX_ADMISSION_RETENTION_PURGE_BATCH


@dataclass(frozen=True, slots=True)
class PurgeExpiredConversationExecutionAdmissionsResult:
    deleted_count: int


class ConversationExecutionAdmissionRepositoryPort(Protocol):
    def find_by_dispatch(self, *, organization_id, dispatch_id): ...

    def lock_by_dispatch(self, *, organization_id, dispatch_id): ...

    def add(self, admission: ConversationExecutionAdmission) -> None: ...

    def save(self, admission: ConversationExecutionAdmission) -> None: ...

    def delete_expired_terminal(self, *, now: datetime, limit: int) -> int: ...


class ConversationExecutionUnitOfWorkPort(Protocol):
    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class _TransactionalUseCase:
    def __init__(self, *, repository, uow) -> None:
        self.repository = repository
        self.uow = uow

    def _execute(self, operation):
        self.uow.begin()
        try:
            result = operation()
            self.uow.commit()
            return result
        except Exception:
            self.uow.rollback()
            raise


class AdmitConversationExecutionUseCase(_TransactionalUseCase):
    def execute(
        self, command: AdmitConversationExecutionCommand
    ) -> AdmitConversationExecutionResult:
        def operation() -> AdmitConversationExecutionResult:
            existing = self.repository.find_by_dispatch(
                organization_id=command.organization_id,
                dispatch_id=command.dispatch_id,
            )
            if existing is not None:
                existing.ensure_same_admission(command)
                return AdmitConversationExecutionResult(
                    admission_id=existing.id,
                    execution_id=existing.execution_id,
                    state=existing.state,
                    replayed=True,
                    safe_failure_reason=existing.safe_failure_reason,
                )
            admission = ConversationExecutionAdmission.admit(command)
            self.repository.add(admission)
            return AdmitConversationExecutionResult(
                admission_id=admission.id,
                execution_id=admission.execution_id,
                state=admission.state,
                replayed=False,
                safe_failure_reason=None,
            )

        return self._execute(operation)


class ClaimConversationExecutionUseCase(_TransactionalUseCase):
    def execute(
        self, command: ClaimConversationExecutionCommand
    ) -> ClaimConversationExecutionResult:
        def operation() -> ClaimConversationExecutionResult:
            admission = self.repository.lock_by_dispatch(
                organization_id=command.organization_id,
                dispatch_id=command.dispatch_id,
            )
            if admission is None:
                raise ConversationExecutionConflictError()
            generation = admission.claim(
                owner=command.owner,
                attempt_id=command.attempt_id,
                lease_deadline=command.lease_deadline,
                now=command.now,
            )
            self.repository.save(admission)
            return ClaimConversationExecutionResult(
                admission_id=admission.id,
                execution_id=admission.execution_id,
                attempt_id=command.attempt_id,
                lease_generation=generation,
            )

        return self._execute(operation)


class FinishConversationExecutionUseCase(_TransactionalUseCase):
    def execute(
        self, command: FinishConversationExecutionCommand
    ) -> FinishConversationExecutionResult:
        def operation() -> FinishConversationExecutionResult:
            admission = self.repository.lock_by_dispatch(
                organization_id=command.organization_id,
                dispatch_id=command.dispatch_id,
            )
            if admission is None:
                raise ConversationExecutionConflictError()
            replayed = admission.finish(
                owner=command.owner,
                lease_generation=command.lease_generation,
                outcome=command.outcome,
                result_entry_id=command.result_entry_id,
                result_digest=command.result_digest,
                safe_failure_reason=command.safe_failure_reason,
                now=command.now,
            )
            self.repository.save(admission)
            return FinishConversationExecutionResult(
                admission_id=admission.id,
                execution_id=admission.execution_id,
                state=admission.state,
                replayed=replayed,
            )

        return self._execute(operation)


class PurgeExpiredConversationExecutionAdmissionsUseCase(_TransactionalUseCase):
    def execute(
        self,
        command: PurgeExpiredConversationExecutionAdmissionsCommand,
    ) -> PurgeExpiredConversationExecutionAdmissionsResult:
        if (
            command.now.tzinfo is None
            or command.now.utcoffset() is None
            or not 1 <= command.limit <= MAX_ADMISSION_RETENTION_PURGE_BATCH
        ):
            raise ValueError("conversation admission retention policy is invalid")

        return self._execute(
            lambda: PurgeExpiredConversationExecutionAdmissionsResult(
                deleted_count=self.repository.delete_expired_terminal(
                    now=command.now,
                    limit=command.limit,
                )
            )
        )


def _validate_admit(command: AdmitConversationExecutionCommand) -> None:
    if (
        command.deployment_version < 1
        or command.storage_generation < 1
        or not _valid_sha256(command.request_fingerprint)
        or any(
            not _SAFE_VERSION.fullmatch(value)
            for value in (
                command.memory_contract_version,
                command.mapping_version,
                command.memory_policy_version,
                command.minimum_worker_capability,
            )
        )
    ):
        raise ConversationExecutionConflictError()


def _command_identity(
    command: AdmitConversationExecutionCommand,
) -> tuple[object, ...]:
    return (
        command.organization_id,
        command.dispatch_id,
        command.session_id,
        command.turn_id,
        command.workflow_id,
        command.app_id,
        command.deployment_id,
        command.deployment_version,
        command.request_fingerprint,
        command.memory_contract_version,
        command.mapping_version,
        command.memory_policy_version,
        command.storage_generation,
        command.minimum_worker_capability,
    )


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


__all__ = [
    "AdmitConversationExecutionCommand",
    "AdmitConversationExecutionResult",
    "AdmitConversationExecutionUseCase",
    "ClaimConversationExecutionCommand",
    "ClaimConversationExecutionResult",
    "ClaimConversationExecutionUseCase",
    "CONVERSATION_EXECUTION_ADMISSION_RETENTION",
    "ConversationExecutionAdmission",
    "ConversationExecutionConflictError",
    "ConversationExecutionFenceError",
    "ConversationExecutionState",
    "FinishConversationExecutionCommand",
    "FinishConversationExecutionResult",
    "FinishConversationExecutionUseCase",
    "MAX_ADMISSION_RETENTION_PURGE_BATCH",
    "PurgeExpiredConversationExecutionAdmissionsCommand",
    "PurgeExpiredConversationExecutionAdmissionsResult",
    "PurgeExpiredConversationExecutionAdmissionsUseCase",
]
