from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.memory.application.public_lifecycle import (
    PublicConversationAdmissionDisposition,
    PublicDeploymentBinding,
)
from apps.memory.application.public_runtime import (
    GetPublicTurnStatusUseCase,
    StartPublicConversationTurnCommand,
    StartPublicConversationTurnUseCase,
)
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationMemoryEntry,
    ConversationSession,
    DispatchStatus,
    ProtectedContent,
    ProtectedEntryContent,
    TurnStatus,
)
from apps.memory.domain.errors import (
    AccessGrantNotUsableError,
    DuplicateRequestConflictError,
    MemoryAdapterUnavailableError,
    PublicConversationFeatureDisabledError,
    PublicConversationTurnLimitExceededError,
)
from apps.memory.domain.public_access import ConversationAccessGrant


def _now() -> datetime:
    return datetime(2026, 7, 22, 13, tzinfo=timezone.utc)


def _binding(**changes) -> PublicDeploymentBinding:
    values = {
        "organization_id": uuid.uuid4(),
        "app_id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(),
        "deployment_id": uuid.uuid4(),
        "deployment_version": 1,
        "mapping_version": "conversation-mapping-v1",
        "memory_policy_version": "memory-policy-v1",
        "memory_contract_version": "conversation-memory-v1",
        "storage_generation": 1,
        "runtime_contract_ready": True,
        "runtime_start_node_id": "start",
        "runtime_input_variable": "question",
        "runtime_llm_node_id": "llm",
        "runtime_answer_node_id": "answer",
        "runtime_output_variable": "answer",
        "runtime_max_turns": 5,
        "runtime_max_context_tokens": 1200,
    }
    values.update(changes)
    return PublicDeploymentBinding(**values)


class _Repository:
    def __init__(self, binding: PublicDeploymentBinding) -> None:
        self.binding = binding
        self.session = ConversationSession.create(
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
            idle_expires_at=_now() + timedelta(hours=1),
            absolute_expires_at=_now() + timedelta(days=1),
            now=_now(),
        )
        self.grant = ConversationAccessGrant.issue(
            grant_id=uuid.uuid4(),
            organization_id=binding.organization_id,
            session_id=self.session.id,
            deployment_id=binding.deployment_id,
            deployment_version=binding.deployment_version,
            audience_kind=AudienceKind.PUBLIC_CHATBOT,
            verifier_hash="f" * 64,
            verifier_key_version="cap-v1",
            expires_at=_now() + timedelta(hours=1),
            now=_now(),
        )
        self.turns = {}
        self.entries = {}
        self.dispatches = {}
        self.now = _now()
        self.completed_turn_count = 0

    def resolve_public_deployment(self, url_slug):
        return self.binding if url_slug == "chatbot" else None

    def lock_public_deployment(self, url_slug):
        return self.resolve_public_deployment(url_slug)

    def lock_access_grant(self, *, verifier_candidates):
        return self.grant if ("cap-v1", "f" * 64) in verifier_candidates else None

    def lock_session(self, *, organization_id, session_id):
        if (
            organization_id == self.session.organization_id
            and session_id == self.session.id
        ):
            return self.session
        return None

    def save_session(self, session):
        self.session = session

    def find_turn_by_request(
        self, *, organization_id, session_id, idempotency_key_hash
    ):
        return next(
            (
                turn
                for turn in self.turns.values()
                if turn.organization_id == organization_id
                and turn.session_id == session_id
                and turn.request_identity.idempotency_key_hash == idempotency_key_hash
            ),
            None,
        )

    def count_completed_turns(self, *, organization_id, session_id):
        return self.completed_turn_count

    def add_turn(self, turn):
        self.turns[turn.id] = turn

    def save_turn(self, turn):
        self.turns[turn.id] = turn

    def lock_turn(self, *, organization_id, session_id, turn_id):
        turn = self.turns.get(turn_id)
        if (
            turn is not None
            and turn.organization_id == organization_id
            and turn.session_id == session_id
        ):
            return turn
        return None

    def add_entry(self, entry):
        self.entries[entry.id] = entry

    def save_entry(self, entry):
        self.entries[entry.id] = entry

    def get_entry(self, *, organization_id, session_id, entry_id):
        entry = self.entries.get(entry_id)
        if (
            entry is not None
            and entry.organization_id == organization_id
            and entry.session_id == session_id
        ):
            return entry
        return None

    def add_dispatch_job(self, dispatch):
        self.dispatches[dispatch.id] = dispatch

    def lock_dispatch_job(self, *, organization_id, dispatch_id):
        dispatch = self.dispatches.get(dispatch_id)
        if dispatch is not None and dispatch.organization_id == organization_id:
            return dispatch
        return None

    def save_dispatch_job(self, dispatch):
        self.dispatches[dispatch.id] = dispatch

    def current_time(self):
        return self.now


