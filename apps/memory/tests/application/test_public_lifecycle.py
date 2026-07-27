from __future__ import annotations

import copy
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet, InvalidToken

from apps.memory.adapters.security import (
    FernetMemoryContentCipher,
    HmacPublicSecretIssuer,
)
from apps.memory.application.content import memory_content_aad
from apps.memory.application.public_lifecycle import (
    ClosePublicConversationUseCase,
    CreatePublicConversationCommand,
    CreatePublicConversationUseCase,
    DeletePublicConversationUseCase,
    GetPublicPurgeStatusUseCase,
    GetPublicTranscriptUseCase,
    IdempotencyReservation,
    IssuedSecret,
    LifecycleCommand,
    PublicConversationPolicy,
    PublicDeploymentBinding,
    PublicTranscriptTurnSource,
    ResetPublicConversationUseCase,
    SecretCiphertext,
    _secret_replay_associated_data_digest,
)
from apps.memory.domain.conversation import (
    ConversationMemoryEntry,
    ConversationPurgeJob,
    ConversationSession,
    ConversationTurn,
    ProtectedEntryContent,
    RequestIdentity,
    TurnStatus,
)
from apps.memory.domain.errors import (
    AccessGrantNotUsableError,
    DuplicateRequestConflictError,
    MemoryAdapterUnavailableError,
    PurgeReceiptNotUsableError,
    SecretReplayExpiredError,
)
from apps.memory.domain.public_access import (
    ConversationAccessGrant,
    ConversationIdempotency,
    EncryptedSecretReplay,
)


