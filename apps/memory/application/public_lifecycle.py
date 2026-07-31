"""Framework-independent public Conversation lifecycle application service.

The public adapter owns no database or HTTP details.  It receives a canonical
deployment binding from a port, persists only verifiers/ciphertext through a
single Unit of Work, and returns raw capabilities only to its caller.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol

from apps.memory.application.ports import MemoryUnitOfWorkPort
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationPurgeJob,
    ConversationSession,
    PurgeStatus,
    SessionLifecycle,
)
from apps.memory.domain.errors import (
    AccessGrantNotUsableError,
    MemoryAdapterUnavailableError,
    PurgeReceiptNotUsableError,
    SecretReplayExpiredError,
)
from apps.memory.domain.public_access import (
    AccessGrantState,
    ConversationAccessGrant,
    ConversationIdempotency,
    EncryptedSecretReplay,
    IdempotencyResultSnapshot,
    IdempotencyStatus,
)
from apps.shared.audit.actions import AuditAction


class PublicConversationAdmissionDisposition(str, Enum):
    LOGICAL_REQUEST = "logical_request"
    EXACT_RETRY = "exact_retry"


@dataclass(frozen=True, slots=True)
class PublicAppBinding:
    organization_id: uuid.UUID
    app_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class PublicDeploymentBinding:
    organization_id: uuid.UUID
    app_id: uuid.UUID
    workflow_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int
    mapping_version: str
    memory_policy_version: str
    memory_contract_version: str
    storage_generation: int


@dataclass(frozen=True, slots=True)
class PublicConversationPolicy:
    idle_lifetime: timedelta = timedelta(hours=24)
    absolute_lifetime: timedelta = timedelta(days=7)
    access_grant_lifetime: timedelta = timedelta(days=1)
    access_secret_replay_lifetime: timedelta = timedelta(minutes=10)
    purge_receipt_lifetime: timedelta = timedelta(days=8)
    purge_secret_replay_lifetime: timedelta = timedelta(hours=24)
    idempotency_retention: timedelta = timedelta(hours=24)
    purge_max_attempts: int = 8

    def __post_init__(self) -> None:
        durations = (
            self.idle_lifetime,
            self.absolute_lifetime,
            self.access_grant_lifetime,
            self.access_secret_replay_lifetime,
            self.purge_receipt_lifetime,
            self.purge_secret_replay_lifetime,
            self.idempotency_retention,
        )
        if any(value <= timedelta(0) for value in durations):
            raise ValueError("public conversation lifetimes must be positive")
        if self.absolute_lifetime <= self.idle_lifetime:
            raise ValueError("absolute lifetime must exceed idle lifetime")
        if self.purge_receipt_lifetime != timedelta(days=8):
            raise ValueError("purge receipt lifetime must be eight days")
        if self.idempotency_retention > timedelta(hours=24):
            raise ValueError("idempotency retention must not exceed twenty-four hours")
        if self.idempotency_retention < max(
            self.access_secret_replay_lifetime,
            self.purge_secret_replay_lifetime,
        ):
            raise ValueError("idempotency retention must cover secret replay lifetimes")
        if self.purge_max_attempts < 1:
            raise ValueError("purge_max_attempts must be positive")


@dataclass(frozen=True, slots=True)
class IssuedSecret:
    raw_value: str
    verifier_hash: str
    verifier_key_version: str


@dataclass(frozen=True, slots=True)
class SecretCiphertext:
    ciphertext: bytes
    key_version: str


class PublicSecretIssuerPort(Protocol):
    def issue_access_grant(self) -> IssuedSecret: ...

    def access_grant_verifiers(self, raw_value: str) -> tuple[tuple[str, str], ...]: ...

    def issue_purge_receipt(self) -> IssuedSecret: ...

    def purge_receipt_verifiers(
        self, raw_value: str
    ) -> tuple[tuple[str, str], ...]: ...


class SecretReplayCipherPort(Protocol):
    def encrypt(
        self, raw_value: str, *, associated_data_digest: str
    ) -> SecretCiphertext: ...

    def decrypt(
        self,
        ciphertext: bytes,
        *,
        key_version: str,
        associated_data_digest: str,
    ) -> str | None: ...


class PublicConversationAuditPort(Protocol):
    def record(
        self,
        *,
        action: str,
        organization_id: uuid.UUID,
        deployment_id: uuid.UUID,
        session_id: uuid.UUID,
        target_type: str,
        target_id: uuid.UUID,
        purge_job_id: uuid.UUID | None = None,
    ) -> None: ...


class PublicConversationAdmissionPort(Protocol):
    def admit(
        self,
        *,
        operation: str,
        binding: "PublicDeploymentBinding | None",
        grant_id: uuid.UUID | None,
        network_address: str,
        request_scope_digest: str,
        request_key_hash: str,
        request_fingerprint: str,
        disposition: PublicConversationAdmissionDisposition,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    record: ConversationIdempotency
    created: bool


class PublicConversationRepositoryPort(Protocol):
    def resolve_public_app(self, url_slug: str) -> PublicAppBinding | None: ...

    def resolve_public_deployment(
        self, url_slug: str
    ) -> PublicDeploymentBinding | None: ...

    def lock_public_deployment(
        self, url_slug: str
    ) -> PublicDeploymentBinding | None: ...

    def reserve_idempotency(
        self, record: ConversationIdempotency
    ) -> IdempotencyReservation: ...

    def find_idempotency(
        self,
        *,
        organization_id: uuid.UUID,
        operation: str,
        scope_digest: str,
        idempotency_key_hash: str,
        now: datetime,
    ) -> ConversationIdempotency | None: ...

    def find_authorized_idempotency(
        self,
        *,
        organization_id: uuid.UUID,
        app_id: uuid.UUID,
        operation: str,
        idempotency_key_hash: str,
        verifier_candidates: tuple[tuple[str, str], ...],
        now: datetime,
    ) -> ConversationIdempotency | None: ...

    def save_idempotency(self, record: ConversationIdempotency) -> None: ...

    def add_session(self, session: ConversationSession) -> None: ...

    def lock_session(
        self, *, organization_id: uuid.UUID, session_id: uuid.UUID
    ) -> ConversationSession | None: ...

    def save_session(self, session: ConversationSession) -> None: ...

    def add_access_grant(self, grant: ConversationAccessGrant) -> None: ...

    def lock_access_grant(
        self, *, verifier_candidates: tuple[tuple[str, str], ...]
    ) -> ConversationAccessGrant | None: ...

    def save_access_grant(self, grant: ConversationAccessGrant) -> None: ...

    def add_secret_replay(self, replay: EncryptedSecretReplay) -> None: ...

    def get_secret_replay(
        self, *, organization_id: uuid.UUID, replay_id: uuid.UUID
    ) -> EncryptedSecretReplay | None: ...

    def add_purge_job(self, job: ConversationPurgeJob) -> None: ...

    def find_purge_job(
        self, *, verifier_candidates: tuple[tuple[str, str], ...]
    ) -> ConversationPurgeJob | None: ...

    def lock_purge_job_by_id(
        self, *, organization_id: uuid.UUID, purge_job_id: uuid.UUID
    ) -> ConversationPurgeJob | None: ...


@dataclass(frozen=True, slots=True)
class CreatePublicConversationCommand:
    url_slug: str
    idempotency_key_hash: str
    request_fingerprint: str
    now: datetime
    network_address: str = ""


@dataclass(frozen=True, slots=True)
class PublicConversationResult:
    lifecycle: SessionLifecycle
    lifecycle_revision: int
    memory_contract_version: str
    expires_at: datetime
    access_token: str
    replayed: bool
    previous_lifecycle: SessionLifecycle | None = None
    previous_lifecycle_revision: int | None = None


@dataclass(frozen=True, slots=True)
class LifecycleCommand:
    url_slug: str
    access_token: str
    idempotency_key_hash: str
    request_fingerprint: str
    expected_lifecycle_revision: int
    now: datetime
    network_address: str = ""


@dataclass(frozen=True, slots=True)
class ClosePublicConversationResult:
    lifecycle: SessionLifecycle
    lifecycle_revision: int
    memory_contract_version: str
    expires_at: datetime
    replayed: bool


@dataclass(frozen=True, slots=True)
class DeletePublicConversationResult:
    lifecycle: SessionLifecycle
    lifecycle_revision: int
    purge_job_id: uuid.UUID
    purge_receipt: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class PublicTranscriptResult:
    lifecycle: SessionLifecycle
    lifecycle_revision: int
    content_revision: int
    expires_at: datetime
    turns: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class PublicPurgeStatusResult:
    status: PurgeStatus
    updated_at: datetime
    safe_failure_reason: str | None


class _TransactionalPublicUseCase:
    def __init__(
        self,
        *,
        repository: PublicConversationRepositoryPort,
        uow: MemoryUnitOfWorkPort,
        secrets: PublicSecretIssuerPort,
        replay_cipher: SecretReplayCipherPort,
        audit: PublicConversationAuditPort,
        policy: PublicConversationPolicy,
        admission: PublicConversationAdmissionPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.uow = uow
        self.secrets = secrets
        self.replay_cipher = replay_cipher
        self.audit = audit
        self.policy = policy
        self.admission = admission
        self.clock = clock or _utc_now

    def _execute(self, operation):
        self.uow.begin()
        try:
            result = operation()
            self.uow.commit()
            return result
        except Exception:
            self.uow.rollback()
            raise

    def _current_time(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise MemoryAdapterUnavailableError()
        return now

    def _binding(
        self,
        url_slug: str,
        *,
        for_update: bool = False,
    ) -> PublicDeploymentBinding:
        resolver = (
            self.repository.lock_public_deployment
            if for_update
            else self.repository.resolve_public_deployment
        )
        binding = resolver(url_slug)
        if binding is None:
            raise AccessGrantNotUsableError()
        return binding

    def _app_binding(self, url_slug: str) -> PublicAppBinding:
        binding = self.repository.resolve_public_app(url_slug)
        if binding is None:
            raise AccessGrantNotUsableError()
        return binding

    def _grant_for_mutation(
        self,
        *,
        binding: PublicDeploymentBinding,
        raw_access_token: str,
        now: datetime,
        allow_expired_for_replay: bool = False,
    ) -> ConversationAccessGrant:
        verifiers = self.secrets.access_grant_verifiers(raw_access_token)
        if not verifiers:
            raise AccessGrantNotUsableError()
        grant = self.repository.lock_access_grant(
            verifier_candidates=verifiers,
        )
        if grant is None:
            raise AccessGrantNotUsableError()
        verifier_hash = next(
            (
                candidate_hash
                for version, candidate_hash in verifiers
                if version == grant.verifier_key_version
            ),
            None,
        )
        if verifier_hash is None or not hmac.compare_digest(
            grant.verifier_hash, verifier_hash
        ):
            raise AccessGrantNotUsableError()
        if (
            grant.deployment_id != binding.deployment_id
            or grant.deployment_version != binding.deployment_version
            or grant.audience_kind is not AudienceKind.PUBLIC_CHATBOT
        ):
            raise AccessGrantNotUsableError()
        if not allow_expired_for_replay and (
            grant.state is AccessGrantState.EXPIRED or now >= grant.expires_at
        ):
            raise AccessGrantNotUsableError()
        return grant

    def _session_for_grant(
        self,
        grant: ConversationAccessGrant,
        *,
        now: datetime,
        allow_expired_for_replay: bool = False,
    ) -> ConversationSession:
        session = self.repository.lock_session(
            organization_id=grant.organization_id,
            session_id=grant.session_id,
        )
        if session is None:
            raise AccessGrantNotUsableError()
        if (
            session.deployment_id != grant.deployment_id
            or session.deployment_version != grant.deployment_version
            or session.audience_kind is not AudienceKind.PUBLIC_CHATBOT
        ):
            raise AccessGrantNotUsableError()
        if not allow_expired_for_replay and (
            now >= session.idle_expires_at or now >= session.absolute_expires_at
        ):
            raise AccessGrantNotUsableError()
        return session

    @staticmethod
    def _require_unexpired_session(
        session: ConversationSession,
        *,
        now: datetime,
    ) -> None:
        if now >= session.idle_expires_at or now >= session.absolute_expires_at:
            raise AccessGrantNotUsableError()

    def _reserve(
        self,
        *,
        organization_id: uuid.UUID,
        operation: str,
        scope_digest: str,
        idempotency_key_hash: str,
        request_fingerprint: str,
        now: datetime,
        authorization_app_id: uuid.UUID | None = None,
        authorization_verifier_key_version: str | None = None,
        authorization_verifier_hash: str | None = None,
    ) -> IdempotencyReservation:
        return self.repository.reserve_idempotency(
            ConversationIdempotency.pending(
                record_id=uuid.uuid4(),
                organization_id=organization_id,
                operation=operation,
                scope_digest=scope_digest,
                idempotency_key_hash=idempotency_key_hash,
                request_fingerprint=request_fingerprint,
                retention_expires_at=now + self.policy.idempotency_retention,
                now=now,
                authorization_app_id=authorization_app_id,
                authorization_verifier_key_version=authorization_verifier_key_version,
                authorization_verifier_hash=authorization_verifier_hash,
            )
        )

    def _add_secret_replay(
        self,
        *,
        record: ConversationIdempotency,
        purpose: str,
        raw_secret: str,
        expires_at: datetime,
    ) -> EncryptedSecretReplay:
        associated_data_digest = _secret_replay_associated_data_digest(
            record=record,
            purpose=purpose,
        )
        ciphertext = self.replay_cipher.encrypt(
            raw_secret,
            associated_data_digest=associated_data_digest,
        )
        replay = EncryptedSecretReplay(
            id=uuid.uuid4(),
            organization_id=record.organization_id,
            idempotency_record_id=record.id,
            purpose=purpose,
            ciphertext=ciphertext.ciphertext,
            key_version=ciphertext.key_version,
            associated_data_digest=associated_data_digest,
            expires_at=expires_at,
            created_at=record.created_at,
        )
        self.repository.add_secret_replay(replay)
        return replay

    def _admit(
        self,
        *,
        operation: str,
        binding: PublicDeploymentBinding | None,
        grant_id: uuid.UUID | None,
        network_address: str,
        request_scope_digest: str,
        request_key_hash: str,
        request_fingerprint: str,
        disposition: PublicConversationAdmissionDisposition,
    ) -> None:
        if self.admission is not None:
            self.admission.admit(
                operation=operation,
                binding=binding,
                grant_id=grant_id,
                network_address=network_address,
                request_scope_digest=request_scope_digest,
                request_key_hash=request_key_hash,
                request_fingerprint=request_fingerprint,
                disposition=disposition,
            )

    @staticmethod
    def _require_admitted_binding(
        current: PublicDeploymentBinding,
        admitted: PublicDeploymentBinding,
    ) -> None:
        if current != admitted:
            raise AccessGrantNotUsableError()

    def _existing_idempotency(
        self,
        *,
        organization_id: uuid.UUID,
        operation: str,
        scope_digest: str,
        idempotency_key_hash: str,
        request_fingerprint: str,
        now: datetime,
    ) -> ConversationIdempotency | None:
        record = self.repository.find_idempotency(
            organization_id=organization_id,
            operation=operation,
            scope_digest=scope_digest,
            idempotency_key_hash=idempotency_key_hash,
            now=now,
        )
        if record is not None:
            record.require_matching_fingerprint(request_fingerprint)
        return record

    def _lifecycle_preflight(
        self,
        *,
        command: LifecycleCommand,
        operation: str,
        require_transcript: bool,
    ) -> tuple[
        PublicDeploymentBinding,
        uuid.UUID,
        str,
        PublicConversationAdmissionDisposition,
    ]:
        binding = self._binding(command.url_slug)
        grant = self._grant_for_mutation(
            binding=binding,
            raw_access_token=command.access_token,
            now=command.now,
            allow_expired_for_replay=True,
        )
        session = self._session_for_grant(
            grant,
            now=command.now,
            allow_expired_for_replay=True,
        )
        scope_digest = _scope_digest(
            operation=operation,
            binding=binding,
            grant=grant,
            session=session,
        )
        existing = self._existing_idempotency(
            organization_id=binding.organization_id,
            operation=operation,
            scope_digest=scope_digest,
            idempotency_key_hash=command.idempotency_key_hash,
            request_fingerprint=command.request_fingerprint,
            now=command.now,
        )
        if existing is None:
            if require_transcript:
                grant.require_transcript(
                    deployment_id=binding.deployment_id,
                    deployment_version=binding.deployment_version,
                    audience_kind=AudienceKind.PUBLIC_CHATBOT,
                    now=command.now,
                )
            else:
                grant.require_active(
                    deployment_id=binding.deployment_id,
                    deployment_version=binding.deployment_version,
                    audience_kind=AudienceKind.PUBLIC_CHATBOT,
                    now=command.now,
                )
            self._require_unexpired_session(session, now=command.now)
        disposition = (
            PublicConversationAdmissionDisposition.LOGICAL_REQUEST
            if existing is None
            else PublicConversationAdmissionDisposition.EXACT_RETRY
        )
        return binding, grant.id, scope_digest, disposition

    def _replay_secret(
        self,
        *,
        record: ConversationIdempotency,
        purpose: str,
        now: datetime,
    ) -> str:
        if (
            record.status is not IdempotencyStatus.COMPLETED
            or record.replay_record_reference is None
            or record.secret_replay_expires_at is None
        ):
            raise SecretReplayExpiredError()
        try:
            replay_id = uuid.UUID(record.replay_record_reference)
        except ValueError:
            raise MemoryAdapterUnavailableError() from None
        replay = self.repository.get_secret_replay(
            organization_id=record.organization_id,
            replay_id=replay_id,
        )
        # A purge/replay row lock can wait past either replay boundary.  Never
        # let the request-start timestamp extend a parent or child TTL.
        replay_now = max(now, self._current_time())
        if (
            replay is None
            or replay.idempotency_record_id != record.id
            or replay.purpose != purpose
            or replay_now >= record.retention_expires_at
            or replay_now >= record.secret_replay_expires_at
            or replay_now >= replay.expires_at
        ):
            raise SecretReplayExpiredError()
        expected_associated_data_digest = _secret_replay_associated_data_digest(
            record=record,
            purpose=purpose,
        )
        if not hmac.compare_digest(
            replay.associated_data_digest,
            expected_associated_data_digest,
        ):
            raise MemoryAdapterUnavailableError()
        raw_secret = self.replay_cipher.decrypt(
            replay.ciphertext,
            key_version=replay.key_version,
            associated_data_digest=expected_associated_data_digest,
        )
        if raw_secret is None:
            raise MemoryAdapterUnavailableError()
        return raw_secret

    def _authorized_delete_replay(
        self,
        command: LifecycleCommand,
    ) -> tuple[DeletePublicConversationResult, str] | None:
        app_binding = self._app_binding(command.url_slug)
        verifiers = self.secrets.access_grant_verifiers(command.access_token)
        if not verifiers:
            return None
        record = self.repository.find_authorized_idempotency(
            organization_id=app_binding.organization_id,
            app_id=app_binding.app_id,
            operation="conversation.delete",
            idempotency_key_hash=command.idempotency_key_hash,
            verifier_candidates=verifiers,
            now=command.now,
        )
        if record is None:
            return None
        if not hmac.compare_digest(
            record.request_fingerprint, command.request_fingerprint
        ):
            raise AccessGrantNotUsableError()
        result_snapshot = _record_result_snapshot(
            record,
            expected_resource_type="conversation_purge_job",
        )
        purge_job = _record_purge_job(self.repository, record)
        if (
            purge_job.organization_id != app_binding.organization_id
            or purge_job.app_id != app_binding.app_id
            or purge_job.audience_kind is not AudienceKind.PUBLIC_CHATBOT
        ):
            raise AccessGrantNotUsableError()
        receipt = self._replay_secret(
            record=record,
            purpose="purge_receipt",
            now=command.now,
        )
        result = DeletePublicConversationResult(
            lifecycle=result_snapshot.lifecycle,
            lifecycle_revision=result_snapshot.lifecycle_revision,
            purge_job_id=purge_job.id,
            purge_receipt=receipt,
            replayed=True,
        )
        return result, record.scope_digest


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CreatePublicConversationUseCase(_TransactionalPublicUseCase):
    def execute(
        self, command: CreatePublicConversationCommand
    ) -> PublicConversationResult:
        def preflight() -> tuple[
            PublicDeploymentBinding,
            str,
            PublicConversationAdmissionDisposition,
        ]:
            binding = self._binding(command.url_slug)
            scope_digest = _scope_digest(
                operation="conversation.create",
                binding=binding,
            )
            existing = self._existing_idempotency(
                organization_id=binding.organization_id,
                operation="conversation.create",
                scope_digest=scope_digest,
                idempotency_key_hash=command.idempotency_key_hash,
                request_fingerprint=command.request_fingerprint,
                now=command.now,
            )
            disposition = (
                PublicConversationAdmissionDisposition.LOGICAL_REQUEST
                if existing is None
                else PublicConversationAdmissionDisposition.EXACT_RETRY
            )
            return binding, scope_digest, disposition

        admission_binding, admission_scope_digest, disposition = self._execute(
            preflight
        )
        self._admit(
            operation="conversation.create",
            binding=admission_binding,
            grant_id=None,
            network_address=command.network_address,
            request_scope_digest=admission_scope_digest,
            request_key_hash=command.idempotency_key_hash,
            request_fingerprint=command.request_fingerprint,
            disposition=disposition,
        )

        def operation() -> PublicConversationResult:
            binding = self._binding(command.url_slug, for_update=True)
            self._require_admitted_binding(binding, admission_binding)
            now = self._current_time()
            scope_digest = _scope_digest(
                operation="conversation.create",
                binding=binding,
            )
            reservation = self._reserve(
                organization_id=binding.organization_id,
                operation="conversation.create",
                scope_digest=scope_digest,
                idempotency_key_hash=command.idempotency_key_hash,
                request_fingerprint=command.request_fingerprint,
                now=now,
            )
            record = reservation.record
            if not reservation.created:
                record.require_matching_fingerprint(command.request_fingerprint)
                return self._replay_create(record=record, now=now)

            session = _new_session(
                binding=binding,
                policy=self.policy,
                now=now,
            )
            issued = self.secrets.issue_access_grant()
            grant = ConversationAccessGrant.issue(
                grant_id=uuid.uuid4(),
                organization_id=binding.organization_id,
                session_id=session.id,
                deployment_id=binding.deployment_id,
                deployment_version=binding.deployment_version,
                audience_kind=AudienceKind.PUBLIC_CHATBOT,
                verifier_hash=issued.verifier_hash,
                verifier_key_version=issued.verifier_key_version,
                expires_at=min(
                    session.absolute_expires_at,
                    now + self.policy.access_grant_lifetime,
                ),
                now=now,
            )
            replay = self._add_secret_replay(
                record=record,
                purpose="access_grant",
                raw_secret=issued.raw_value,
                expires_at=now + self.policy.access_secret_replay_lifetime,
            )
            grant.replay_record_reference = str(replay.id)
            record.complete(
                resource_type="conversation_session",
                resource_reference=str(session.id),
                replay_record_reference=str(replay.id),
                secret_replay_expires_at=replay.expires_at,
                safe_result_code="created",
                result_snapshot=_session_result_snapshot(session, grant=grant),
                now=now,
            )
            self.repository.add_session(session)
            self.repository.add_access_grant(grant)
            self.repository.save_idempotency(record)
            self.audit.record(
                action=AuditAction.MEMORY_SESSION_CREATED,
                organization_id=binding.organization_id,
                deployment_id=binding.deployment_id,
                session_id=session.id,
                target_type="conversation_session",
                target_id=session.id,
            )
            self.audit.record(
                action=AuditAction.MEMORY_GRANT_ISSUED,
                organization_id=binding.organization_id,
                deployment_id=binding.deployment_id,
                session_id=session.id,
                target_type="conversation_access_grant",
                target_id=grant.id,
            )
            return _conversation_result(
                session=session,
                grant=grant,
                access_token=issued.raw_value,
                replayed=False,
            )

        return self._execute(operation)

    def _replay_create(
        self,
        *,
        record: ConversationIdempotency,
        now: datetime,
    ) -> PublicConversationResult:
        access_token = self._replay_secret(
            record=record,
            purpose="access_grant",
            now=now,
        )
        return _conversation_result_from_snapshot(
            record=record,
            access_token=access_token,
            replayed=True,
        )


class ClosePublicConversationUseCase(_TransactionalPublicUseCase):
    def execute(self, command: LifecycleCommand) -> ClosePublicConversationResult:
        admission_binding, admission_grant_id, admission_scope_digest, disposition = (
            self._execute(
                lambda: self._lifecycle_preflight(
                    command=command,
                    operation="conversation.close",
                    require_transcript=False,
                )
            )
        )
        self._admit(
            operation="conversation.close",
            binding=admission_binding,
            grant_id=admission_grant_id,
            network_address=command.network_address,
            request_scope_digest=admission_scope_digest,
            request_key_hash=command.idempotency_key_hash,
            request_fingerprint=command.request_fingerprint,
            disposition=disposition,
        )

        def operation() -> ClosePublicConversationResult:
            binding = self._binding(command.url_slug, for_update=True)
            self._require_admitted_binding(binding, admission_binding)
            grant = self._grant_for_mutation(
                binding=binding,
                raw_access_token=command.access_token,
                now=command.now,
                allow_expired_for_replay=True,
            )
            session = self._session_for_grant(
                grant,
                now=command.now,
                allow_expired_for_replay=True,
            )
            # Expiry is authoritative only after every mutation row lock is held.
            now = self._current_time()
            reservation = self._reserve(
                organization_id=binding.organization_id,
                operation="conversation.close",
                scope_digest=_scope_digest(
                    operation="conversation.close",
                    binding=binding,
                    grant=grant,
                    session=session,
                ),
                idempotency_key_hash=command.idempotency_key_hash,
                request_fingerprint=command.request_fingerprint,
                now=now,
            )
            if not reservation.created:
                reservation.record.require_matching_fingerprint(
                    command.request_fingerprint
                )
                return _close_result_from_snapshot(reservation.record, replayed=True)

            grant.require_active(
                deployment_id=binding.deployment_id,
                deployment_version=binding.deployment_version,
                audience_kind=AudienceKind.PUBLIC_CHATBOT,
                now=now,
            )
            self._require_unexpired_session(session, now=now)
            session.close(
                expected_lifecycle_revision=command.expected_lifecycle_revision,
                now=now,
            )
            grant.restrict_to_transcript(now=now)
            reservation.record.complete(
                resource_type="conversation_session",
                resource_reference=str(session.id),
                replay_record_reference=None,
                secret_replay_expires_at=None,
                safe_result_code="closed",
                result_snapshot=_session_result_snapshot(session, grant=grant),
                now=now,
            )
            self.repository.save_session(session)
            self.repository.save_access_grant(grant)
            self.repository.save_idempotency(reservation.record)
            self.audit.record(
                action=AuditAction.MEMORY_SESSION_CLOSED,
                organization_id=binding.organization_id,
                deployment_id=binding.deployment_id,
                session_id=session.id,
                target_type="conversation_session",
                target_id=session.id,
            )
            return _close_result(session, grant=grant, replayed=False)

        return self._execute(operation)


class ResetPublicConversationUseCase(_TransactionalPublicUseCase):
    def execute(self, command: LifecycleCommand) -> PublicConversationResult:
        admission_binding, admission_grant_id, admission_scope_digest, disposition = (
            self._execute(
                lambda: self._lifecycle_preflight(
                    command=command,
                    operation="conversation.reset",
                    require_transcript=False,
                )
            )
        )
        self._admit(
            operation="conversation.reset",
            binding=admission_binding,
            grant_id=admission_grant_id,
            network_address=command.network_address,
            request_scope_digest=admission_scope_digest,
            request_key_hash=command.idempotency_key_hash,
            request_fingerprint=command.request_fingerprint,
            disposition=disposition,
        )

        def operation() -> PublicConversationResult:
            binding = self._binding(command.url_slug, for_update=True)
            self._require_admitted_binding(binding, admission_binding)
            old_grant = self._grant_for_mutation(
                binding=binding,
                raw_access_token=command.access_token,
                now=command.now,
                allow_expired_for_replay=True,
            )
            old_session = self._session_for_grant(
                old_grant,
                now=command.now,
                allow_expired_for_replay=True,
            )
            # Expiry is authoritative only after every mutation row lock is held.
            now = self._current_time()
            reservation = self._reserve(
                organization_id=binding.organization_id,
                operation="conversation.reset",
                scope_digest=_scope_digest(
                    operation="conversation.reset",
                    binding=binding,
                    grant=old_grant,
                    session=old_session,
                ),
                idempotency_key_hash=command.idempotency_key_hash,
                request_fingerprint=command.request_fingerprint,
                now=now,
            )
            if not reservation.created:
                reservation.record.require_matching_fingerprint(
                    command.request_fingerprint
                )
                token = self._replay_secret(
                    record=reservation.record,
                    purpose="access_grant",
                    now=now,
                )
                return _conversation_result_from_snapshot(
                    record=reservation.record,
                    access_token=token,
                    replayed=True,
                )

            old_grant.require_active(
                deployment_id=binding.deployment_id,
                deployment_version=binding.deployment_version,
                audience_kind=AudienceKind.PUBLIC_CHATBOT,
                now=now,
            )
            self._require_unexpired_session(old_session, now=now)
            old_session.close(
                expected_lifecycle_revision=command.expected_lifecycle_revision,
                now=now,
            )
            old_grant.revoke(now=now)
            new_session = _new_session(
                binding=binding,
                policy=self.policy,
                now=now,
            )
            issued = self.secrets.issue_access_grant()
            new_grant = ConversationAccessGrant.issue(
                grant_id=uuid.uuid4(),
                organization_id=binding.organization_id,
                session_id=new_session.id,
                deployment_id=binding.deployment_id,
                deployment_version=binding.deployment_version,
                audience_kind=AudienceKind.PUBLIC_CHATBOT,
                verifier_hash=issued.verifier_hash,
                verifier_key_version=issued.verifier_key_version,
                expires_at=min(
                    new_session.absolute_expires_at,
                    now + self.policy.access_grant_lifetime,
                ),
                now=now,
            )
            replay = self._add_secret_replay(
                record=reservation.record,
                purpose="access_grant",
                raw_secret=issued.raw_value,
                expires_at=now + self.policy.access_secret_replay_lifetime,
            )
            new_grant.replay_record_reference = str(replay.id)
            reservation.record.complete(
                resource_type="conversation_session",
                resource_reference=str(new_session.id),
                replay_record_reference=str(replay.id),
                secret_replay_expires_at=replay.expires_at,
                safe_result_code="reset",
                result_snapshot=_session_result_snapshot(
                    new_session,
                    grant=new_grant,
                    previous_lifecycle=old_session.lifecycle,
                    previous_lifecycle_revision=old_session.lifecycle_revision,
                ),
                now=now,
            )
            self.repository.save_session(old_session)
            self.repository.save_access_grant(old_grant)
            self.repository.add_session(new_session)
            self.repository.add_access_grant(new_grant)
            self.repository.save_idempotency(reservation.record)
            self.audit.record(
                action=AuditAction.MEMORY_SESSION_RESET,
                organization_id=binding.organization_id,
                deployment_id=binding.deployment_id,
                session_id=old_session.id,
                target_type="conversation_session",
                target_id=old_session.id,
            )
            self.audit.record(
                action=AuditAction.MEMORY_GRANT_REVOKED,
                organization_id=binding.organization_id,
                deployment_id=binding.deployment_id,
                session_id=old_session.id,
                target_type="conversation_access_grant",
                target_id=old_grant.id,
            )
            self.audit.record(
                action=AuditAction.MEMORY_SESSION_CREATED,
                organization_id=binding.organization_id,
                deployment_id=binding.deployment_id,
                session_id=new_session.id,
                target_type="conversation_session",
                target_id=new_session.id,
            )
            self.audit.record(
                action=AuditAction.MEMORY_GRANT_ISSUED,
                organization_id=binding.organization_id,
                deployment_id=binding.deployment_id,
                session_id=new_session.id,
                target_type="conversation_access_grant",
                target_id=new_grant.id,
            )
            return _conversation_result(
                session=new_session,
                grant=new_grant,
                access_token=issued.raw_value,
                replayed=False,
                previous_lifecycle=old_session.lifecycle,
                previous_lifecycle_revision=old_session.lifecycle_revision,
            )

        return self._execute(operation)


class DeletePublicConversationUseCase(_TransactionalPublicUseCase):
    def execute(self, command: LifecycleCommand) -> DeletePublicConversationResult:
        authorized_replay = self._execute(
            lambda: self._authorized_delete_replay(command)
        )
        if authorized_replay is not None:
            result, replay_scope_digest = authorized_replay
            self._admit(
                operation="conversation.delete",
                binding=None,
                grant_id=None,
                network_address=command.network_address,
                request_scope_digest=replay_scope_digest,
                request_key_hash=command.idempotency_key_hash,
                request_fingerprint=command.request_fingerprint,
                disposition=PublicConversationAdmissionDisposition.EXACT_RETRY,
            )
            return result

        admission_binding, admission_grant_id, admission_scope_digest, disposition = (
            self._execute(
                lambda: self._lifecycle_preflight(
                    command=command,
                    operation="conversation.delete",
                    require_transcript=True,
                )
            )
        )
        self._admit(
            operation="conversation.delete",
            binding=admission_binding,
            grant_id=admission_grant_id,
            network_address=command.network_address,
            request_scope_digest=admission_scope_digest,
            request_key_hash=command.idempotency_key_hash,
            request_fingerprint=command.request_fingerprint,
            disposition=disposition,
        )

        def operation() -> DeletePublicConversationResult:
            binding = self._binding(command.url_slug, for_update=True)
            self._require_admitted_binding(binding, admission_binding)
            grant = self._grant_for_mutation(
                binding=binding,
                raw_access_token=command.access_token,
                now=command.now,
                allow_expired_for_replay=True,
            )
            session = self._session_for_grant(
                grant,
                now=command.now,
                allow_expired_for_replay=True,
            )
            # Expiry is authoritative only after every mutation row lock is held.
            now = self._current_time()
            reservation = self._reserve(
                organization_id=binding.organization_id,
                operation="conversation.delete",
                scope_digest=_scope_digest(
                    operation="conversation.delete",
                    binding=binding,
                    grant=grant,
                    session=session,
                ),
                idempotency_key_hash=command.idempotency_key_hash,
                request_fingerprint=command.request_fingerprint,
                now=now,
                authorization_app_id=binding.app_id,
                authorization_verifier_key_version=grant.verifier_key_version,
                authorization_verifier_hash=grant.verifier_hash,
            )
            if not reservation.created:
                reservation.record.require_matching_fingerprint(
                    command.request_fingerprint
                )
                result_snapshot = _record_result_snapshot(
                    reservation.record,
                    expected_resource_type="conversation_purge_job",
                )
                purge_job = _record_purge_job(self.repository, reservation.record)
                receipt = self._replay_secret(
                    record=reservation.record,
                    purpose="purge_receipt",
                    now=now,
                )
                return DeletePublicConversationResult(
                    lifecycle=result_snapshot.lifecycle,
                    lifecycle_revision=result_snapshot.lifecycle_revision,
                    purge_job_id=purge_job.id,
                    purge_receipt=receipt,
                    replayed=True,
                )

            grant.require_transcript(
                deployment_id=binding.deployment_id,
                deployment_version=binding.deployment_version,
                audience_kind=AudienceKind.PUBLIC_CHATBOT,
                now=now,
            )
            self._require_unexpired_session(session, now=now)
            issued = self.secrets.issue_purge_receipt()
            purge_job = ConversationPurgeJob.pending(
                purge_job_id=uuid.uuid4(),
                organization_id=binding.organization_id,
                session_id=session.id,
                session_reference_digest=hashlib.sha256(
                    str(session.id).encode("ascii")
                ).hexdigest(),
                app_id=binding.app_id,
                deployment_id=binding.deployment_id,
                deployment_version=binding.deployment_version,
                audience_kind=AudienceKind.PUBLIC_CHATBOT,
                receipt_verifier_hash=issued.verifier_hash,
                receipt_verifier_key_version=issued.verifier_key_version,
                receipt_expires_at=now + self.policy.purge_receipt_lifetime,
                max_attempts=self.policy.purge_max_attempts,
                now=now,
            )
            session.request_delete(
                expected_lifecycle_revision=command.expected_lifecycle_revision,
                now=now,
            )
            grant.revoke(now=now)
            replay = self._add_secret_replay(
                record=reservation.record,
                purpose="purge_receipt",
                raw_secret=issued.raw_value,
                expires_at=min(
                    purge_job.receipt_expires_at,
                    now + self.policy.purge_secret_replay_lifetime,
                ),
            )
            reservation.record.complete(
                resource_type="conversation_purge_job",
                resource_reference=str(purge_job.id),
                replay_record_reference=str(replay.id),
                secret_replay_expires_at=replay.expires_at,
                safe_result_code="delete_requested",
                result_snapshot=IdempotencyResultSnapshot(
                    lifecycle=session.lifecycle,
                    lifecycle_revision=session.lifecycle_revision,
                    memory_contract_version=None,
                    expires_at=None,
                ),
                now=now,
            )
            self.repository.save_session(session)
            self.repository.save_access_grant(grant)
            self.repository.add_purge_job(purge_job)
            self.repository.save_idempotency(reservation.record)
            self.audit.record(
                action=AuditAction.MEMORY_SESSION_DELETE_REQUESTED,
                organization_id=binding.organization_id,
                deployment_id=binding.deployment_id,
                session_id=session.id,
                target_type="conversation_session",
                target_id=session.id,
                purge_job_id=purge_job.id,
            )
            self.audit.record(
                action=AuditAction.MEMORY_GRANT_REVOKED,
                organization_id=binding.organization_id,
                deployment_id=binding.deployment_id,
                session_id=session.id,
                target_type="conversation_access_grant",
                target_id=grant.id,
                purge_job_id=purge_job.id,
            )
            return DeletePublicConversationResult(
                lifecycle=session.lifecycle,
                lifecycle_revision=session.lifecycle_revision,
                purge_job_id=purge_job.id,
                purge_receipt=issued.raw_value,
                replayed=False,
            )

        return self._execute(operation)


class GetPublicTranscriptUseCase(_TransactionalPublicUseCase):
    """Return the safe empty projection until MBA-318 writes public turns."""

    def execute(
        self,
        *,
        url_slug: str,
        access_token: str,
        now: datetime,
    ) -> PublicTranscriptResult:
        def operation() -> PublicTranscriptResult:
            binding = self._binding(url_slug)
            grant = self._grant_for_mutation(
                binding=binding,
                raw_access_token=access_token,
                now=now,
            )
            grant.require_transcript(
                deployment_id=binding.deployment_id,
                deployment_version=binding.deployment_version,
                audience_kind=AudienceKind.PUBLIC_CHATBOT,
                now=now,
            )
            session = self._session_for_grant(grant, now=now)
            if session.lifecycle not in {
                SessionLifecycle.ACTIVE,
                SessionLifecycle.CLOSED,
            }:
                raise AccessGrantNotUsableError()
            return PublicTranscriptResult(
                lifecycle=session.lifecycle,
                lifecycle_revision=session.lifecycle_revision,
                content_revision=session.content_revision,
                expires_at=_effective_access_expires_at(
                    session=session,
                    grant=grant,
                ),
                turns=(),
            )

        return self._execute(operation)


class GetPublicPurgeStatusUseCase(_TransactionalPublicUseCase):
    def execute(
        self,
        *,
        url_slug: str,
        purge_receipt: str,
        now: datetime,
    ) -> PublicPurgeStatusResult:
        def operation() -> PublicPurgeStatusResult:
            # Resolve first so a stale/unknown slug cannot become an oracle.
            binding = self.repository.resolve_public_app(url_slug)
            if binding is None:
                raise PurgeReceiptNotUsableError()
            verifiers = self.secrets.purge_receipt_verifiers(purge_receipt)
            if not verifiers:
                raise PurgeReceiptNotUsableError()
            job = self.repository.find_purge_job(
                verifier_candidates=verifiers,
            )
            if job is None or now >= job.receipt_expires_at:
                raise PurgeReceiptNotUsableError()
            verifier_hash = next(
                (
                    candidate_hash
                    for version, candidate_hash in verifiers
                    if version == job.receipt_verifier_key_version
                ),
                None,
            )
            if verifier_hash is None or not hmac.compare_digest(
                job.receipt_verifier_hash, verifier_hash
            ):
                raise PurgeReceiptNotUsableError()
            if (
                job.organization_id != binding.organization_id
                or job.app_id != binding.app_id
                or job.audience_kind is not AudienceKind.PUBLIC_CHATBOT
            ):
                raise PurgeReceiptNotUsableError()
            return PublicPurgeStatusResult(
                status=job.status,
                updated_at=job.updated_at,
                safe_failure_reason=job.safe_failure_reason,
            )

        return self._execute(operation)


def _new_session(
    *,
    binding: PublicDeploymentBinding,
    policy: PublicConversationPolicy,
    now: datetime,
) -> ConversationSession:
    return ConversationSession.create(
        session_id=uuid.uuid4(),
        organization_id=binding.organization_id,
        app_id=binding.app_id,
        workflow_id=binding.workflow_id,
        deployment_id=binding.deployment_id,
        deployment_version=binding.deployment_version,
        deployment_snapshot_hash=None,
        mapping_version=binding.mapping_version,
        memory_policy_version=binding.memory_policy_version,
        memory_contract_version=binding.memory_contract_version,
        storage_generation=binding.storage_generation,
        audience_kind=AudienceKind.PUBLIC_CHATBOT,
        subject_type=None,
        subject_id=None,
        idle_expires_at=now + policy.idle_lifetime,
        absolute_expires_at=now + policy.absolute_lifetime,
        now=now,
    )


def _scope_digest(
    *,
    operation: str,
    binding: PublicDeploymentBinding,
    grant: ConversationAccessGrant | None = None,
    session: ConversationSession | None = None,
) -> str:
    data = {
        "operation": operation,
        "organization_id": str(binding.organization_id),
        "deployment_id": str(binding.deployment_id),
        "deployment_version": binding.deployment_version,
        "audience_kind": AudienceKind.PUBLIC_CHATBOT.value,
        "grant_id": str(grant.id) if grant is not None else None,
        "session_id": str(session.id) if session is not None else None,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def public_request_fingerprint(
    request_body: dict[str, object],
    *,
    expected_lifecycle_revision: int | None = None,
) -> str:
    """Hash the bounded semantic request, including lifecycle preconditions."""

    if expected_lifecycle_revision is not None and expected_lifecycle_revision < 1:
        raise ValueError("expected lifecycle revision must be positive")
    data: dict[str, object] = {"body": request_body}
    if expected_lifecycle_revision is not None:
        data["preconditions"] = {
            "expected_lifecycle_revision": expected_lifecycle_revision,
        }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _secret_replay_associated_data_digest(
    *, record: ConversationIdempotency, purpose: str
) -> str:
    data = {
        "idempotency_record_id": str(record.id),
        "organization_id": str(record.organization_id),
        "operation": record.operation,
        "scope_digest": record.scope_digest,
        "idempotency_key_hash": record.idempotency_key_hash,
        "request_fingerprint": record.request_fingerprint,
        "purpose": purpose,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _record_result_snapshot(
    record: ConversationIdempotency,
    *,
    expected_resource_type: str,
) -> IdempotencyResultSnapshot:
    if (
        record.status is not IdempotencyStatus.COMPLETED
        or record.resource_type != expected_resource_type
        or not record.resource_reference
        or record.result_snapshot is None
    ):
        raise MemoryAdapterUnavailableError()
    try:
        uuid.UUID(record.resource_reference)
    except ValueError:
        raise MemoryAdapterUnavailableError() from None
    return record.result_snapshot


def _record_purge_job(
    repository: PublicConversationRepositoryPort,
    record: ConversationIdempotency,
) -> ConversationPurgeJob:
    if (
        record.resource_type != "conversation_purge_job"
        or not record.resource_reference
    ):
        raise MemoryAdapterUnavailableError()
    try:
        resource_id = uuid.UUID(record.resource_reference)
    except ValueError:
        raise MemoryAdapterUnavailableError() from None
    # This lookup runs only after a matching verifier-bound idempotency record;
    # its UUID is never a public capability.
    result = repository.lock_purge_job_by_id(
        organization_id=record.organization_id,
        purge_job_id=resource_id,
    )
    if result is None:
        raise MemoryAdapterUnavailableError()
    return result


def _conversation_result(
    *,
    session: ConversationSession,
    grant: ConversationAccessGrant,
    access_token: str,
    replayed: bool,
    previous_lifecycle: SessionLifecycle | None = None,
    previous_lifecycle_revision: int | None = None,
) -> PublicConversationResult:
    return PublicConversationResult(
        lifecycle=session.lifecycle,
        lifecycle_revision=session.lifecycle_revision,
        memory_contract_version=session.memory_contract_version,
        expires_at=_effective_access_expires_at(session=session, grant=grant),
        access_token=access_token,
        replayed=replayed,
        previous_lifecycle=previous_lifecycle,
        previous_lifecycle_revision=previous_lifecycle_revision,
    )


def _session_result_snapshot(
    session: ConversationSession,
    *,
    grant: ConversationAccessGrant,
    previous_lifecycle: SessionLifecycle | None = None,
    previous_lifecycle_revision: int | None = None,
) -> IdempotencyResultSnapshot:
    return IdempotencyResultSnapshot(
        lifecycle=session.lifecycle,
        lifecycle_revision=session.lifecycle_revision,
        memory_contract_version=session.memory_contract_version,
        expires_at=_effective_access_expires_at(session=session, grant=grant),
        previous_lifecycle=previous_lifecycle,
        previous_lifecycle_revision=previous_lifecycle_revision,
    )


def _conversation_result_from_snapshot(
    *,
    record: ConversationIdempotency,
    access_token: str,
    replayed: bool,
) -> PublicConversationResult:
    snapshot = _record_result_snapshot(
        record,
        expected_resource_type="conversation_session",
    )
    if snapshot.memory_contract_version is None or snapshot.expires_at is None:
        raise MemoryAdapterUnavailableError()
    return PublicConversationResult(
        lifecycle=snapshot.lifecycle,
        lifecycle_revision=snapshot.lifecycle_revision,
        memory_contract_version=snapshot.memory_contract_version,
        expires_at=snapshot.expires_at,
        access_token=access_token,
        replayed=replayed,
        previous_lifecycle=snapshot.previous_lifecycle,
        previous_lifecycle_revision=snapshot.previous_lifecycle_revision,
    )


def _close_result(
    session: ConversationSession,
    *,
    grant: ConversationAccessGrant,
    replayed: bool,
) -> ClosePublicConversationResult:
    return ClosePublicConversationResult(
        lifecycle=session.lifecycle,
        lifecycle_revision=session.lifecycle_revision,
        memory_contract_version=session.memory_contract_version,
        expires_at=_effective_access_expires_at(session=session, grant=grant),
        replayed=replayed,
    )


def _effective_access_expires_at(
    *,
    session: ConversationSession,
    grant: ConversationAccessGrant,
) -> datetime:
    return min(
        grant.expires_at,
        session.idle_expires_at,
        session.absolute_expires_at,
    )


def _close_result_from_snapshot(
    record: ConversationIdempotency,
    *,
    replayed: bool,
) -> ClosePublicConversationResult:
    snapshot = _record_result_snapshot(
        record,
        expected_resource_type="conversation_session",
    )
    if snapshot.memory_contract_version is None or snapshot.expires_at is None:
        raise MemoryAdapterUnavailableError()
    return ClosePublicConversationResult(
        lifecycle=snapshot.lifecycle,
        lifecycle_revision=snapshot.lifecycle_revision,
        memory_contract_version=snapshot.memory_contract_version,
        expires_at=snapshot.expires_at,
        replayed=replayed,
    )


__all__ = [
    "ClosePublicConversationResult",
    "ClosePublicConversationUseCase",
    "CreatePublicConversationCommand",
    "CreatePublicConversationUseCase",
    "DeletePublicConversationResult",
    "DeletePublicConversationUseCase",
    "GetPublicPurgeStatusUseCase",
    "GetPublicTranscriptUseCase",
    "IdempotencyReservation",
    "IssuedSecret",
    "LifecycleCommand",
    "PublicConversationAuditPort",
    "PublicConversationAdmissionPort",
    "PublicConversationAdmissionDisposition",
    "PublicConversationPolicy",
    "PublicConversationRepositoryPort",
    "PublicConversationResult",
    "PublicDeploymentBinding",
    "PublicPurgeStatusResult",
    "PublicSecretIssuerPort",
    "public_request_fingerprint",
    "ResetPublicConversationUseCase",
    "SecretCiphertext",
    "SecretReplayCipherPort",
]
