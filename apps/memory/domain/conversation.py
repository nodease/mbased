from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from apps.memory.domain.errors import (
    ActiveTurnConflictError,
    DispatchStateConflictError,
    DuplicateRequestConflictError,
    InvalidTurnTransitionError,
    SessionClosedError,
    SessionNotActiveError,
    StaleLifecycleRevisionError,
    StaleTurnVersionError,
)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_SAFE_CHANNEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_MEMORY_CONTENT_BYTES = 16_384
MAX_PURGE_RECEIPT_LIFETIME = timedelta(days=8)


class AudienceKind(StrEnum):
    PUBLIC_CHATBOT = "public_chatbot"
    AUTHENTICATED_INTERNAL_CHATBOT = "authenticated_internal_chatbot"


class SessionLifecycle(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    DELETE_PENDING = "delete_pending"
    DELETED = "deleted"
    EXPIRED = "expired"


class TurnStatus(StrEnum):
    PENDING_DISPATCH = "pending_dispatch"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EntryType(StrEnum):
    USER_TURN = "user_turn"
    ASSISTANT_TURN = "assistant_turn"
    NODE_PROJECTION = "node_projection"


class EntryLifecycle(StrEnum):
    PROVISIONAL = "provisional"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class DispatchStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    PUBLISHED = "published"
    ACKNOWLEDGED = "acknowledged"
    RECONCILE_REQUIRED = "reconcile_required"
    TERMINAL = "terminal"


class PurgeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_HOLD = "completed_with_hold"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"


def _require_sha256(value: str, field_name: str) -> None:
    if not _SHA256_HEX.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _require_safe_version(value: str, field_name: str) -> None:
    if not _SAFE_VERSION.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")


def _require_channel(value: str) -> None:
    if not _SAFE_CHANNEL.fullmatch(value):
        raise ValueError("channel is invalid")


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    idempotency_key_hash: str
    request_fingerprint: str

    def __post_init__(self) -> None:
        _require_sha256(self.idempotency_key_hash, "idempotency_key_hash")
        _require_sha256(self.request_fingerprint, "request_fingerprint")

    def ensure_replay_matches(self, request_fingerprint: str) -> None:
        _require_sha256(request_fingerprint, "request_fingerprint")
        if self.request_fingerprint != request_fingerprint:
            raise DuplicateRequestConflictError()


@dataclass(frozen=True, slots=True)
class ProtectedContent:
    ciphertext: bytes = field(repr=False)
    key_version: str
    format_version: str
    content_digest: str
    plaintext_byte_length: int

    def __post_init__(self) -> None:
        if not self.ciphertext:
            raise ValueError("ciphertext is required")
        _require_safe_version(self.key_version, "key_version")
        _require_safe_version(self.format_version, "format_version")
        _require_sha256(self.content_digest, "content_digest")
        if not 0 < self.plaintext_byte_length <= MAX_MEMORY_CONTENT_BYTES:
            raise ValueError(
                f"plaintext_byte_length must be between 1 and "
                f"{MAX_MEMORY_CONTENT_BYTES}"
            )


@dataclass(frozen=True, slots=True)
class ProtectedEntryContent:
    display: ProtectedContent | None
    model: ProtectedContent | None

    def __post_init__(self) -> None:
        if self.display is None and self.model is None:
            raise ValueError("at least one protected projection is required")


@dataclass(slots=True)
class ConversationSession:
    id: uuid.UUID
    organization_id: uuid.UUID
    app_id: uuid.UUID
    workflow_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int | None
    deployment_snapshot_hash: str | None
    mapping_version: str
    memory_policy_version: str
    memory_contract_version: str
    storage_generation: int
    audience_kind: AudienceKind
    subject_type: str | None
    subject_id: uuid.UUID | None
    lifecycle: SessionLifecycle
    lifecycle_revision: int
    content_revision: int
    active_turn_id: uuid.UUID | None
    next_turn_sequence: int
    idle_expires_at: datetime
    absolute_expires_at: datetime
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    delete_requested_at: datetime | None = None
    deleted_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        session_id: uuid.UUID,
        organization_id: uuid.UUID,
        app_id: uuid.UUID,
        workflow_id: uuid.UUID,
        deployment_id: uuid.UUID,
        deployment_version: int | None,
        deployment_snapshot_hash: str | None,
        mapping_version: str,
        memory_policy_version: str,
        memory_contract_version: str,
        storage_generation: int,
        audience_kind: AudienceKind,
        subject_type: str | None,
        subject_id: uuid.UUID | None,
        idle_expires_at: datetime,
        absolute_expires_at: datetime,
        now: datetime,
    ) -> "ConversationSession":
        if deployment_version is None and deployment_snapshot_hash is None:
            raise ValueError("deployment version or snapshot hash is required")
        if deployment_version is not None and deployment_version < 1:
            raise ValueError("deployment_version must be positive")
        if deployment_snapshot_hash is not None:
            _require_sha256(
                deployment_snapshot_hash,
                "deployment_snapshot_hash",
            )
        for value, name in (
            (mapping_version, "mapping_version"),
            (memory_policy_version, "memory_policy_version"),
            (memory_contract_version, "memory_contract_version"),
        ):
            _require_safe_version(value, name)
        if storage_generation < 1:
            raise ValueError("storage_generation must be positive")
        if idle_expires_at <= now or absolute_expires_at <= idle_expires_at:
            raise ValueError("session expiry bounds are invalid")
        if audience_kind is AudienceKind.PUBLIC_CHATBOT:
            if subject_type is not None or subject_id is not None:
                raise ValueError("public audience cannot carry subject binding")
        elif not subject_type or subject_id is None:
            raise ValueError("authenticated audience requires subject binding")
        elif not _SAFE_VERSION.fullmatch(subject_type):
            raise ValueError("subject_type is invalid")

        return cls(
            id=session_id,
            organization_id=organization_id,
            app_id=app_id,
            workflow_id=workflow_id,
            deployment_id=deployment_id,
            deployment_version=deployment_version,
            deployment_snapshot_hash=deployment_snapshot_hash,
            mapping_version=mapping_version,
            memory_policy_version=memory_policy_version,
            memory_contract_version=memory_contract_version,
            storage_generation=storage_generation,
            audience_kind=audience_kind,
            subject_type=subject_type,
            subject_id=subject_id,
            lifecycle=SessionLifecycle.ACTIVE,
            lifecycle_revision=1,
            content_revision=0,
            active_turn_id=None,
            next_turn_sequence=1,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            created_at=now,
            updated_at=now,
        )

    def claim_turn(
        self,
        *,
        turn_id: uuid.UUID,
        expected_lifecycle_revision: int,
        now: datetime,
    ) -> int:
        self._require_revision(expected_lifecycle_revision)
        self._require_active(now=now)
        if self.active_turn_id is not None:
            raise ActiveTurnConflictError()
        sequence = self.next_turn_sequence
        self.active_turn_id = turn_id
        self.next_turn_sequence += 1
        return sequence

    def release_turn(
        self,
        *,
        turn_id: uuid.UUID,
        expected_lifecycle_revision: int,
        content_changed: bool,
        now: datetime,
    ) -> None:
        self._require_revision(expected_lifecycle_revision)
        self._require_active(now=now)
        if self.active_turn_id != turn_id:
            raise ActiveTurnConflictError()
        self.active_turn_id = None
        if content_changed:
            self.content_revision += 1

    def close(
        self,
        *,
        expected_lifecycle_revision: int,
        now: datetime,
    ) -> None:
        self._require_revision(expected_lifecycle_revision)
        self._require_active()
        self.lifecycle = SessionLifecycle.CLOSED
        self.lifecycle_revision += 1
        self.active_turn_id = None
        self.closed_at = now
        self.updated_at = now

    def request_delete(
        self,
        *,
        expected_lifecycle_revision: int,
        now: datetime,
    ) -> None:
        self._require_revision(expected_lifecycle_revision)
        if self.lifecycle not in {
            SessionLifecycle.ACTIVE,
            SessionLifecycle.CLOSED,
        }:
            raise SessionNotActiveError()
        self.lifecycle = SessionLifecycle.DELETE_PENDING
        self.lifecycle_revision += 1
        self.active_turn_id = None
        self.delete_requested_at = now
        self.updated_at = now

    def mark_deleted(self, *, expected_lifecycle_revision: int, now: datetime) -> None:
        self._require_revision(expected_lifecycle_revision)
        if self.lifecycle is not SessionLifecycle.DELETE_PENDING:
            raise SessionNotActiveError()
        self.lifecycle = SessionLifecycle.DELETED
        self.lifecycle_revision += 1
        self.deleted_at = now
        self.updated_at = now

    def _require_revision(self, expected: int) -> None:
        if self.lifecycle_revision != expected:
            raise StaleLifecycleRevisionError()

    def _require_active(self, *, now: datetime | None = None) -> None:
        if self.lifecycle is SessionLifecycle.CLOSED:
            raise SessionClosedError()
        if self.lifecycle is not SessionLifecycle.ACTIVE:
            raise SessionNotActiveError()
        if now is not None and (
            now >= self.idle_expires_at or now >= self.absolute_expires_at
        ):
            raise SessionNotActiveError()