class _Uow:
    def begin(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


class _Secrets:
    def access_grant_verifiers(self, raw_value):
        return (("cap-v1", "f" * 64),) if raw_value == "token" else ()


class _Fingerprinter:
    def fingerprint(self, *, key_version=None, **kwargs):
        if key_version not in {None, "admission-v1"}:
            raise ValueError("unknown fingerprint key")
        input_text = kwargs["input_text"]
        return "admission-v1", ("a" if input_text == "hello" else "b") * 64


class _RotatingFingerprinter:
    def __init__(self):
        self.primary = "admission-v1"
        self.available = {"admission-v1", "admission-v2"}

    def fingerprint(self, *, key_version=None, **kwargs):
        version = key_version or self.primary
        if version not in self.available:
            raise ValueError("unknown fingerprint key")
        input_text = kwargs["input_text"]
        marker = "a" if input_text == "hello" else "b"
        return version, marker * 64


class _Cipher:
    def __init__(self):
        self.values = []
        self.reveals = []

    def protect(self, value, *, associated_data):
        self.values.append((value, associated_data))
        return ProtectedContent(
            ciphertext=b"encrypted",
            key_version="content-v1",
            format_version="memory-content-fernet-v1",
            content_digest="c" * 64,
            plaintext_byte_length=len(value.encode("utf-8")),
        )

    def reveal(self, protected, *, associated_data):
        self.reveals.append((protected, associated_data))
        return "Approved redacted answer"


class _Admission:
    def __init__(self, on_admit=None):
        self.calls = []
        self.on_admit = on_admit

    def admit(self, **kwargs):
        if self.on_admit is not None:
            self.on_admit()
        self.calls.append(kwargs)


class _Publisher:
    def __init__(self):
        self.calls = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)