def _now() -> datetime:
    return datetime(2026, 7, 18, 11, 0, tzinfo=timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class _Repository:
    binding: PublicDeploymentBinding

    def __post_init__(self) -> None:
        self.sessions: dict[uuid.UUID, ConversationSession] = {}
        self.grants: dict[tuple[str, str], ConversationAccessGrant] = {}
        self.idempotency: dict[
            tuple[uuid.UUID, str, str, str], ConversationIdempotency
        ] = {}
        self.replays: dict[uuid.UUID, EncryptedSecretReplay] = {}
        self.purge_jobs: dict[uuid.UUID, ConversationPurgeJob] = {}
        self.transcript_rows: list[PublicTranscriptTurnSource] = []

    def resolve_public_deployment(self, url_slug: str):
        return self.binding if url_slug == "public-chatbot" else None

    def lock_public_deployment(self, url_slug: str):
        return self.resolve_public_deployment(url_slug)

    def resolve_public_app(self, url_slug: str):
        return self.binding if url_slug == "public-chatbot" else None

    def reserve_idempotency(self, record: ConversationIdempotency):
        key = (
            record.organization_id,
            record.operation,
            record.scope_digest,
            record.idempotency_key_hash,
        )
        existing = self.idempotency.get(key)
        if existing is not None and existing.retention_expires_at > record.created_at:
            return IdempotencyReservation(existing, created=False)
        if existing is not None:
            del self.idempotency[key]
        self.idempotency[key] = record
        return IdempotencyReservation(record, created=True)

    def find_idempotency(
        self,
        *,
        organization_id: uuid.UUID,
        operation: str,
        scope_digest: str,
        idempotency_key_hash: str,
        now: datetime,
    ):
        record = self.idempotency.get(
            (organization_id, operation, scope_digest, idempotency_key_hash)
        )
        if record is None or record.retention_expires_at <= now:
            return None
        return record

    def find_authorized_idempotency(
        self,
        *,
        organization_id: uuid.UUID,
        app_id: uuid.UUID,
        operation: str,
        idempotency_key_hash: str,
        verifier_candidates,
        now: datetime,
    ):
        matches = [
            record
            for record in self.idempotency.values()
            if record.organization_id == organization_id
            and record.authorization_app_id == app_id
            and record.operation == operation
            and record.idempotency_key_hash == idempotency_key_hash
            and (
                record.authorization_verifier_key_version,
                record.authorization_verifier_hash,
            )
            in verifier_candidates
            and record.retention_expires_at > now
        ]
        return matches[0] if len(matches) == 1 else None

    def save_idempotency(self, record: ConversationIdempotency) -> None:
        self.idempotency[
            (
                record.organization_id,
                record.operation,
                record.scope_digest,
                record.idempotency_key_hash,
            )
        ] = record

    def add_session(self, session: ConversationSession) -> None:
        self.sessions[session.id] = session

    def lock_session(self, *, organization_id: uuid.UUID, session_id: uuid.UUID):
        session = self.sessions.get(session_id)
        if session is None or session.organization_id != organization_id:
            return None
        return session

    def save_session(self, session: ConversationSession) -> None:
        self.sessions[session.id] = session

    def list_public_transcript_turns(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        after_sequence: int,
        limit: int,
    ):
        rows = sorted(
            (
                row
                for row in self.transcript_rows
                if row.turn.organization_id == organization_id
                and row.turn.session_id == session_id
                and row.turn.sequence > after_sequence
            ),
            key=lambda row: (row.turn.sequence, row.turn.id),
        )
        return tuple(rows[:limit])

    def add_access_grant(self, grant: ConversationAccessGrant) -> None:
        self.grants[(grant.verifier_key_version, grant.verifier_hash)] = grant

    def lock_access_grant(self, *, verifier_candidates):
        matches = [
            self.grants[candidate]
            for candidate in verifier_candidates
            if candidate in self.grants
        ]
        return matches[0] if len(matches) == 1 else None

    def save_access_grant(self, grant: ConversationAccessGrant) -> None:
        self.add_access_grant(grant)

    def add_secret_replay(self, replay: EncryptedSecretReplay) -> None:
        self.replays[replay.id] = replay

    def get_secret_replay(self, *, organization_id: uuid.UUID, replay_id: uuid.UUID):
        replay = self.replays.get(replay_id)
        if replay is None or replay.organization_id != organization_id:
            return None
        return replay

    def add_purge_job(self, job: ConversationPurgeJob) -> None:
        self.purge_jobs[job.id] = job

    def find_purge_job(self, *, verifier_candidates):
        for job in self.purge_jobs.values():
            if (
                job.receipt_verifier_key_version,
                job.receipt_verifier_hash,
            ) in verifier_candidates:
                return job
        return None

    def lock_purge_job_by_id(
        self, *, organization_id: uuid.UUID, purge_job_id: uuid.UUID
    ):
        job = self.purge_jobs.get(purge_job_id)
        if job is None or job.organization_id != organization_id:
            return None
        return job


class _UnitOfWork:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self._snapshot = None

    def begin(self) -> None:
        self._snapshot = copy.deepcopy(
            (
                self.repository.sessions,
                self.repository.grants,
                self.repository.idempotency,
                self.repository.replays,
                self.repository.purge_jobs,
            )
        )

    def commit(self) -> None:
        self._snapshot = None

    def rollback(self) -> None:
        assert self._snapshot is not None
        (
            self.repository.sessions,
            self.repository.grants,
            self.repository.idempotency,
            self.repository.replays,
            self.repository.purge_jobs,
        ) = self._snapshot
        self._snapshot = None

    @property
    def is_active(self) -> bool:
        return self._snapshot is not None


class _Secrets:
    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._cursor_codec = HmacPublicSecretIssuer(self._key, key_version="hmac-v1")

    def _issue(self, prefix: str, purpose: str) -> IssuedSecret:
        raw = f"{prefix}_v1_{secrets.token_urlsafe(32)}"
        return IssuedSecret(
            raw_value=raw,
            verifier_hash=self._digest(purpose, raw),
            verifier_key_version="hmac-v1",
        )

    def _verify(self, prefix: str, purpose: str, raw: str):
        if not raw.startswith(f"{prefix}_v1_") or len(raw) < 48:
            return None
        return "hmac-v1", self._digest(purpose, raw)

    def _digest(self, purpose: str, value: str) -> str:
        return hmac.new(
            self._key,
            f"test:{purpose}:{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def issue_access_grant(self) -> IssuedSecret:
        return self._issue("cag", "access")

    def access_grant_verifiers(self, raw_value: str):
        verifier = self._verify("cag", "access", raw_value)
        return (verifier,) if verifier is not None else ()

    def issue_purge_receipt(self) -> IssuedSecret:
        return self._issue("cpr", "purge")

    def purge_receipt_verifiers(self, raw_value: str):
        verifier = self._verify("cpr", "purge", raw_value)
        return (verifier,) if verifier is not None else ()

    def encode_transcript_cursor(self, **kwargs) -> str:
        return self._cursor_codec.encode_transcript_cursor(**kwargs)

    def decode_transcript_cursor(self, cursor: str, **kwargs) -> int:
        return self._cursor_codec.decode_transcript_cursor(cursor, **kwargs)


class _Cipher:
    def __init__(self) -> None:
        self._fernet = Fernet(Fernet.generate_key())

    def encrypt(
        self, raw_value: str, *, associated_data_digest: str
    ) -> SecretCiphertext:
        payload = f"{associated_data_digest}:{raw_value}".encode("utf-8")
        return SecretCiphertext(self._fernet.encrypt(payload), "fernet-test-v1")

    def decrypt(
        self,
        ciphertext: bytes,
        *,
        key_version: str,
        associated_data_digest: str,
    ) -> str | None:
        if key_version != "fernet-test-v1":
            return None
        try:
            payload = self._fernet.decrypt(ciphertext).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError):
            return None
        prefix = f"{associated_data_digest}:"
        return payload[len(prefix) :] if payload.startswith(prefix) else None


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def record(self, **event):
        self.events.append(event)


def _binding() -> PublicDeploymentBinding:
    return PublicDeploymentBinding(
        organization_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=2,
        mapping_version="mapping-v1",
        memory_policy_version="memory-v1",
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
    )


def _application(*, policy: PublicConversationPolicy | None = None):
    repository = _Repository(_binding())
    return (
        repository,
        _UnitOfWork(repository),
        _Secrets(),
        _Cipher(),
        _Audit(),
        policy or PublicConversationPolicy(),
    )


def _use_case(cls, components, *, admission=None, clock=None, content_cipher=None):
    repository, uow, secrets_port, cipher, audit, policy = components
    kwargs = dict(
        repository=repository,
        uow=uow,
        secrets=secrets_port,
        replay_cipher=cipher,
        audit=audit,
        policy=policy,
        admission=admission,
        clock=clock or _now,
    )
    if content_cipher is not None:
        kwargs["content_cipher"] = content_cipher
    return cls(**kwargs)


class _Admission:
    def __init__(
        self,
        error: Exception | None = None,
        *,
        uow: _UnitOfWork | None = None,
        on_admit=None,
    ) -> None:
        self.error = error
        self.uow = uow
        self.on_admit = on_admit
        self.calls: list[dict] = []

    def admit(self, **kwargs) -> None:
        if self.uow is not None:
            assert self.uow.is_active is False
        self.calls.append(kwargs)
        if self.on_admit is not None:
            self.on_admit()
        if self.error is not None:
            raise self.error


def _create_command(*, now: datetime = _now(), suffix: str = "one"):
    return CreatePublicConversationCommand(
        url_slug="public-chatbot",
        idempotency_key_hash=_hash(f"create-key-{suffix}"),
        request_fingerprint=_hash("create-request"),
        now=now,
    )


def _lifecycle_command(
    token: str,
    *,
    now: datetime = _now(),
    suffix: str = "one",
    expected_revision: int = 1,
):
    return LifecycleCommand(
        url_slug="public-chatbot",
        access_token=token,
        idempotency_key_hash=_hash(f"lifecycle-key-{suffix}"),
        request_fingerprint=_hash("empty-json"),
        expected_lifecycle_revision=expected_revision,
        now=now,
    )


def test_create_replays_the_same_bounded_access_token_without_storing_raw_value():
    components = _application()
    repository = components[0]
    use_case = _use_case(CreatePublicConversationUseCase, components)
    command = _create_command()

    first = use_case.execute(command)
    replay = use_case.execute(command)

    assert replay.replayed is True
    assert replay.access_token == first.access_token
    assert all(
        "token" not in grant.__dataclass_fields__
        for grant in repository.grants.values()
    )
    assert all(
        first.access_token.encode("utf-8") not in value.ciphertext
        for value in repository.replays.values()
    )
    record = next(iter(repository.idempotency.values()))
    assert record.retention_expires_at == _now() + timedelta(hours=24)


def test_expired_idempotency_record_is_not_replayed_or_left_as_a_unique_claim():
    components = _application()
    repository = components[0]
    current_time = [_now()]
    create = _use_case(
        CreatePublicConversationUseCase,
        components,
        clock=lambda: current_time[0],
    )
    command = _create_command(suffix="expired-idempotency")
    first = create.execute(command)
    current_time[0] = _now() + timedelta(hours=24, seconds=1)

    replacement = create.execute(replace(command, now=current_time[0]))

    assert replacement.replayed is False
    assert replacement.access_token != first.access_token
    assert len(repository.sessions) == 2
    assert len(repository.idempotency) == 1
    record = next(iter(repository.idempotency.values()))
    assert record.created_at == current_time[0]


def test_previous_capability_key_remains_usable_after_bounded_rotation():
    previous_key = secrets.token_bytes(32)
    components = list(_application())
    components[2] = HmacPublicSecretIssuer(previous_key, key_version="cap-v1")
    previous_components = tuple(components)
    created = _use_case(
        CreatePublicConversationUseCase,
        previous_components,
    ).execute(_create_command())
    components[2] = HmacPublicSecretIssuer(
        {
            "cap-v2": secrets.token_bytes(32),
            "cap-v1": previous_key,
        },
        primary_key_version="cap-v2",
    )

    closed = _use_case(
        ClosePublicConversationUseCase,
        tuple(components),
    ).execute(_lifecycle_command(created.access_token, suffix="rotated-close"))

    assert closed.lifecycle.value == "closed"


def test_previous_purge_receipt_remains_usable_after_bounded_rotation():
    previous_key = secrets.token_bytes(32)
    components = list(_application())
    components[2] = HmacPublicSecretIssuer(previous_key, key_version="cap-v1")
    previous_components = tuple(components)
    created = _use_case(
        CreatePublicConversationUseCase,
        previous_components,
    ).execute(_create_command(suffix="receipt-rotation"))
    deleted = _use_case(
        DeletePublicConversationUseCase,
        previous_components,
    ).execute(
        _lifecycle_command(
            created.access_token,
            suffix="receipt-rotation",
        )
    )
    components[2] = HmacPublicSecretIssuer(
        {
            "cap-v2": secrets.token_bytes(32),
            "cap-v1": previous_key,
        },
        primary_key_version="cap-v2",
    )

    status = _use_case(
        GetPublicPurgeStatusUseCase,
        tuple(components),
    ).execute(
        url_slug="public-chatbot",
        purge_receipt=deleted.purge_receipt,
        now=_now(),
    )

    assert status.status.value == "pending"


def test_create_replay_restores_the_initial_response_after_the_session_closes():
    components = _application()
    create = _use_case(CreatePublicConversationUseCase, components)
    close = _use_case(ClosePublicConversationUseCase, components)
    command = _create_command()
    first = create.execute(command)
    close.execute(
        _lifecycle_command(first.access_token, suffix="close-before-create-replay")
    )

    replay = create.execute(command)

    assert replay.replayed is True
    assert (
        replay.lifecycle,
        replay.lifecycle_revision,
        replay.memory_contract_version,
        replay.expires_at,
    ) == (
        first.lifecycle,
        first.lifecycle_revision,
        first.memory_contract_version,
        first.expires_at,
    )


def test_public_results_report_the_earliest_access_expiry_and_replay_it_stably():
    policy = replace(
        PublicConversationPolicy(),
        idle_lifetime=timedelta(hours=12),
        access_grant_lifetime=timedelta(hours=18),
    )
    components = _application(policy=policy)
    create = _use_case(CreatePublicConversationUseCase, components)
    reset = _use_case(ResetPublicConversationUseCase, components)
    close = _use_case(ClosePublicConversationUseCase, components)
    transcript = _use_case(GetPublicTranscriptUseCase, components)
    create_command = _create_command()

    created = create.execute(create_command)
    created_replay = create.execute(create_command)
    reset_command = _lifecycle_command(created.access_token, suffix="expiry-reset")
    replacement = reset.execute(reset_command)
    close_command = _lifecycle_command(
        replacement.access_token,
        suffix="expiry-close",
    )
    closed = close.execute(close_command)
    replacement_replay = reset.execute(reset_command)
    closed_replay = close.execute(close_command)
    visible = transcript.execute(
        url_slug="public-chatbot",
        access_token=replacement.access_token,
        now=_now(),
    )

    expected_expiry = _now() + timedelta(hours=12)
    assert created.expires_at == expected_expiry
    assert created_replay.expires_at == expected_expiry
    assert replacement.expires_at == expected_expiry
    assert replacement_replay.expires_at == expected_expiry
    assert closed.expires_at == expected_expiry
    assert closed_replay.expires_at == expected_expiry
    assert visible.expires_at == expected_expiry
    assert visible.content_revision == 0
    assert visible.turns == ()


@pytest.mark.parametrize(
    ("field", "replacement_value"),
    (
        ("scope_digest", "d" * 64),
        ("idempotency_key_hash", "e" * 64),
        ("request_fingerprint", "f" * 64),
    ),
)
def test_secret_replay_ciphertext_is_bound_to_immutable_idempotency_identity(
    field: str,
    replacement_value: str,
):
    now = _now()
    record = ConversationIdempotency.pending(
        record_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        operation="conversation.create",
        scope_digest=_hash("scope"),
        idempotency_key_hash=_hash("idempotency-key"),
        request_fingerprint=_hash("request"),
        retention_expires_at=now + timedelta(days=1),
        now=now,
    )
    cipher = _Cipher()
    purpose = "access-grant"
    associated_data_digest = _secret_replay_associated_data_digest(
        record=record,
        purpose=purpose,
    )
    encrypted = cipher.encrypt(
        "bounded-secret",
        associated_data_digest=associated_data_digest,
    )
    retargeted_record = replace(record, **{field: replacement_value})

    assert (
        cipher.decrypt(
            encrypted.ciphertext,
            key_version=encrypted.key_version,
            associated_data_digest=_secret_replay_associated_data_digest(
                record=retargeted_record,
                purpose=purpose,
            ),
        )
        is None
    )


def test_secret_replay_rejects_a_stored_associated_data_digest_mismatch():
    components = _application()
    repository = components[0]
    use_case = _use_case(CreatePublicConversationUseCase, components)
    command = _create_command()
    use_case.execute(command)
    replay = next(iter(repository.replays.values()))
    tampered_digest = "f" * 64
    if replay.associated_data_digest == tampered_digest:
        tampered_digest = "e" * 64
    repository.replays[replay.id] = replace(
        replay,
        associated_data_digest=tampered_digest,
    )

    with pytest.raises(MemoryAdapterUnavailableError):
        use_case.execute(command)


def test_close_makes_grant_transcript_only_and_does_not_duplicate_audit_event():
    components = _application()
    create = _use_case(CreatePublicConversationUseCase, components)
    close = _use_case(ClosePublicConversationUseCase, components)
    transcript = _use_case(GetPublicTranscriptUseCase, components)
    reset = _use_case(ResetPublicConversationUseCase, components)
    first = create.execute(_create_command())
    command = _lifecycle_command(first.access_token)

    closed = close.execute(command)
    replay = close.execute(command)
    visible = transcript.execute(
        url_slug="public-chatbot", access_token=first.access_token, now=_now()
    )

    assert closed.lifecycle.value == "closed"
    assert replay.replayed is True
    assert visible.turns == ()
    with pytest.raises(AccessGrantNotUsableError):
        reset.execute(_lifecycle_command(first.access_token, suffix="different"))
    assert [event["action"] for event in components[4].events] == [
        "memory.session.created",
        "memory.grant.issued",
        "memory.session.closed",
    ]


def test_close_replay_restores_the_closed_response_after_privacy_delete():
    components = _application()
    create = _use_case(CreatePublicConversationUseCase, components)
    close = _use_case(ClosePublicConversationUseCase, components)
    delete = _use_case(DeletePublicConversationUseCase, components)
    first = create.execute(_create_command())
    close_command = _lifecycle_command(first.access_token, suffix="close-snapshot")
    closed = close.execute(close_command)
    delete.execute(
        _lifecycle_command(
            first.access_token,
            suffix="delete-after-close-snapshot",
            expected_revision=closed.lifecycle_revision,
        )
    )

    replay = close.execute(close_command)

    assert replay.replayed is True
    assert (
        replay.lifecycle,
        replay.lifecycle_revision,
        replay.memory_contract_version,
        replay.expires_at,
    ) == (
        closed.lifecycle,
        closed.lifecycle_revision,
        closed.memory_contract_version,
        closed.expires_at,
    )


def test_same_lifecycle_key_with_a_different_precondition_fingerprint_conflicts():
    components = _application()
    create = _use_case(CreatePublicConversationUseCase, components)
    close = _use_case(ClosePublicConversationUseCase, components)
    first = create.execute(_create_command())
    command = _lifecycle_command(first.access_token)

    close.execute(command)

    with pytest.raises(DuplicateRequestConflictError):
        close.execute(
            replace(
                command,
                expected_lifecycle_revision=2,
                request_fingerprint=_hash("empty-json-with-revision-2"),
            )
        )


def test_reset_revokes_old_grant_but_matching_retry_returns_one_replacement():
    components = _application()
    create = _use_case(CreatePublicConversationUseCase, components)
    reset = _use_case(ResetPublicConversationUseCase, components)
    first = create.execute(_create_command())
    command = _lifecycle_command(first.access_token)

    replacement = reset.execute(command)
    replay = reset.execute(command)

    assert replacement.access_token != first.access_token
    assert replacement.previous_lifecycle_revision == 2
    assert replay.replayed is True
    assert replay.access_token == replacement.access_token
    assert replay.previous_lifecycle_revision == 2
    with pytest.raises(AccessGrantNotUsableError):
        reset.execute(_lifecycle_command(first.access_token, suffix="other"))
    assert [event["action"] for event in components[4].events] == [
        "memory.session.created",
        "memory.grant.issued",
        "memory.session.reset",
        "memory.grant.revoked",
        "memory.session.created",
        "memory.grant.issued",
    ]
    assert [event["target_type"] for event in components[4].events] == [
        "conversation_session",
        "conversation_access_grant",
        "conversation_session",
        "conversation_access_grant",
        "conversation_session",
        "conversation_access_grant",
    ]
    assert components[4].events[2]["target_id"] != components[4].events[4]["target_id"]


def test_reset_replay_restores_the_initial_replacement_response_after_close():
    components = _application()
    create = _use_case(CreatePublicConversationUseCase, components)
    reset = _use_case(ResetPublicConversationUseCase, components)
    close = _use_case(ClosePublicConversationUseCase, components)
    first = create.execute(_create_command())
    reset_command = _lifecycle_command(first.access_token, suffix="reset-snapshot")
    replacement = reset.execute(reset_command)
    close.execute(
        _lifecycle_command(
            replacement.access_token,
            suffix="close-replacement",
            expected_revision=replacement.lifecycle_revision,
        )
    )

    replay = reset.execute(reset_command)

    assert replay.replayed is True
    assert replay.access_token == replacement.access_token
    assert (
        replay.lifecycle,
        replay.lifecycle_revision,
        replay.memory_contract_version,
        replay.expires_at,
        replay.previous_lifecycle,
        replay.previous_lifecycle_revision,
    ) == (
        replacement.lifecycle,
        replacement.lifecycle_revision,
        replacement.memory_contract_version,
        replacement.expires_at,
        replacement.previous_lifecycle,
        replacement.previous_lifecycle_revision,
    )


def test_close_replays_after_original_grant_and_session_expire_but_new_key_fails():
    policy = replace(
        PublicConversationPolicy(),
        idle_lifetime=timedelta(minutes=1),
        access_grant_lifetime=timedelta(minutes=1),
    )
    components = _application(policy=policy)
    repository = components[0]
    admission = _Admission()
    create = _use_case(CreatePublicConversationUseCase, components)
    close = _use_case(
        ClosePublicConversationUseCase,
        components,
        admission=admission,
    )
    first = create.execute(_create_command())
    command = _lifecycle_command(first.access_token)
    closed = close.execute(command)

    replay = close.execute(replace(command, now=_now() + timedelta(minutes=2)))

    assert replay.replayed is True
    assert replay.lifecycle_revision == closed.lifecycle_revision
    record_count = len(repository.idempotency)
    with pytest.raises(AccessGrantNotUsableError):
        close.execute(
            _lifecycle_command(
                first.access_token,
                now=_now() + timedelta(minutes=2),
                suffix="new-after-expiry",
                expected_revision=2,
            )
        )
    assert len(repository.idempotency) == record_count
    assert [call["disposition"].value for call in admission.calls] == [
        "logical_request",
        "exact_retry",
    ]


def test_reset_replays_replacement_secret_after_original_scope_expires():
    policy = replace(
        PublicConversationPolicy(),
        idle_lifetime=timedelta(minutes=1),
        access_grant_lifetime=timedelta(minutes=1),
    )
    components = _application(policy=policy)
    create = _use_case(CreatePublicConversationUseCase, components)
    reset = _use_case(ResetPublicConversationUseCase, components)
    first = create.execute(_create_command())
    command = _lifecycle_command(first.access_token)
    replacement = reset.execute(command)

    replay = reset.execute(replace(command, now=_now() + timedelta(minutes=2)))

    assert replay.replayed is True
    assert replay.access_token == replacement.access_token


def test_delete_replays_receipt_after_original_scope_expires():
    policy = replace(
        PublicConversationPolicy(),
        idle_lifetime=timedelta(minutes=1),
        access_grant_lifetime=timedelta(minutes=1),
    )
    components = _application(policy=policy)
    create = _use_case(CreatePublicConversationUseCase, components)
    delete = _use_case(DeletePublicConversationUseCase, components)
    first = create.execute(_create_command())
    command = _lifecycle_command(first.access_token)
    deleted = delete.execute(command)

    replay = delete.execute(replace(command, now=_now() + timedelta(minutes=2)))

    assert replay.replayed is True
    assert replay.purge_receipt == deleted.purge_receipt


@pytest.mark.parametrize(
    "blocking_call",
    ("lock_purge_job_by_id", "get_secret_replay"),
)
def test_delete_replay_revalidates_expiry_after_each_replay_row_lock(
    blocking_call: str,
):
    components = _application()
    repository = components[0]
    current_time = [_now()]
    create = _use_case(
        CreatePublicConversationUseCase,
        components,
        clock=lambda: current_time[0],
    )
    delete = _use_case(
        DeletePublicConversationUseCase,
        components,
        clock=lambda: current_time[0],
    )
    created = create.execute(_create_command(suffix=f"delete-{blocking_call}"))
    command = _lifecycle_command(
        created.access_token,
        suffix=f"delete-{blocking_call}",
    )
    delete.execute(command)
    record = next(
        record
        for record in repository.idempotency.values()
        if record.operation == "conversation.delete"
    )
    assert record.secret_replay_expires_at is not None
    replay_boundary = min(
        record.retention_expires_at,
        record.secret_replay_expires_at,
    )
    current_time[0] = replay_boundary - timedelta(microseconds=1)
    original_call = getattr(repository, blocking_call)

    def complete_lock_wait(**kwargs):
        result = original_call(**kwargs)
        current_time[0] = replay_boundary
        return result

    setattr(repository, blocking_call, complete_lock_wait)

    with pytest.raises(SecretReplayExpiredError):
        delete.execute(replace(command, now=current_time[0]))


def test_access_grant_replay_revalidates_expiry_after_secret_row_lock():
    components = _application()
    repository = components[0]
    current_time = [_now()]
    create = _use_case(
        CreatePublicConversationUseCase,
        components,
        clock=lambda: current_time[0],
    )
    command = _create_command(suffix="create-replay-lock-wait")
    create.execute(command)
    record = next(
        record
        for record in repository.idempotency.values()
        if record.operation == "conversation.create"
    )
    assert record.secret_replay_expires_at is not None
    replay_boundary = record.secret_replay_expires_at
    current_time[0] = replay_boundary - timedelta(microseconds=1)
    original_get_secret_replay = repository.get_secret_replay

    def get_secret_replay_after_wait(**kwargs):
        replay = original_get_secret_replay(**kwargs)
        current_time[0] = replay_boundary
        return replay

    repository.get_secret_replay = get_secret_replay_after_wait

    with pytest.raises(SecretReplayExpiredError):
        create.execute(replace(command, now=current_time[0]))


def test_delete_replay_restores_the_initial_revision_after_terminal_progress():
    components = _application()
    repository = components[0]
    create = _use_case(CreatePublicConversationUseCase, components)
    delete = _use_case(DeletePublicConversationUseCase, components)
    first = create.execute(_create_command())
    command = _lifecycle_command(first.access_token, suffix="delete-snapshot")
    deleted = delete.execute(command)
    session = next(iter(repository.sessions.values()))
    session.mark_deleted(
        expected_lifecycle_revision=deleted.lifecycle_revision,
        now=_now() + timedelta(minutes=1),
    )

    replay = delete.execute(command)

    assert replay.replayed is True
    assert replay.lifecycle == deleted.lifecycle
    assert replay.lifecycle_revision == deleted.lifecycle_revision
    assert replay.purge_job_id == deleted.purge_job_id
    assert replay.purge_receipt == deleted.purge_receipt


def test_delete_replays_after_physical_purge_removes_grant_and_session_rows():
    components = _application()
    repository = components[0]
    create = _use_case(CreatePublicConversationUseCase, components)
    admission = _Admission()
    delete = _use_case(DeletePublicConversationUseCase, components, admission=admission)
    created = create.execute(_create_command(suffix="purged-delete-replay"))
    command = _lifecycle_command(
        created.access_token,
        suffix="purged-delete-replay",
    )
    deleted = delete.execute(command)
    record = next(
        record
        for record in repository.idempotency.values()
        if record.operation == "conversation.delete"
    )

    repository.sessions.clear()
    repository.grants.clear()
    replay = delete.execute(command)

    assert replay.replayed is True
    assert replay.purge_job_id == deleted.purge_job_id
    assert replay.purge_receipt == deleted.purge_receipt
    assert record.authorization_app_id == repository.binding.app_id
    assert record.authorization_verifier_hash != created.access_token
    assert [call["disposition"].value for call in admission.calls] == [
        "logical_request",
        "exact_retry",
    ]
    with pytest.raises(AccessGrantNotUsableError):
        delete.execute(
            replace(
                command,
                access_token=components[2].issue_access_grant().raw_value,
            )
        )
    with pytest.raises(AccessGrantNotUsableError):
        delete.execute(
            replace(
                command,
                request_fingerprint=_hash("different-delete-fingerprint"),
            )
        )


def test_delete_revokes_conversation_grant_and_replays_receipt_only_until_ttl():
    components = _application(
        policy=replace(
            PublicConversationPolicy(),
            idle_lifetime=timedelta(days=2),
            access_grant_lifetime=timedelta(days=2),
        )
    )
    repository = components[0]
    create = _use_case(CreatePublicConversationUseCase, components)
    delete = _use_case(DeletePublicConversationUseCase, components)
    purge_status = _use_case(GetPublicPurgeStatusUseCase, components)
    first = create.execute(_create_command())
    command = _lifecycle_command(first.access_token)

    deleted = delete.execute(command)
    replay = delete.execute(command)
    status = purge_status.execute(
        url_slug="public-chatbot", purge_receipt=deleted.purge_receipt, now=_now()
    )

    assert replay.replayed is True
    assert replay.purge_receipt == deleted.purge_receipt
    assert status.status.value == "pending"
    with pytest.raises(AccessGrantNotUsableError):
        delete.execute(_lifecycle_command(first.access_token, suffix="other"))
    # The 24-hour idempotency tombstone and replay expire together for delete.
    with pytest.raises(AccessGrantNotUsableError):
        delete.execute(
            _lifecycle_command(
                first.access_token,
                now=_now() + timedelta(hours=24),
            )
        )
    purge_job = repository.purge_jobs[deleted.purge_job_id]
    repository.sessions.pop(purge_job.session_id)
    purge_job.session_id = None
    status_after_physical_session_delete = purge_status.execute(
        url_slug="public-chatbot",
        purge_receipt=deleted.purge_receipt,
        now=_now(),
    )
    assert status_after_physical_session_delete.status.value == "pending"
    assert [event["action"] for event in components[4].events] == [
        "memory.session.created",
        "memory.grant.issued",
        "memory.session.delete_requested",
        "memory.grant.revoked",
    ]
    assert [event["target_type"] for event in components[4].events[-2:]] == [
        "conversation_session",
        "conversation_access_grant",
    ]


def test_purge_status_survives_redeployment_but_rejects_slug_reassignment():
    components = _application()
    repository = components[0]
    create = _use_case(CreatePublicConversationUseCase, components)
    delete = _use_case(DeletePublicConversationUseCase, components)
    purge_status = _use_case(GetPublicPurgeStatusUseCase, components)
    created = create.execute(_create_command(suffix="purge-redeploy"))
    deleted = delete.execute(
        _lifecycle_command(created.access_token, suffix="purge-redeploy")
    )
    original_binding = repository.binding
    repository.binding = replace(
        original_binding,
        deployment_id=uuid.uuid4(),
        deployment_version=original_binding.deployment_version + 1,
    )

    status = purge_status.execute(
        url_slug="public-chatbot",
        purge_receipt=deleted.purge_receipt,
        now=_now(),
    )

    assert status.status.value == "pending"
    repository.binding = replace(repository.binding, app_id=uuid.uuid4())
    with pytest.raises(PurgeReceiptNotUsableError):
        purge_status.execute(
            url_slug="public-chatbot",
            purge_receipt=deleted.purge_receipt,
            now=_now(),
        )


def test_closed_conversation_can_request_privacy_delete_but_cannot_reset():
    components = _application()
    create = _use_case(CreatePublicConversationUseCase, components)
    close = _use_case(ClosePublicConversationUseCase, components)
    reset = _use_case(ResetPublicConversationUseCase, components)
    delete = _use_case(DeletePublicConversationUseCase, components)
    first = create.execute(_create_command())
    close.execute(_lifecycle_command(first.access_token, suffix="close"))

    with pytest.raises(AccessGrantNotUsableError):
        reset.execute(
            _lifecycle_command(
                first.access_token,
                suffix="reset-after-close",
                expected_revision=2,
            )
        )

    result = delete.execute(
        _lifecycle_command(
            first.access_token,
            suffix="delete-after-close",
            expected_revision=2,
        )
    )

    assert result.lifecycle.value == "delete_pending"


def test_mutation_admission_separates_primary_request_from_exact_retry():
    components = _application()
    admission = _Admission()
    create = _use_case(
        CreatePublicConversationUseCase,
        components,
        admission=admission,
    )
    command = _create_command()

    create.execute(command)
    create.execute(command)

    assert len(admission.calls) == 2
    assert admission.calls[0]["operation"] == "conversation.create"
    assert admission.calls[0]["request_key_hash"] == command.idempotency_key_hash
    assert admission.calls[0]["request_fingerprint"] == command.request_fingerprint
    assert admission.calls[0]["disposition"].value == "logical_request"
    assert admission.calls[1]["disposition"].value == "exact_retry"


def test_create_rejects_deployment_switch_after_admission():
    components = _application()
    repository = components[0]
    original_binding = repository.binding

    def switch_deployment() -> None:
        repository.binding = replace(
            original_binding,
            deployment_id=uuid.uuid4(),
            deployment_version=original_binding.deployment_version + 1,
        )

    admission = _Admission(on_admit=switch_deployment)
    create = _use_case(
        CreatePublicConversationUseCase,
        components,
        admission=admission,
    )

    with pytest.raises(AccessGrantNotUsableError):
        create.execute(_create_command(suffix="deployment-switch"))

    assert repository.sessions == {}
    assert repository.grants == {}
    assert repository.idempotency == {}


def test_mutation_admission_runs_after_database_preflight_releases_locks():
    components = _application()
    admission = _Admission(uow=components[1])
    create = _use_case(
        CreatePublicConversationUseCase,
        components,
        admission=admission,
    )

    create.execute(_create_command())

    assert len(admission.calls) == 1


def test_lifecycle_admission_runs_after_grant_and_session_preflight_releases_locks():
    components = _application()
    created = _use_case(
        CreatePublicConversationUseCase,
        components,
    ).execute(_create_command())
    admission = _Admission(uow=components[1])
    close = _use_case(
        ClosePublicConversationUseCase,
        components,
        admission=admission,
    )

    close.execute(_lifecycle_command(created.access_token, suffix="unlocked-close"))

    assert len(admission.calls) == 1


def test_lifecycle_revalidates_revocation_after_external_admission():
    components = _application()
    repository = components[0]
    created = _use_case(
        CreatePublicConversationUseCase,
        components,
    ).execute(_create_command())
    grant = next(iter(repository.grants.values()))
    admission = _Admission(on_admit=lambda: grant.revoke(now=_now()))
    close = _use_case(
        ClosePublicConversationUseCase,
        components,
        admission=admission,
    )

    with pytest.raises(AccessGrantNotUsableError):
        close.execute(_lifecycle_command(created.access_token, suffix="revoked-close"))

    assert next(iter(repository.sessions.values())).lifecycle.value == "active"


@pytest.mark.parametrize(
    "use_case_type",
    (
        ClosePublicConversationUseCase,
        ResetPublicConversationUseCase,
        DeletePublicConversationUseCase,
    ),
)
def test_lifecycle_revalidates_expiry_using_fresh_time_after_admission(
    use_case_type,
):
    policy = replace(
        PublicConversationPolicy(),
        idle_lifetime=timedelta(minutes=1),
        access_grant_lifetime=timedelta(minutes=1),
    )
    components = _application(policy=policy)
    created = _use_case(CreatePublicConversationUseCase, components).execute(
        _create_command(suffix=f"fresh-clock-{use_case_type.__name__}")
    )
    current_time = [_now()]
    admission = _Admission(
        on_admit=lambda: current_time.__setitem__(
            0,
            _now() + timedelta(minutes=1),
        )
    )
    use_case = _use_case(
        use_case_type,
        components,
        admission=admission,
        clock=lambda: current_time[0],
    )

    with pytest.raises(AccessGrantNotUsableError):
        use_case.execute(
            _lifecycle_command(
                created.access_token,
                suffix=f"fresh-clock-{use_case_type.__name__}",
            )
        )

    assert next(iter(components[0].sessions.values())).lifecycle.value == "active"
    assert len(admission.calls) == 1


@pytest.mark.parametrize(
    "use_case_type",
    (
        ClosePublicConversationUseCase,
        ResetPublicConversationUseCase,
        DeletePublicConversationUseCase,
    ),
)
def test_lifecycle_revalidates_expiry_after_waiting_for_mutation_row_locks(
    use_case_type,
):
    policy = replace(
        PublicConversationPolicy(),
        idle_lifetime=timedelta(minutes=1),
        access_grant_lifetime=timedelta(minutes=1),
    )
    components = _application(policy=policy)
    repository = components[0]
    created = _use_case(CreatePublicConversationUseCase, components).execute(
        _create_command(suffix=f"lock-clock-{use_case_type.__name__}")
    )
    current_time = [_now()]
    original_lock = repository.lock_session

    def lock_after_wait(*, organization_id: uuid.UUID, session_id: uuid.UUID):
        session = original_lock(
            organization_id=organization_id,
            session_id=session_id,
        )
        current_time[0] = _now() + timedelta(minutes=1)
        return session

    repository.lock_session = lock_after_wait
    use_case = _use_case(
        use_case_type,
        components,
        clock=lambda: current_time[0],
    )

    with pytest.raises(AccessGrantNotUsableError):
        use_case.execute(
            _lifecycle_command(
                created.access_token,
                suffix=f"lock-clock-{use_case_type.__name__}",
            )
        )

    assert next(iter(repository.sessions.values())).lifecycle.value == "active"


def test_admission_unavailable_rolls_back_pending_create_idempotency_record():
    components = _application()
    repository = components[0]
    create = _use_case(
        CreatePublicConversationUseCase,
        components,
        admission=_Admission(MemoryAdapterUnavailableError()),
    )

    with pytest.raises(MemoryAdapterUnavailableError):
        create.execute(_create_command())

    assert repository.sessions == {}
    assert repository.idempotency == {}


def _content_cipher() -> FernetMemoryContentCipher:
    return FernetMemoryContentCipher(
        Fernet.generate_key(),
        digest_hmac_key=secrets.token_bytes(32),
    )


def _terminal_transcript_source(
    *,
    repository: _Repository,
    cipher: FernetMemoryContentCipher,
    sequence: int,
    status: TurnStatus = TurnStatus.COMPLETED,
) -> PublicTranscriptTurnSource:
    session = next(iter(repository.sessions.values()))
    turn_id = uuid.uuid4()
    user_entry_id = uuid.uuid4()
    user_content = ProtectedEntryContent(
        display=cipher.protect(
            f"public user {sequence}",
            associated_data=memory_content_aad(
                organization_id=session.organization_id,
                session_id=session.id,
                turn_id=turn_id,
                entry_id=user_entry_id,
                projection="display",
            ),
        ),
        model=cipher.protect(
            f"raw user {sequence}",
            associated_data=memory_content_aad(
                organization_id=session.organization_id,
                session_id=session.id,
                turn_id=turn_id,
                entry_id=user_entry_id,
                projection="model",
            ),
        ),
    )
    user_entry = ConversationMemoryEntry.provisional_user(
        entry_id=user_entry_id,
        organization_id=session.organization_id,
        session_id=session.id,
        turn_id=turn_id,
        sequence=sequence * 2 - 1,
        channel="conversation",
        content=user_content,
        idempotency_key_hash=f"{sequence:064x}",
        now=_now(),
    )
    user_entry.approve(content_revision=sequence, now=_now())
    turn = ConversationTurn.start(
        turn_id=turn_id,
        organization_id=session.organization_id,
        session_id=session.id,
        sequence=sequence,
        started_lifecycle_revision=session.lifecycle_revision,
        request_identity=RequestIdentity(
            idempotency_key_hash=f"{sequence:064x}",
            request_fingerprint=f"{sequence + 1000:064x}",
        ),
        user_entry_id=user_entry_id,
        dispatch_id=uuid.uuid4(),
        now=_now(),
    )
    if status is not TurnStatus.COMPLETED:
        turn.fail(
            expected_version=turn.version,
            safe_reason_code="memory.safe_terminal_failure",
            now=_now(),
        )
        return PublicTranscriptTurnSource(
            turn=turn,
            user_entry=user_entry,
            assistant_entry=None,
        )
    assistant_entry_id = uuid.uuid4()
    assistant_entry = ConversationMemoryEntry.approved_assistant(
        entry_id=assistant_entry_id,
        organization_id=session.organization_id,
        session_id=session.id,
        turn_id=turn_id,
        sequence=sequence * 2,
        channel="conversation",
        content=ProtectedEntryContent(
            display=cipher.protect(
                f"public assistant {sequence}",
                associated_data=memory_content_aad(
                    organization_id=session.organization_id,
                    session_id=session.id,
                    turn_id=turn_id,
                    entry_id=assistant_entry_id,
                    projection="display",
                ),
            ),
            model=cipher.protect(
                f"raw assistant {sequence}",
                associated_data=memory_content_aad(
                    organization_id=session.organization_id,
                    session_id=session.id,
                    turn_id=turn_id,
                    entry_id=assistant_entry_id,
                    projection="model",
                ),
            ),
        ),
        content_revision=sequence,
        idempotency_key_hash=f"{sequence + 2000:064x}",
        now=_now(),
    )
    turn.mark_queued(expected_version=turn.version, now=_now())
    turn.mark_running(
        expected_version=turn.version,
        execution_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        now=_now(),
    )
    turn.complete(
        expected_version=turn.version,
        assistant_entry_id=assistant_entry_id,
        now=_now(),
    )
    return PublicTranscriptTurnSource(
        turn=turn,
        user_entry=user_entry,
        assistant_entry=assistant_entry,
    )


def test_transcript_projects_only_aad_bound_display_content_and_safe_failures():
    components = _application()
    repository = components[0]
    created = _use_case(CreatePublicConversationUseCase, components).execute(
        _create_command(suffix="transcript-content")
    )
    cipher = _content_cipher()
    repository.transcript_rows.extend(
        (
            _terminal_transcript_source(
                repository=repository,
                cipher=cipher,
                sequence=1,
            ),
            _terminal_transcript_source(
                repository=repository,
                cipher=cipher,
                sequence=2,
                status=TurnStatus.FAILED,
            ),
        )
    )
    transcript = _use_case(
        GetPublicTranscriptUseCase,
        components,
        content_cipher=cipher,
    )

    result = transcript.execute(
        url_slug="public-chatbot",
        access_token=created.access_token,
        now=_now(),
    )

    completed, failed = result.turns
    assert completed.user_content == "public user 1"
    assert completed.assistant_content == "public assistant 1"
    assert completed.safe_failure_reason is None
    assert failed.state is TurnStatus.FAILED
    assert failed.user_content is None
    assert failed.assistant_content is None
    assert failed.safe_failure_reason == "memory.safe_terminal_failure"
    assert "raw user" not in repr(result)
    assert "raw assistant" not in repr(result)


def test_transcript_is_stably_paginated_with_an_opaque_cursor():
    components = _application()
    repository = components[0]
    created = _use_case(CreatePublicConversationUseCase, components).execute(
        _create_command(suffix="transcript-page")
    )
    cipher = _content_cipher()
    repository.transcript_rows.extend(
        _terminal_transcript_source(
            repository=repository,
            cipher=cipher,
            sequence=sequence,
            status=TurnStatus.FAILED,
        )
        for sequence in range(1, 52)
    )
    transcript = _use_case(
        GetPublicTranscriptUseCase,
        components,
        content_cipher=cipher,
    )

    first = transcript.execute(
        url_slug="public-chatbot",
        access_token=created.access_token,
        now=_now(),
    )
    second = transcript.execute(
        url_slug="public-chatbot",
        access_token=created.access_token,
        cursor=first.next_cursor,
        now=_now(),
    )

    assert [turn.sequence for turn in first.turns] == list(range(1, 51))
    assert first.next_cursor is not None
    assert "50" not in first.next_cursor
    assert [turn.sequence for turn in second.turns] == [51]
    assert second.next_cursor is None


def test_transcript_fails_closed_when_completed_display_ciphertext_is_tampered():
    components = _application()
    repository = components[0]
    created = _use_case(CreatePublicConversationUseCase, components).execute(
        _create_command(suffix="transcript-tamper")
    )
    cipher = _content_cipher()
    source = _terminal_transcript_source(
        repository=repository,
        cipher=cipher,
        sequence=1,
    )
    assert source.assistant_entry is not None
    assert source.assistant_entry.content is not None
    source.assistant_entry.content = replace(
        source.assistant_entry.content,
        display=replace(
            source.assistant_entry.content.display,
            ciphertext=b"tampered",
        ),
    )
    repository.transcript_rows.append(source)
    transcript = _use_case(
        GetPublicTranscriptUseCase,
        components,
        content_cipher=cipher,
    )

    with pytest.raises(MemoryAdapterUnavailableError):
        transcript.execute(
            url_slug="public-chatbot",
            access_token=created.access_token,
            now=_now(),
        )


def test_transcript_cursor_cannot_be_reused_by_another_authorized_session():
    components = _application()
    repository = components[0]
    create = _use_case(CreatePublicConversationUseCase, components)
    first_session = create.execute(_create_command(suffix="cursor-session-one"))
    cipher = _content_cipher()
    repository.transcript_rows.extend(
        _terminal_transcript_source(
            repository=repository,
            cipher=cipher,
            sequence=sequence,
            status=TurnStatus.FAILED,
        )
        for sequence in range(1, 52)
    )
    transcript = _use_case(
        GetPublicTranscriptUseCase,
        components,
        content_cipher=cipher,
    )
    first_page = transcript.execute(
        url_slug="public-chatbot",
        access_token=first_session.access_token,
        now=_now(),
    )
    second_session = create.execute(_create_command(suffix="cursor-session-two"))

    with pytest.raises(AccessGrantNotUsableError):
        transcript.execute(
            url_slug="public-chatbot",
            access_token=second_session.access_token,
            cursor=first_page.next_cursor,
            now=_now(),
        )