@dataclass(slots=True)
class ConversationTurn:
    id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    sequence: int
    version: int
    started_lifecycle_revision: int
    request_identity: RequestIdentity
    status: TurnStatus
    user_entry_id: uuid.UUID
    dispatch_id: uuid.UUID
    assistant_entry_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    latest_attempt_id: uuid.UUID | None
    safe_failure_reason: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def start(
        cls,
        *,
        turn_id: uuid.UUID,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        sequence: int,
        started_lifecycle_revision: int,
        request_identity: RequestIdentity,
        user_entry_id: uuid.UUID,
        dispatch_id: uuid.UUID,
        now: datetime,
    ) -> "ConversationTurn":
        if sequence < 1:
            raise ValueError("sequence must be positive")
        if started_lifecycle_revision < 1:
            raise ValueError("started_lifecycle_revision must be positive")
        return cls(
            id=turn_id,
            organization_id=organization_id,
            session_id=session_id,
            sequence=sequence,
            version=1,
            started_lifecycle_revision=started_lifecycle_revision,
            request_identity=request_identity,
            status=TurnStatus.PENDING_DISPATCH,
            user_entry_id=user_entry_id,
            dispatch_id=dispatch_id,
            assistant_entry_id=None,
            execution_id=None,
            latest_attempt_id=None,
            safe_failure_reason=None,
            created_at=now,
            updated_at=now,
            started_at=None,
            completed_at=None,
        )

    @property
    def terminal(self) -> bool:
        return self.status in {
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
        }

    def mark_queued(self, *, expected_version: int, now: datetime) -> None:
        self._transition(
            expected_version=expected_version,
            allowed={TurnStatus.PENDING_DISPATCH},
            target=TurnStatus.QUEUED,
            now=now,
        )

    def mark_running(
        self,
        *,
        expected_version: int,
        execution_id: uuid.UUID,
        attempt_id: uuid.UUID,
        now: datetime,
    ) -> None:
        self._transition(
            expected_version=expected_version,
            allowed={TurnStatus.QUEUED},
            target=TurnStatus.RUNNING,
            now=now,
        )
        self.execution_id = execution_id
        self.latest_attempt_id = attempt_id
        self.started_at = now

    def complete(
        self,
        *,
        expected_version: int,
        assistant_entry_id: uuid.UUID,
        now: datetime,
    ) -> None:
        self._transition(
            expected_version=expected_version,
            allowed={TurnStatus.RUNNING},
            target=TurnStatus.COMPLETED,
            now=now,
        )
        self.assistant_entry_id = assistant_entry_id
        self.completed_at = now

    def fail(
        self,
        *,
        expected_version: int,
        safe_reason_code: str,
        now: datetime,
    ) -> None:
        _require_safe_version(safe_reason_code, "safe_reason_code")
        self._transition(
            expected_version=expected_version,
            allowed={
                TurnStatus.PENDING_DISPATCH,
                TurnStatus.QUEUED,
                TurnStatus.RUNNING,
            },
            target=TurnStatus.FAILED,
            now=now,
        )
        self.safe_failure_reason = safe_reason_code
        self.completed_at = now

    def cancel(
        self,
        *,
        expected_version: int,
        safe_reason_code: str,
        now: datetime,
    ) -> None:
        _require_safe_version(safe_reason_code, "safe_reason_code")
        self._transition(
            expected_version=expected_version,
            allowed={
                TurnStatus.PENDING_DISPATCH,
                TurnStatus.QUEUED,
                TurnStatus.RUNNING,
            },
            target=TurnStatus.CANCELLED,
            now=now,
        )
        self.safe_failure_reason = safe_reason_code
        self.completed_at = now

    def _transition(
        self,
        *,
        expected_version: int,
        allowed: set[TurnStatus],
        target: TurnStatus,
        now: datetime,
    ) -> None:
        if self.version != expected_version:
            raise StaleTurnVersionError()
        if self.status not in allowed:
            raise InvalidTurnTransitionError()
        self.status = target
        self.version += 1
        self.updated_at = now