class _FailOncePublisher(_Publisher):
    def publish(self, **kwargs):
        super().publish(**kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("publish unavailable")


class _ClaimingFailOncePublisher(_Publisher):
    def __init__(self, repository):
        super().__init__()
        self.repository = repository

    def publish(self, **kwargs):
        super().publish(**kwargs)
        if len(self.calls) == 1:
            self.repository.dispatches[kwargs["dispatch_id"]].claim(
                owner="gateway-test-owner",
                deadline=self.repository.now + timedelta(seconds=30),
                now=self.repository.now,
            )
            self.repository.dispatches[kwargs["dispatch_id"]].record_publish_failure(
                owner="gateway-test-owner",
                claim_generation=1,
                safe_reason_code="memory.dispatch_publish_failed",
                now=self.repository.now,
            )
            raise RuntimeError("definitive pre-send failure")


def _command(input_text="hello") -> StartPublicConversationTurnCommand:
    return StartPublicConversationTurnCommand(
        url_slug="chatbot",
        access_token="token",
        idempotency_key_hash="1" * 64,
        expected_lifecycle_revision=1,
        inputs={"question": input_text},
        network_address="203.0.113.10",
        now=_now(),
    )


def _use_case(
    repository,
    *,
    publisher=None,
    max_dispatch_attempts=5,
    fingerprinter=None,
):
    cipher = _Cipher()
    admission = _Admission()
    return (
        StartPublicConversationTurnUseCase(
            repository=repository,
            uow=_Uow(),
            secrets=_Secrets(),
            content_cipher=cipher,
            fingerprinter=fingerprinter or _Fingerprinter(),
            admission=admission,
            dispatch_publisher=publisher,
            minimum_worker_capability="memory-runtime-v1",
            max_dispatch_attempts=max_dispatch_attempts,
        ),
        cipher,
        admission,
    )


def test_exact_retry_terminalizes_an_exhausted_publish_failure() -> None:
    repository = _Repository(_binding())
    publisher = _ClaimingFailOncePublisher(repository)
    use_case, _cipher, _admission = _use_case(
        repository,
        publisher=publisher,
        max_dispatch_attempts=1,
    )

    with pytest.raises(RuntimeError, match="definitive pre-send failure"):
        use_case.execute(_command())

    turn = next(iter(repository.turns.values()))
    dispatch = repository.dispatches[turn.dispatch_id]
    assert dispatch.status is DispatchStatus.TERMINAL
    assert turn.status is TurnStatus.PENDING_DISPATCH

    recovered = use_case.execute(_command())

    assert recovered.turn_state is TurnStatus.FAILED
    assert recovered.dispatch_publish_required is False
    assert turn.safe_failure_reason == "memory.dispatch_publish_failed"
    assert repository.session.active_turn_id is None
    assert repository.entries[turn.user_entry_id].lifecycle.value == "rejected"
    assert len(publisher.calls) == 1
    status = GetPublicTurnStatusUseCase(
        repository=repository,
        uow=_Uow(),
        secrets=_Secrets(),
        content_cipher=_Cipher(),
    ).execute(
        url_slug="chatbot",
        turn_id=turn.id,
        access_token="token",
        now=_now(),
    )
    assert status.turn_state is TurnStatus.FAILED
    assert status.display is None
    assert status.safe_failure_reason == "memory.dispatch_publish_failed"


def test_exact_retry_terminalizes_an_expired_final_claim() -> None:
    repository = _Repository(_binding())
    use_case, _cipher, _admission = _use_case(
        repository,
        max_dispatch_attempts=1,
    )
    started = use_case.execute(_command())
    dispatch = repository.dispatches[started.dispatch_id]
    deadline = repository.now + timedelta(seconds=30)
    dispatch.claim(
        owner="dispatcher-a",
        deadline=deadline,
        now=repository.now,
    )
    repository.now = deadline

    recovered = use_case.execute(_command())
    turn = repository.turns[started.turn_id]

    assert dispatch.status is DispatchStatus.TERMINAL
    assert recovered.turn_state is TurnStatus.FAILED
    assert turn.safe_failure_reason == "memory.dispatch_claim_expired"
    assert repository.session.active_turn_id is None
    assert repository.entries[turn.user_entry_id].lifecycle.value == "rejected"


def test_public_run_starts_exactly_one_grant_bound_turn_and_dispatch() -> None:
    repository = _Repository(_binding())
    use_case, cipher, admission = _use_case(repository)

    result = use_case.execute(_command())

    turn = repository.turns[result.turn_id]
    assert result.replayed is False
    assert result.turn_state.value == "pending_dispatch"
    assert turn.access_grant_id == repository.grant.id
    assert turn.request_fingerprint_key_version == "admission-v1"
    assert repository.dispatches[result.dispatch_id].turn_id == result.turn_id
    assert {
        entry.dependency_proof_version for entry in repository.entries.values()
    } == {"conversation-source-free-v1"}
    assert [value for value, _aad in cipher.values] == ["hello", "hello"]
    assert len(admission.calls) == 1
    assert (
        admission.calls[0]["disposition"]
        is PublicConversationAdmissionDisposition.LOGICAL_REQUEST
    )


def test_dispatch_notification_is_reference_only_and_occurs_after_commit() -> None:
    repository = _Repository(_binding())
    publisher = _Publisher()
    use_case, _cipher, _admission = _use_case(
        repository,
        publisher=publisher,
    )

    result = use_case.execute(_command())

    assert publisher.calls == [
        {
            "organization_id": repository.binding.organization_id,
            "session_id": repository.session.id,
            "dispatch_id": result.dispatch_id,
            "turn_id": result.turn_id,
            "memory_contract_version": "conversation-memory-v1",
            "storage_generation": 1,
            "minimum_worker_capability": "memory-runtime-v1",
        }
    ]
    assert "input" not in publisher.calls[0]
    assert "grant" not in publisher.calls[0]


def test_same_key_and_input_replays_turn_without_second_content_write() -> None:
    repository = _Repository(_binding())
    use_case, cipher, admission = _use_case(repository)

    first = use_case.execute(_command())
    replay = use_case.execute(_command())

    assert replay.replayed is True
    assert replay.turn_id == first.turn_id
    assert len(repository.turns) == 1
    assert len(repository.dispatches) == 1
    assert len(cipher.values) == 2
    assert (
        admission.calls[-1]["disposition"]
        is PublicConversationAdmissionDisposition.EXACT_RETRY
    )


def test_exact_retry_republishes_after_initial_publish_failure() -> None:
    repository = _Repository(_binding())
    publisher = _FailOncePublisher()
    use_case, _cipher, _admission = _use_case(repository, publisher=publisher)

    with pytest.raises(RuntimeError, match="publish unavailable"):
        use_case.execute(_command())

    retry = use_case.execute(_command())

    assert retry.replayed is True
    assert len(repository.turns) == 1
    assert len(repository.dispatches) == 1
    assert len(publisher.calls) == 2


def test_exact_retry_republishes_after_committed_claim_publish_failure() -> None:
    repository = _Repository(_binding())
    publisher = _ClaimingFailOncePublisher(repository)
    use_case, _cipher, _admission = _use_case(repository, publisher=publisher)

    with pytest.raises(RuntimeError, match="definitive pre-send failure"):
        use_case.execute(_command())

    dispatch = next(iter(repository.dispatches.values()))
    assert dispatch.status is DispatchStatus.RECONCILE_REQUIRED
    republished = use_case.execute(_command())

    assert republished.replayed is True
    assert len(repository.turns) == 1
    assert len(repository.dispatches) == 1
    assert len(publisher.calls) == 2


@pytest.mark.parametrize(
    "dispatch_status",
    [DispatchStatus.CLAIMED, DispatchStatus.ACKNOWLEDGED],
)
def test_exact_retry_does_not_publish_after_dispatch_is_claimed_or_acknowledged(
    dispatch_status,
) -> None:
    repository = _Repository(_binding())
    publisher = _Publisher()
    use_case, _cipher, _admission = _use_case(repository, publisher=publisher)

    first = use_case.execute(_command())
    dispatch = repository.dispatches[first.dispatch_id]
    dispatch.claim(
        owner="gateway-test-owner",
        deadline=repository.now + timedelta(seconds=30),
        now=repository.now,
    )
    if dispatch_status is DispatchStatus.ACKNOWLEDGED:
        dispatch.acknowledge(
            claim_generation=dispatch.claim_generation,
            broker_message_id="conversation-turn-test",
            workflow_admission_reference="admission-test",
            now=repository.now,
        )
    replay = use_case.execute(_command())

    assert replay.replayed is True
    assert len(publisher.calls) == 1


def test_public_turn_status_returns_only_approved_display_projection() -> None:
    repository = _Repository(_binding())
    start_turn, cipher, _admission = _use_case(repository)
    started = start_turn.execute(_command())
    turn = repository.turns[started.turn_id]
    assistant_entry_id = uuid.uuid4()
    turn.status = TurnStatus.COMPLETED
    turn.assistant_entry_id = assistant_entry_id
    repository.entries[assistant_entry_id] = ConversationMemoryEntry.approved_assistant(
        entry_id=assistant_entry_id,
        organization_id=repository.binding.organization_id,
        session_id=repository.session.id,
        turn_id=turn.id,
        sequence=turn.sequence * 2,
        channel="conversation",
        content=ProtectedEntryContent(
            display=cipher.protect("Approved redacted answer", associated_data="test"),
            model=cipher.protect("raw model projection", associated_data="test"),
        ),
        content_revision=1,
        idempotency_key_hash="2" * 64,
        now=_now(),
    )
    use_case = GetPublicTurnStatusUseCase(
        repository=repository,
        uow=_Uow(),
        secrets=_Secrets(),
        content_cipher=cipher,
    )

    result = use_case.execute(
        url_slug="chatbot",
        turn_id=turn.id,
        access_token="token",
        now=_now(),
    )

    assert result.turn_id == turn.id
    assert result.turn_state is TurnStatus.COMPLETED
    assert result.lifecycle_revision == repository.session.lifecycle_revision
    assert result.display == "Approved redacted answer"
    assert result.safe_failure_reason is None
    assert cipher.reveals == [
        (
            repository.entries[assistant_entry_id].content.display,
            (
                f"memory-content-v1:{repository.binding.organization_id}:"
                f"{repository.session.id}:{turn.id}:{assistant_entry_id}:display"
            ),
        )
    ]


def test_public_turn_status_hides_invalid_capability() -> None:
    repository = _Repository(_binding())
    use_case = GetPublicTurnStatusUseCase(
        repository=repository,
        uow=_Uow(),
        secrets=_Secrets(),
        content_cipher=_Cipher(),
    )

    with pytest.raises(AccessGrantNotUsableError):
        use_case.execute(
            url_slug="chatbot",
            turn_id=uuid.uuid4(),
            access_token="invalid",
            now=_now(),
        )


def test_same_key_with_different_input_is_conflict_before_new_write() -> None:
    repository = _Repository(_binding())
    use_case, cipher, _admission = _use_case(repository)
    use_case.execute(_command())

    with pytest.raises(DuplicateRequestConflictError):
        use_case.execute(_command("different"))

    assert len(repository.turns) == 1
    assert len(cipher.values) == 2


def test_runtime_requires_explicit_supported_frozen_contract() -> None:
    repository = _Repository(_binding(runtime_contract_ready=False))
    use_case, _cipher, admission = _use_case(repository)

    with pytest.raises(PublicConversationFeatureDisabledError):
        use_case.execute(_command())

    assert repository.turns == {}
    assert admission.calls == []


def test_invalid_or_expired_grant_is_resource_hidden_without_turn() -> None:
    repository = _Repository(_binding())
    repository.grant.expires_at = _now()
    use_case, _cipher, admission = _use_case(repository)

    with pytest.raises(AccessGrantNotUsableError):
        use_case.execute(_command())

    assert repository.turns == {}
    assert admission.calls == []


def test_exact_retry_uses_the_stored_fingerprint_key_after_primary_rotation() -> None:
    repository = _Repository(_binding())
    fingerprinter = _RotatingFingerprinter()
    use_case, _cipher, admission = _use_case(
        repository,
        fingerprinter=fingerprinter,
    )
    first = use_case.execute(_command())
    fingerprinter.primary = "admission-v2"

    replay = use_case.execute(_command())

    assert replay.replayed is True
    assert replay.turn_id == first.turn_id
    assert repository.turns[first.turn_id].request_fingerprint_key_version == (
        "admission-v1"
    )
    assert admission.calls[-1]["disposition"] is (
        PublicConversationAdmissionDisposition.EXACT_RETRY
    )


def test_rotated_fingerprint_still_rejects_same_key_with_different_input() -> None:
    repository = _Repository(_binding())
    fingerprinter = _RotatingFingerprinter()
    use_case, cipher, admission = _use_case(
        repository,
        fingerprinter=fingerprinter,
    )
    use_case.execute(_command())
    fingerprinter.primary = "admission-v2"

    with pytest.raises(DuplicateRequestConflictError):
        use_case.execute(_command("different"))

    assert len(repository.turns) == 1
    assert len(cipher.values) == 2
    assert len(admission.calls) == 1


def test_missing_stored_fingerprint_key_fails_closed_before_admission_or_write() -> (
    None
):
    repository = _Repository(_binding())
    fingerprinter = _RotatingFingerprinter()
    use_case, cipher, admission = _use_case(
        repository,
        fingerprinter=fingerprinter,
    )
    first = use_case.execute(_command())
    fingerprinter.primary = "admission-v2"
    fingerprinter.available.remove("admission-v1")

    with pytest.raises(MemoryAdapterUnavailableError):
        use_case.execute(_command())

    assert list(repository.turns) == [first.turn_id]
    assert len(cipher.values) == 2
    assert len(admission.calls) == 1


@pytest.mark.parametrize("completed_turns", [0, 99])
def test_public_turn_limit_allows_requests_below_the_completed_turn_cap(
    completed_turns,
) -> None:
    repository = _Repository(_binding())
    repository.completed_turn_count = completed_turns
    use_case, _cipher, admission = _use_case(repository)

    result = use_case.execute(_command())

    assert result.turn_state is TurnStatus.PENDING_DISPATCH
    assert len(admission.calls) == 1


def test_101st_completed_public_turn_is_rejected_before_admission_or_write() -> None:
    repository = _Repository(_binding())
    repository.completed_turn_count = 100
    publisher = _Publisher()
    use_case, cipher, admission = _use_case(repository, publisher=publisher)

    with pytest.raises(PublicConversationTurnLimitExceededError):
        use_case.execute(_command())

    assert repository.turns == {}
    assert cipher.values == []
    assert admission.calls == []
    assert publisher.calls == []


def test_exact_retry_remains_available_at_the_completed_turn_cap() -> None:
    repository = _Repository(_binding())
    use_case, cipher, admission = _use_case(repository)
    first = use_case.execute(_command())
    repository.completed_turn_count = 100

    replay = use_case.execute(_command())

    assert replay.replayed is True
    assert replay.turn_id == first.turn_id
    assert len(cipher.values) == 2
    assert admission.calls[-1]["disposition"] is (
        PublicConversationAdmissionDisposition.EXACT_RETRY
    )


def test_turn_cap_is_rechecked_under_the_locked_start_transaction():
    repository = _Repository(_binding())
    publisher = _Publisher()
    use_case, cipher, admission = _use_case(
        repository,
        publisher=publisher,
    )

    admission.on_admit = lambda: setattr(repository, "completed_turn_count", 100)
    with pytest.raises(PublicConversationTurnLimitExceededError):
        use_case.execute(_command())

    assert len(admission.calls) == 1
    assert repository.turns == {}
    assert cipher.values == []
    assert publisher.calls == []
