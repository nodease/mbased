from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.memory.domain.conversation import (
    AudienceKind,
    SessionLifecycle,
    _require_safe_version,
    _require_sha256,
)
from apps.memory.domain.errors import AccessGrantNotUsableError, AccessGrantScopeError


class AccessGrantState(StrEnum):
    ACTIVE = "active"
    TRANSCRIPT_ONLY = "transcript_only"
    REVOKED = "revoked"
    EXPIRED = "expired"


class IdempotencyStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


_SAFE_OPERATION = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


@dataclass(slots=True)
class ConversationAccessGrant:
    """A persisted public bearer verifier and its immutable deployment binding.

    This object intentionally has no raw token field.  Raw bearer values only
    exist at the transport edge and in the bounded encrypted replay envelope.
    """

    id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int
    audience_kind: AudienceKind
    verifier_hash: str
    verifier_key_version: str
    state: AccessGrantState
    replay_record_reference: str | None
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    @classmethod
    def issue(
        cls,
        *,
        grant_id: uuid.UUID,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        deployment_id: uuid.UUID,
        deployment_version: int,
        audience_kind: AudienceKind | str,
        verifier_hash: str,
        verifier_key_version: str,
        expires_at: datetime,
        now: datetime,
    ) -> "ConversationAccessGrant":
        if deployment_version < 1:
            raise ValueError("deployment_version must be positive")
        _require_sha256(verifier_hash, "verifier_hash")
        _require_safe_version(verifier_key_version, "verifier_key_version")
        if expires_at <= now:
            raise ValueError("grant expiry must be later than issuance")
        return cls(
            id=grant_id,
            organization_id=organization_id,
            session_id=session_id,
            deployment_id=deployment_id,
            deployment_version=deployment_version,
            audience_kind=AudienceKind(audience_kind),
            verifier_hash=verifier_hash,
            verifier_key_version=verifier_key_version,
            state=AccessGrantState.ACTIVE,
            replay_record_reference=None,
            issued_at=now,
            expires_at=expires_at,
        )

    def require_active(
        self,
        *,
        deployment_id: uuid.UUID,
        deployment_version: int,
        audience_kind: AudienceKind | str,
        now: datetime,
    ) -> None:
        self._require_binding(
            deployment_id=deployment_id,
            deployment_version=deployment_version,
            audience_kind=audience_kind,
        )
        self._expire_if_needed(now)
        if self.state is not AccessGrantState.ACTIVE:
            raise AccessGrantNotUsableError()

    def require_transcript(
        self,
        *,
        deployment_id: uuid.UUID,
        deployment_version: int,
        audience_kind: AudienceKind | str,
        now: datetime,
    ) -> None:
        self._require_binding(
            deployment_id=deployment_id,
            deployment_version=deployment_version,
            audience_kind=audience_kind,
        )
        self._expire_if_needed(now)
        if self.state not in {
            AccessGrantState.ACTIVE,
            AccessGrantState.TRANSCRIPT_ONLY,
        }:
            raise AccessGrantNotUsableError()

    def restrict_to_transcript(self, *, now: datetime) -> None:
        self._expire_if_needed(now)
        if self.state is AccessGrantState.ACTIVE:
            self.state = AccessGrantState.TRANSCRIPT_ONLY
            return
        if self.state is not AccessGrantState.TRANSCRIPT_ONLY:
            raise AccessGrantNotUsableError()

    def revoke(self, *, now: datetime) -> None:
        if self.state is AccessGrantState.REVOKED:
            return
        if self.state is AccessGrantState.EXPIRED:
            return
        self.state = AccessGrantState.REVOKED
        self.revoked_at = now

    def _require_binding(
        self,
        *,
        deployment_id: uuid.UUID,
        deployment_version: int,
        audience_kind: AudienceKind | str,
    ) -> None:
        if (
            self.deployment_id != deployment_id
            or self.deployment_version != deployment_version
            or self.audience_kind is not AudienceKind(audience_kind)
        ):
            raise AccessGrantScopeError()

    def _expire_if_needed(self, now: datetime) -> None:
        if (
            self.state
            in {AccessGrantState.ACTIVE, AccessGrantState.TRANSCRIPT_ONLY}
            and now >= self.expires_at
        ):
            self.state = AccessGrantState.EXPIRED


@dataclass(frozen=True, slots=True)
class IdempotencyResultSnapshot:
    """Typed, content-free snapshot of the first successful public response."""

    lifecycle: SessionLifecycle
    lifecycle_revision: int
    memory_contract_version: str | None
    expires_at: datetime | None
    previous_lifecycle: SessionLifecycle | None = None
    previous_lifecycle_revision: int | None = None

    def __post_init__(self) -> None:
        if self.lifecycle_revision < 1:
            raise ValueError("result lifecycle revision must be positive")
        if self.memory_contract_version is not None:
            _require_safe_version(
                self.memory_contract_version,
                "result_memory_contract_version",
            )
        if (self.memory_contract_version is None) != (self.expires_at is None):
            raise ValueError("result contract and expiry snapshot must be complete")
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
        ):
            raise ValueError("result expiry must be timezone-aware")
        if (self.previous_lifecycle is None) != (
            self.previous_lifecycle_revision is None
        ):
            raise ValueError("previous lifecycle snapshot must be complete")
        if (
            self.previous_lifecycle_revision is not None
            and self.previous_lifecycle_revision < 1
        ):
            raise ValueError("previous lifecycle revision must be positive")