@dataclass(slots=True)
class ConversationMemoryEntry:
    id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    sequence: int
    entry_type: EntryType
    lifecycle: EntryLifecycle
    channel: str
    producer_node_id: str | None
    content: ProtectedEntryContent | None
    content_revision: int | None
    idempotency_key_hash: str
    created_at: datetime
    updated_at: datetime
    invalidated_at: datetime | None = None
    expires_at: datetime | None = None

    @classmethod
    def provisional_user(
        cls,
        *,
        entry_id: uuid.UUID,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        sequence: int,
        channel: str,
        content: ProtectedEntryContent,
        idempotency_key_hash: str,
        now: datetime,
    ) -> "ConversationMemoryEntry":
        _require_channel(channel)
        _require_sha256(idempotency_key_hash, "idempotency_key_hash")
        return cls(
            id=entry_id,
            organization_id=organization_id,
            session_id=session_id,
            turn_id=turn_id,
            sequence=sequence,
            entry_type=EntryType.USER_TURN,
            lifecycle=EntryLifecycle.PROVISIONAL,
            channel=channel,
            producer_node_id=None,
            content=content,
            content_revision=None,
            idempotency_key_hash=idempotency_key_hash,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def approved_assistant(
        cls,
        *,
        entry_id: uuid.UUID,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        sequence: int,
        channel: str,
        content: ProtectedEntryContent,
        content_revision: int,
        idempotency_key_hash: str,
        now: datetime,
    ) -> "ConversationMemoryEntry":
        _require_channel(channel)
        _require_sha256(idempotency_key_hash, "idempotency_key_hash")
        if content_revision < 1:
            raise ValueError("content_revision must be positive")
        return cls(
            id=entry_id,
            organization_id=organization_id,
            session_id=session_id,
            turn_id=turn_id,
            sequence=sequence,
            entry_type=EntryType.ASSISTANT_TURN,
            lifecycle=EntryLifecycle.APPROVED,
            channel=channel,
            producer_node_id=None,
            content=content,
            content_revision=content_revision,
            idempotency_key_hash=idempotency_key_hash,
            created_at=now,
            updated_at=now,
        )

    def approve(self, *, content_revision: int, now: datetime) -> None:
        if self.lifecycle is not EntryLifecycle.PROVISIONAL:
            raise InvalidTurnTransitionError()
        if content_revision < 1:
            raise ValueError("content_revision must be positive")
        self.lifecycle = EntryLifecycle.APPROVED
        self.content_revision = content_revision
        self.updated_at = now

    def reject(self, *, now: datetime) -> None:
        if self.lifecycle is not EntryLifecycle.PROVISIONAL:
            raise InvalidTurnTransitionError()
        self.lifecycle = EntryLifecycle.REJECTED
        self.content_revision = None
        self.updated_at = now


@dataclass(slots=True)
class MemoryTurnDispatchJob:
    id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    memory_contract_version: str
    storage_generation: int
    minimum_worker_capability: str
    status: DispatchStatus
    claim_generation: int
    claim_owner: str | None
    claim_deadline_at: datetime | None
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    broker_message_id: str | None
    workflow_admission_reference: str | None
    safe_failure_reason: str | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None
    acknowledged_at: datetime | None = None
    terminal_at: datetime | None = None

    @classmethod
    def pending(
        cls,
        *,
        dispatch_id: uuid.UUID,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        memory_contract_version: str,
        storage_generation: int,
        minimum_worker_capability: str,
        max_attempts: int,
        now: datetime,
    ) -> "MemoryTurnDispatchJob":
        _require_safe_version(memory_contract_version, "memory_contract_version")
        _require_safe_version(
            minimum_worker_capability,
            "minimum_worker_capability",
        )
        if storage_generation < 1 or max_attempts < 1:
            raise ValueError("dispatch generation and max_attempts must be positive")
        return cls(
            id=dispatch_id,
            organization_id=organization_id,
            session_id=session_id,
            turn_id=turn_id,
            memory_contract_version=memory_contract_version,
            storage_generation=storage_generation,
            minimum_worker_capability=minimum_worker_capability,
            status=DispatchStatus.PENDING,
            claim_generation=0,
            claim_owner=None,
            claim_deadline_at=None,
            attempt_count=0,
            max_attempts=max_attempts,
            next_attempt_at=None,
            broker_message_id=None,
            workflow_admission_reference=None,
            safe_failure_reason=None,
            created_at=now,
            updated_at=now,
        )

    def claim(self, *, owner: str, deadline: datetime, now: datetime) -> int:
        _require_safe_version(owner, "owner")
        if self.status not in {
            DispatchStatus.PENDING,
            DispatchStatus.RECONCILE_REQUIRED,
        }:
            raise DispatchStateConflictError()
        if self.next_attempt_at is not None and now < self.next_attempt_at:
            raise DispatchStateConflictError()
        if deadline <= now or self.attempt_count >= self.max_attempts:
            raise DispatchStateConflictError()
        self.claim_generation += 1
        self.attempt_count += 1
        self.claim_owner = owner
        self.claim_deadline_at = deadline
        self.next_attempt_at = None
        self.safe_failure_reason = None
        self.status = DispatchStatus.CLAIMED
        self.updated_at = now
        return self.claim_generation

    def recover_expired_claim(
        self,
        *,
        now: datetime,
        retry_at: datetime | None,
        safe_reason_code: str,
    ) -> None:
        _require_safe_version(safe_reason_code, "safe_reason_code")
        if (
            self.status is not DispatchStatus.CLAIMED
            or self.claim_deadline_at is None
            or now < self.claim_deadline_at
        ):
            raise DispatchStateConflictError()
        terminal = self.attempt_count >= self.max_attempts
        if terminal and retry_at is not None:
            raise ValueError("terminal dispatch cannot schedule another retry")
        if not terminal and (retry_at is None or retry_at <= now):
            raise ValueError("retry_at must be later than recovery time")

        self.claim_owner = None
        self.claim_deadline_at = None
        self.safe_failure_reason = safe_reason_code
        self.updated_at = now
        if terminal:
            self.status = DispatchStatus.TERMINAL
            self.next_attempt_at = None
            self.terminal_at = now
            return
        self.status = DispatchStatus.RECONCILE_REQUIRED
        self.next_attempt_at = retry_at

    def mark_published(
        self,
        *,
        owner: str,
        claim_generation: int,
        broker_message_id: str,
        now: datetime,
    ) -> None:
        self._require_claim(owner=owner, claim_generation=claim_generation)
        if not broker_message_id or len(broker_message_id) > 255:
            raise ValueError("broker_message_id is invalid")
        self.broker_message_id = broker_message_id
        self.status = DispatchStatus.PUBLISHED
        self.claim_owner = None
        self.claim_deadline_at = None
        self.next_attempt_at = None
        self.published_at = now
        self.updated_at = now

    def _require_claim(self, *, owner: str, claim_generation: int) -> None:
        if (
            self.status is not DispatchStatus.CLAIMED
            or self.claim_owner != owner
            or self.claim_generation != claim_generation
        ):
            raise DispatchStateConflictError()


@dataclass(slots=True)
class ConversationPurgeJob:
    id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID | None
    session_reference_digest: str
    app_id: uuid.UUID | None
    deployment_id: uuid.UUID | None
    deployment_version: int | None
    audience_kind: AudienceKind | None
    receipt_verifier_hash: str
    receipt_verifier_key_version: str
    receipt_expires_at: datetime
    status: PurgeStatus
    claim_generation: int
    attempt_count: int
    max_attempts: int
    safe_failure_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def pending(
        cls,
        *,
        purge_job_id: uuid.UUID,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        session_reference_digest: str,
        app_id: uuid.UUID,
        deployment_id: uuid.UUID,
        deployment_version: int | None,
        audience_kind: AudienceKind | str,
        receipt_verifier_hash: str,
        receipt_verifier_key_version: str,
        receipt_expires_at: datetime,
        max_attempts: int,
        now: datetime,
    ) -> "ConversationPurgeJob":
        _require_sha256(session_reference_digest, "session_reference_digest")
        _require_sha256(receipt_verifier_hash, "receipt_verifier_hash")
        _require_safe_version(
            receipt_verifier_key_version,
            "receipt_verifier_key_version",
        )
        canonical_audience = AudienceKind(audience_kind)
        if (
            (deployment_version is not None and deployment_version < 1)
            or (
                canonical_audience is AudienceKind.PUBLIC_CHATBOT
                and deployment_version is None
            )
            or receipt_expires_at <= now
            or receipt_expires_at > now + MAX_PURGE_RECEIPT_LIFETIME
            or max_attempts < 1
        ):
            raise ValueError("purge receipt or attempt bounds are invalid")
        return cls(
            id=purge_job_id,
            organization_id=organization_id,
            session_id=session_id,
            session_reference_digest=session_reference_digest,
            app_id=app_id,
            deployment_id=deployment_id,
            deployment_version=deployment_version,
            audience_kind=canonical_audience,
            receipt_verifier_hash=receipt_verifier_hash,
            receipt_verifier_key_version=receipt_verifier_key_version,
            receipt_expires_at=receipt_expires_at,
            status=PurgeStatus.PENDING,
            claim_generation=0,
            attempt_count=0,
            max_attempts=max_attempts,
            safe_failure_reason=None,
            created_at=now,
            updated_at=now,
        )
