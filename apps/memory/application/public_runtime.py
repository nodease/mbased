"""Public Conversation run admission up to the durable Memory dispatch boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Protocol

from apps.memory.application.public_lifecycle import (
    PublicConversationAdmissionDisposition,
    PublicDeploymentBinding,
)
from apps.memory.application.content import memory_content_aad
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationMemoryEntry,
    ConversationSession,
    ConversationTurn,
    DispatchStatus,
    EntryLifecycle,
    EntryType,
    MAX_MEMORY_CONTENT_BYTES,
    MemoryTurnDispatchJob,
    ProtectedContent,
    ProtectedEntryContent,
    RequestIdentity,
    SessionLifecycle,
    TurnStatus,
)
from apps.memory.domain.errors import (
    AccessGrantNotUsableError,
    PublicConversationFeatureDisabledError,
)
from apps.memory.domain.public_access import AccessGrantState


class RuntimeFingerprintPort(Protocol):
    def fingerprint(self, **kwargs) -> tuple[str, str]: ...


class MemoryContentCipherPort(Protocol):
    def protect(self, value: str, *, associated_data: str) -> ProtectedContent: ...

    def reveal(
        self,
        protected: ProtectedContent,
        *,
        associated_data: str,
    ) -> str | None: ...


class TurnDispatchPublisherPort(Protocol):
    def publish(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        dispatch_id: uuid.UUID,
        turn_id: uuid.UUID,
        memory_contract_version: str,
        storage_generation: int,
        minimum_worker_capability: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class StartPublicConversationTurnCommand:
    url_slug: str
    access_token: str
    idempotency_key_hash: str
    expected_lifecycle_revision: int
    inputs: Mapping[str, object]
    network_address: str
    now: datetime


@dataclass(frozen=True, slots=True)
class StartPublicConversationTurnResult:
    session_id: uuid.UUID
    turn_id: uuid.UUID
    dispatch_id: uuid.UUID
    turn_sequence: int
    turn_version: int
    lifecycle_revision: int
    turn_state: TurnStatus
    replayed: bool
    dispatch_publish_required: bool = False


@dataclass(frozen=True, slots=True)
class PublicTurnStatusResult:
    turn_id: uuid.UUID
    turn_sequence: int
    turn_state: TurnStatus
    display: str | None
    safe_failure_reason: str | None
    lifecycle_revision: int


@dataclass(frozen=True, slots=True)
class _Preflight:
    binding: PublicDeploymentBinding
    grant_id: uuid.UUID
    session_id: uuid.UUID
    request_fingerprint: str
    fingerprint_key_version: str
    input_text: str
    disposition: PublicConversationAdmissionDisposition


class StartPublicConversationTurnUseCase:
    def __init__(
        self,
        *,
        repository,
        uow,
        secrets,
        content_cipher: MemoryContentCipherPort,
        fingerprinter: RuntimeFingerprintPort,
        admission=None,
        dispatch_publisher: TurnDispatchPublisherPort | None = None,
        minimum_worker_capability: str,
        max_dispatch_attempts: int,
    ) -> None:
        if not minimum_worker_capability or max_dispatch_attempts < 1:
            raise ValueError("public Conversation runtime policy is invalid")
        self.repository = repository
        self.uow = uow
        self.secrets = secrets
        self.content_cipher = content_cipher
        self.fingerprinter = fingerprinter
        self.admission = admission
        self.dispatch_publisher = dispatch_publisher
        self.minimum_worker_capability = minimum_worker_capability
        self.max_dispatch_attempts = max_dispatch_attempts

    def execute(
        self,
        command: StartPublicConversationTurnCommand,
    ) -> StartPublicConversationTurnResult:
        preflight = self._execute(lambda: self._preflight(command))
        if self.admission is not None:
            self.admission.admit(
                operation="conversation.run",
                binding=preflight.binding,
                grant_id=preflight.grant_id,
                network_address=command.network_address,
                request_scope_digest=_scope_digest(
                    binding=preflight.binding,
                    grant_id=preflight.grant_id,
                    session_id=preflight.session_id,
                ),
                request_key_hash=command.idempotency_key_hash,
                request_fingerprint=preflight.request_fingerprint,
                disposition=preflight.disposition,
            )
        result = self._execute(lambda: self._start(command, preflight))
        if self.dispatch_publisher is not None and result.dispatch_publish_required and result.turn_state not in {
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
        }:
            self.dispatch_publisher.publish(
                organization_id=preflight.binding.organization_id,
                session_id=preflight.session_id,
                dispatch_id=result.dispatch_id,
                turn_id=result.turn_id,
                memory_contract_version=preflight.binding.memory_contract_version,
                storage_generation=preflight.binding.storage_generation,
                minimum_worker_capability=self.minimum_worker_capability,
            )
        return result

    def _preflight(
        self,
        command: StartPublicConversationTurnCommand,
    ) -> _Preflight:
        binding = self._binding(command.url_slug, for_update=False)
        input_text = _mapped_input(binding, command.inputs)
        grant, session = self._authorized_scope(
            binding=binding,
            access_token=command.access_token,
            expected_lifecycle_revision=command.expected_lifecycle_revision,
            now=command.now,
        )
        key_version, fingerprint = self.fingerprinter.fingerprint(
            organization_id=binding.organization_id,
            deployment_id=binding.deployment_id,
            deployment_version=binding.deployment_version,
            grant_id=grant.id,
            session_id=session.id,
            expected_lifecycle_revision=command.expected_lifecycle_revision,
            mapping_version=binding.mapping_version,
            memory_policy_version=binding.memory_policy_version,
            memory_contract_version=binding.memory_contract_version,
            storage_generation=binding.storage_generation,
            input_variable=binding.runtime_input_variable,
            input_text=input_text,
        )
        existing = self.repository.find_turn_by_request(
            organization_id=binding.organization_id,
            session_id=session.id,
            idempotency_key_hash=command.idempotency_key_hash,
        )
        disposition = PublicConversationAdmissionDisposition.LOGICAL_REQUEST
        if existing is not None:
            existing.request_identity.ensure_replay_matches(fingerprint)
            if existing.access_grant_id != grant.id:
                raise AccessGrantNotUsableError()
            disposition = PublicConversationAdmissionDisposition.EXACT_RETRY
        return _Preflight(
            binding=binding,
            grant_id=grant.id,
            session_id=session.id,
            request_fingerprint=fingerprint,
            fingerprint_key_version=key_version,
            input_text=input_text,
            disposition=disposition,
        )

    def _start(
        self,
        command: StartPublicConversationTurnCommand,
        preflight: _Preflight,
    ) -> StartPublicConversationTurnResult:
        binding = self._binding(command.url_slug, for_update=True)
        if binding != preflight.binding:
            raise AccessGrantNotUsableError()
        now = self.repository.current_time()
        grant, session = self._authorized_scope(
            binding=binding,
            access_token=command.access_token,
            expected_lifecycle_revision=command.expected_lifecycle_revision,
            now=now,
        )
        if grant.id != preflight.grant_id or session.id != preflight.session_id:
            raise AccessGrantNotUsableError()
        key_version, fingerprint = self.fingerprinter.fingerprint(
            organization_id=binding.organization_id,
            deployment_id=binding.deployment_id,
            deployment_version=binding.deployment_version,
            grant_id=grant.id,
            session_id=session.id,
            expected_lifecycle_revision=command.expected_lifecycle_revision,
            mapping_version=binding.mapping_version,
            memory_policy_version=binding.memory_policy_version,
            memory_contract_version=binding.memory_contract_version,
            storage_generation=binding.storage_generation,
            input_variable=binding.runtime_input_variable,
            input_text=preflight.input_text,
        )
        if key_version != preflight.fingerprint_key_version or fingerprint != preflight.request_fingerprint:
            raise AccessGrantNotUsableError()
        existing = self.repository.find_turn_by_request(
            organization_id=binding.organization_id,
            session_id=session.id,
            idempotency_key_hash=command.idempotency_key_hash,
        )
        if existing is not None:
            turn = self.repository.lock_turn(
                organization_id=binding.organization_id,
                session_id=session.id,
                turn_id=existing.id,
            )
            if turn is None:
                raise AccessGrantNotUsableError()
            turn.request_identity.ensure_replay_matches(fingerprint)
            if turn.access_grant_id != grant.id:
                raise AccessGrantNotUsableError()
            user_entry = self.repository.get_entry(
                organization_id=binding.organization_id,
                session_id=session.id,
                entry_id=turn.user_entry_id,
            )
            if user_entry is None or user_entry.turn_id != turn.id:
                raise AccessGrantNotUsableError()
            dispatch = self.repository.lock_dispatch_job(
                organization_id=binding.organization_id,
                dispatch_id=turn.dispatch_id,
            )
            if dispatch is None or dispatch.turn_id != turn.id:
                raise AccessGrantNotUsableError()
            if (
                dispatch.status is DispatchStatus.CLAIMED
                and dispatch.claim_deadline_at is not None
                and now >= dispatch.claim_deadline_at
            ):
                dispatch.recover_expired_claim(
                    now=now,
                    retry_at=(
                        None
                        if dispatch.attempt_count >= dispatch.max_attempts
                        else now + timedelta(seconds=1)
                    ),
                    safe_reason_code="memory.dispatch_claim_expired",
                )
                self.repository.save_dispatch_job(dispatch)
            if dispatch.status is DispatchStatus.TERMINAL and not turn.terminal:
                safe_reason = (
                    dispatch.safe_failure_reason or "memory.dispatch_terminal"
                )
                turn.fail(
                    expected_version=turn.version,
                    safe_reason_code=safe_reason,
                    now=now,
                )
                session.release_terminal_turn(
                    turn_id=turn.id,
                    expected_lifecycle_revision=session.lifecycle_revision,
                    now=now,
                )
                user_entry.reject(now=now)
                self.repository.save_turn(turn)
                self.repository.save_session(session)
                self.repository.save_entry(user_entry)
            return _turn_result(
                turn,
                session.lifecycle_revision,
                replayed=True,
                dispatch_publish_required=(
                    dispatch.status is DispatchStatus.PENDING
                    or (
                        dispatch.status is DispatchStatus.RECONCILE_REQUIRED
                        and dispatch.next_attempt_at is not None
                        and now >= dispatch.next_attempt_at
                    )
                ),
            )

        turn_id = uuid.uuid4()
        user_entry_id = uuid.uuid4()
        dispatch_id = uuid.uuid4()
        sequence = session.claim_turn(
            turn_id=turn_id,
            expected_lifecycle_revision=command.expected_lifecycle_revision,
            now=now,
        )
        request_identity = RequestIdentity(
            idempotency_key_hash=command.idempotency_key_hash,
            request_fingerprint=fingerprint,
        )
        turn = ConversationTurn.start(
            turn_id=turn_id,
            organization_id=binding.organization_id,
            session_id=session.id,
            sequence=sequence,
            started_lifecycle_revision=session.lifecycle_revision,
            request_identity=request_identity,
            user_entry_id=user_entry_id,
            dispatch_id=dispatch_id,
            access_grant_id=grant.id,
            request_fingerprint_key_version=key_version,
            now=now,
        )
        display = self.content_cipher.protect(
            preflight.input_text,
            associated_data=memory_content_aad(
                organization_id=binding.organization_id,
                session_id=session.id,
                turn_id=turn_id,
                entry_id=user_entry_id,
                projection="display",
            ),
        )
        model = self.content_cipher.protect(
            preflight.input_text,
            associated_data=memory_content_aad(
                organization_id=binding.organization_id,
                session_id=session.id,
                turn_id=turn_id,
                entry_id=user_entry_id,
                projection="model",
            ),
        )
        entry = ConversationMemoryEntry.provisional_user(
            entry_id=user_entry_id,
            organization_id=binding.organization_id,
            session_id=session.id,
            turn_id=turn_id,
            sequence=(sequence * 2) - 1,
            channel="conversation",
            content=ProtectedEntryContent(display=display, model=model),
            idempotency_key_hash=command.idempotency_key_hash,
            now=now,
        )
        dispatch = MemoryTurnDispatchJob.pending(
            dispatch_id=dispatch_id,
            organization_id=binding.organization_id,
            session_id=session.id,
            turn_id=turn_id,
            memory_contract_version=binding.memory_contract_version,
            storage_generation=binding.storage_generation,
            minimum_worker_capability=self.minimum_worker_capability,
            max_attempts=self.max_dispatch_attempts,
            now=now,
        )
        self.repository.save_session(session)
        self.repository.add_turn(turn)
        self.repository.add_entry(entry)
        self.repository.add_dispatch_job(dispatch)
        return _turn_result(
            turn,
            session.lifecycle_revision,
            replayed=False,
            dispatch_publish_required=True,
        )

    def _binding(self, url_slug: str, *, for_update: bool) -> PublicDeploymentBinding:
        resolver = (
            self.repository.lock_public_deployment
            if for_update
            else self.repository.resolve_public_deployment
        )
        binding = resolver(url_slug)
        if binding is None:
            raise AccessGrantNotUsableError()
        if not binding.runtime_contract_ready:
            raise PublicConversationFeatureDisabledError()
        return binding

    def _authorized_scope(
        self,
        *,
        binding: PublicDeploymentBinding,
        access_token: str,
        expected_lifecycle_revision: int,
        now: datetime,
    ):
        verifiers = self.secrets.access_grant_verifiers(access_token)
        if not verifiers:
            raise AccessGrantNotUsableError()
        grant = self.repository.lock_access_grant(verifier_candidates=verifiers)
        if grant is None:
            raise AccessGrantNotUsableError()
        candidate = next(
            (
                digest
                for version, digest in verifiers
                if version == grant.verifier_key_version
            ),
            None,
        )
        if candidate is None or not hmac.compare_digest(candidate, grant.verifier_hash):
            raise AccessGrantNotUsableError()
        grant.require_active(
            deployment_id=binding.deployment_id,
            deployment_version=binding.deployment_version,
            audience_kind=AudienceKind.PUBLIC_CHATBOT,
            now=now,
        )
        if grant.state is not AccessGrantState.ACTIVE:
            raise AccessGrantNotUsableError()
        session = self.repository.lock_session(
            organization_id=binding.organization_id,
            session_id=grant.session_id,
        )
        if session is None or not _session_matches(session, binding):
            raise AccessGrantNotUsableError()
        session.require_active(
            expected_lifecycle_revision=expected_lifecycle_revision,
            now=now,
        )
        return grant, session

    def _execute(self, operation):
        self.uow.begin()
        try:
            result = operation()
            self.uow.commit()
            return result
        except Exception:
            self.uow.rollback()
            raise


def _mapped_input(binding: PublicDeploymentBinding, inputs: Mapping[str, object]) -> str:
    variable = binding.runtime_input_variable
    if not isinstance(inputs, Mapping) or not variable or set(inputs) != {variable}:
        raise ValueError("memory.input_mapping_invalid")
    value = inputs.get(variable)
    if not isinstance(value, str):
        raise ValueError("memory.input_mapping_invalid")
    length = len(value.encode("utf-8"))
    if not 1 <= length <= MAX_MEMORY_CONTENT_BYTES:
        raise ValueError("memory.input_mapping_invalid")
    return value


def _session_matches(session, binding: PublicDeploymentBinding) -> bool:
    return (
        session.organization_id == binding.organization_id
        and session.app_id == binding.app_id
        and session.workflow_id == binding.workflow_id
        and session.deployment_id == binding.deployment_id
        and session.deployment_version == binding.deployment_version
        and session.mapping_version == binding.mapping_version
        and session.memory_policy_version == binding.memory_policy_version
        and session.memory_contract_version == binding.memory_contract_version
        and session.storage_generation == binding.storage_generation
        and session.audience_kind is AudienceKind.PUBLIC_CHATBOT
    )


def _scope_digest(*, binding, grant_id, session_id) -> str:
    payload = json.dumps(
        {
            "operation": "conversation.run",
            "organization_id": str(binding.organization_id),
            "deployment_id": str(binding.deployment_id),
            "deployment_version": binding.deployment_version,
            "grant_id": str(grant_id),
            "session_id": str(session_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class GetPublicTurnStatusUseCase:
    """Resolve one grant-bound turn without exposing non-display projections."""

    def __init__(self, *, repository, uow, secrets, content_cipher: MemoryContentCipherPort) -> None:
        self.repository = repository
        self.uow = uow
        self.secrets = secrets
        self.content_cipher = content_cipher

    def execute(
        self,
        *,
        url_slug: str,
        turn_id: uuid.UUID,
        access_token: str,
        now: datetime,
    ) -> PublicTurnStatusResult:
        self.uow.begin()
        try:
            binding = self.repository.resolve_public_deployment(url_slug)
            if binding is None:
                raise AccessGrantNotUsableError()
            grant, session = self._authorized_transcript_scope(
                binding=binding,
                access_token=access_token,
                now=now,
            )
            turn = self.repository.lock_turn(
                organization_id=binding.organization_id,
                session_id=session.id,
                turn_id=turn_id,
            )
            if turn is None or turn.access_grant_id != grant.id:
                raise AccessGrantNotUsableError()
            result = PublicTurnStatusResult(
                turn_id=turn.id,
                turn_sequence=turn.sequence,
                turn_state=turn.status,
                display=self._approved_display(
                    binding=binding,
                    session=session,
                    turn=turn,
                ),
                safe_failure_reason=turn.safe_failure_reason,
                lifecycle_revision=session.lifecycle_revision,
            )
            self.uow.commit()
            return result
        except Exception:
            self.uow.rollback()
            raise

    def _authorized_transcript_scope(
        self,
        *,
        binding: PublicDeploymentBinding,
        access_token: str,
        now: datetime,
    ) -> tuple[object, ConversationSession]:
        verifiers = self.secrets.access_grant_verifiers(access_token)
        if not verifiers:
            raise AccessGrantNotUsableError()
        grant = self.repository.lock_access_grant(verifier_candidates=verifiers)
        if grant is None:
            raise AccessGrantNotUsableError()
        candidate = next(
            (digest for version, digest in verifiers if version == grant.verifier_key_version),
            None,
        )
        if candidate is None or not hmac.compare_digest(candidate, grant.verifier_hash):
            raise AccessGrantNotUsableError()
        grant.require_transcript(
            deployment_id=binding.deployment_id,
            deployment_version=binding.deployment_version,
            audience_kind=AudienceKind.PUBLIC_CHATBOT,
            now=now,
        )
        session = self.repository.lock_session(
            organization_id=binding.organization_id,
            session_id=grant.session_id,
        )
        if (
            session is None
            or session.lifecycle not in {SessionLifecycle.ACTIVE, SessionLifecycle.CLOSED}
            or not _session_matches(session, binding)
        ):
            raise AccessGrantNotUsableError()
        return grant, session

    def _approved_display(
        self,
        *,
        binding: PublicDeploymentBinding,
        session: ConversationSession,
        turn: ConversationTurn,
    ) -> str | None:
        if turn.status is not TurnStatus.COMPLETED:
            return None
        if turn.assistant_entry_id is None:
            raise AccessGrantNotUsableError()
        entry = self.repository.get_entry(
            organization_id=binding.organization_id,
            session_id=session.id,
            entry_id=turn.assistant_entry_id,
        )
        if (
            entry is None
            or entry.turn_id != turn.id
            or entry.entry_type is not EntryType.ASSISTANT_TURN
            or entry.lifecycle is not EntryLifecycle.APPROVED
            or entry.content is None
            or entry.content.display is None
        ):
            raise AccessGrantNotUsableError()
        display = self.content_cipher.reveal(
            entry.content.display,
            associated_data=memory_content_aad(
                organization_id=binding.organization_id,
                session_id=session.id,
                turn_id=turn.id,
                entry_id=entry.id,
                projection="display",
            ),
        )
        if display is None:
            raise AccessGrantNotUsableError()
        return display


def _turn_result(
    turn,
    lifecycle_revision: int,
    *,
    replayed: bool,
    dispatch_publish_required: bool = False,
):
    return StartPublicConversationTurnResult(
        session_id=turn.session_id,
        turn_id=turn.id,
        dispatch_id=turn.dispatch_id,
        turn_sequence=turn.sequence,
        turn_version=turn.version,
        lifecycle_revision=lifecycle_revision,
        turn_state=turn.status,
        replayed=replayed,
        dispatch_publish_required=dispatch_publish_required,
    )


__all__ = [
    "GetPublicTurnStatusUseCase",
    "MemoryContentCipherPort",
    "PublicTurnStatusResult",
    "RuntimeFingerprintPort",
    "StartPublicConversationTurnCommand",
    "StartPublicConversationTurnResult",
    "StartPublicConversationTurnUseCase",
    "TurnDispatchPublisherPort",
]