@dataclass(slots=True)
class ConversationIdempotency:
    id: uuid.UUID
    organization_id: uuid.UUID
    operation: str
    scope_digest: str
    idempotency_key_hash: str
    request_fingerprint: str
    status: IdempotencyStatus
    resource_type: str | None
    resource_reference: str | None
    replay_record_reference: str | None
    secret_replay_expires_at: datetime | None
    retention_expires_at: datetime
    safe_result_code: str | None
    result_snapshot: IdempotencyResultSnapshot | None
    authorization_app_id: uuid.UUID | None
    authorization_verifier_key_version: str | None
    authorization_verifier_hash: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def pending(
        cls,
        *,
        record_id: uuid.UUID,
        organization_id: uuid.UUID,
        operation: str,
        scope_digest: str,
        idempotency_key_hash: str,
        request_fingerprint: str,
        retention_expires_at: datetime,
        now: datetime,
        authorization_app_id: uuid.UUID | None = None,
        authorization_verifier_key_version: str | None = None,
        authorization_verifier_hash: str | None = None,
    ) -> "ConversationIdempotency":
        if not _SAFE_OPERATION.fullmatch(operation):
            raise ValueError("operation is invalid")
        for value, name in (
            (scope_digest, "scope_digest"),
            (idempotency_key_hash, "idempotency_key_hash"),
            (request_fingerprint, "request_fingerprint"),
        ):
            _require_sha256(value, name)
        if retention_expires_at <= now:
            raise ValueError("idempotency retention must be future-dated")
        authorization_values = (
            authorization_app_id,
            authorization_verifier_key_version,
            authorization_verifier_hash,
        )
        if any(value is None for value in authorization_values) and any(
            value is not None for value in authorization_values
        ):
            raise ValueError("idempotency authorization scope must be complete")
        if authorization_verifier_key_version is not None:
            _require_safe_version(
                authorization_verifier_key_version,
                "authorization_verifier_key_version",
            )
            _require_sha256(authorization_verifier_hash or "", "authorization_verifier_hash")
        return cls(
            id=record_id,
            organization_id=organization_id,
            operation=operation,
            scope_digest=scope_digest,
            idempotency_key_hash=idempotency_key_hash,
            request_fingerprint=request_fingerprint,
            status=IdempotencyStatus.PENDING,
            resource_type=None,
            resource_reference=None,
            replay_record_reference=None,
            secret_replay_expires_at=None,
            retention_expires_at=retention_expires_at,
            safe_result_code=None,
            result_snapshot=None,
            authorization_app_id=authorization_app_id,
            authorization_verifier_key_version=authorization_verifier_key_version,
            authorization_verifier_hash=authorization_verifier_hash,
            created_at=now,
            updated_at=now,
        )

    def require_matching_fingerprint(self, request_fingerprint: str) -> None:
        _require_sha256(request_fingerprint, "request_fingerprint")
        if self.request_fingerprint != request_fingerprint:
            from apps.memory.domain.errors import DuplicateRequestConflictError

            raise DuplicateRequestConflictError()

    def complete(
        self,
        *,
        resource_type: str,
        resource_reference: str,
        replay_record_reference: str | None,
        secret_replay_expires_at: datetime | None,
        safe_result_code: str,
        result_snapshot: IdempotencyResultSnapshot,
        now: datetime,
    ) -> None:
        if self.status is IdempotencyStatus.COMPLETED:
            return
        if self.status is not IdempotencyStatus.PENDING:
            raise ValueError("failed idempotency record cannot complete")
        if not resource_type or not resource_reference:
            raise ValueError("completed idempotency resource is required")
        _require_safe_version(safe_result_code, "safe_result_code")
        if secret_replay_expires_at is not None and secret_replay_expires_at <= now:
            raise ValueError("secret replay expiry must be future-dated")
        if not isinstance(result_snapshot, IdempotencyResultSnapshot):
            raise ValueError("completed idempotency result snapshot is required")
        self.status = IdempotencyStatus.COMPLETED
        self.resource_type = resource_type
        self.resource_reference = resource_reference
        self.replay_record_reference = replay_record_reference
        self.secret_replay_expires_at = secret_replay_expires_at
        self.safe_result_code = safe_result_code
        self.result_snapshot = result_snapshot
        self.updated_at = now


@dataclass(frozen=True, slots=True)
class EncryptedSecretReplay:
    id: uuid.UUID
    organization_id: uuid.UUID
    idempotency_record_id: uuid.UUID
    purpose: str
    ciphertext: bytes
    key_version: str
    associated_data_digest: str
    expires_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.ciphertext:
            raise ValueError("secret replay ciphertext is required")
        _require_safe_version(self.purpose, "purpose")
        _require_safe_version(self.key_version, "key_version")
        _require_sha256(self.associated_data_digest, "associated_data_digest")
