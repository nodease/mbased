from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationPurgeJob,
    ConversationSession,
    ConversationTurn,
    MemoryTurnDispatchJob,
    ProtectedContent,
    ProtectedEntryContent,
    RequestIdentity,
    SessionLifecycle,
    TurnStatus,
)
from apps.memory.domain.errors import (
    ActiveTurnConflictError,
    DuplicateRequestConflictError,
    DispatchStateConflictError,
    InvalidTurnTransitionError,
    SessionClosedError,
    SessionNotActiveError,
    StaleLifecycleRevisionError,
    StaleRevisionError,
    StaleTurnVersionError,
)


def _now() -> datetime:
    return datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def _session() -> ConversationSession:
    return ConversationSession.create(
        session_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=3,
        deployment_snapshot_hash=None,
        mapping_version="mapping-v1",
        memory_policy_version="memory-v1",
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        audience_kind=AudienceKind.PUBLIC_CHATBOT,
        subject_type=None,
        subject_id=None,
        idle_expires_at=_now() + timedelta(hours=24),
        absolute_expires_at=_now() + timedelta(days=7),
        now=_now(),
    )


def _request_identity(seed: str = "a") -> RequestIdentity:
    return RequestIdentity(
        idempotency_key_hash=seed * 64,
        request_fingerprint="b" * 64,
    )


def _protected_content() -> ProtectedContent:
    return ProtectedContent(
        ciphertext=b"encrypted-user-projection",
        key_version="key-v1",
        format_version="memory-envelope-v1",
        content_digest="c" * 64,
        plaintext_byte_length=128,
    )


def test_session_claims_one_active_turn_without_changing_lifecycle_revision():
    session = _session()
    turn_id = uuid.uuid4()

    sequence = session.claim_turn(
        turn_id=turn_id,
        expected_lifecycle_revision=1,
        now=_now(),
    )

    assert sequence == 1
    assert session.active_turn_id == turn_id
    assert session.next_turn_sequence == 2
    assert session.lifecycle_revision == 1
    assert session.content_revision == 0

    with pytest.raises(ActiveTurnConflictError):
        session.claim_turn(
            turn_id=uuid.uuid4(),
            expected_lifecycle_revision=1,
            now=_now(),
        )


def test_session_rejects_stale_revision_and_terminal_lifecycle():
    session = _session()

    with pytest.raises(StaleLifecycleRevisionError) as stale:
        session.claim_turn(
            turn_id=uuid.uuid4(),
            expected_lifecycle_revision=2,
            now=_now(),
        )
    assert stale.value.code == "memory.stale_lifecycle_revision"

    session.close(expected_lifecycle_revision=1, now=_now())
    assert session.lifecycle is SessionLifecycle.CLOSED
    assert session.lifecycle_revision == 2

    with pytest.raises(SessionClosedError) as closed:
        session.claim_turn(
            turn_id=uuid.uuid4(),
            expected_lifecycle_revision=2,
            now=_now(),
        )
    assert closed.value.code == "memory.session_closed"


def test_session_claim_and_release_reject_idle_or_absolute_expiry_boundary():
    idle_expired = _session()
    idle_expired.idle_expires_at = _now()
    with pytest.raises(SessionNotActiveError):
        idle_expired.claim_turn(
            turn_id=uuid.uuid4(),
            expected_lifecycle_revision=1,
            now=_now(),
        )

    absolute_expired = _session()
    turn_id = uuid.uuid4()
    absolute_expired.claim_turn(
        turn_id=turn_id,
        expected_lifecycle_revision=1,
        now=_now(),
    )
    absolute_expired.absolute_expires_at = _now()
    with pytest.raises(SessionNotActiveError):
        absolute_expired.release_turn(
            turn_id=turn_id,
            expected_lifecycle_revision=1,
            content_changed=True,
            now=_now(),
        )

    assert absolute_expired.active_turn_id == turn_id
    assert absolute_expired.content_revision == 0


def test_purge_receipt_rejects_more_than_eight_days_from_issue_time():
    common = {
        "organization_id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "session_reference_digest": "a" * 64,
        "app_id": uuid.uuid4(),
        "deployment_id": uuid.uuid4(),
        "deployment_version": 1,
        "audience_kind": AudienceKind.PUBLIC_CHATBOT,
        "receipt_verifier_hash": "b" * 64,
        "receipt_verifier_key_version": "key-v1",
        "max_attempts": 8,
        "now": _now(),
    }

    accepted = ConversationPurgeJob.pending(
        **common,
        purge_job_id=uuid.uuid4(),
        receipt_expires_at=_now() + timedelta(days=8),
    )
    assert accepted.receipt_expires_at == _now() + timedelta(days=8)

    with pytest.raises(ValueError, match="purge receipt"):
        ConversationPurgeJob.pending(
            **common,
            purge_job_id=uuid.uuid4(),
            receipt_expires_at=_now() + timedelta(days=8, microseconds=1),
        )


def test_completed_turn_changes_content_revision_but_failed_turn_does_not():
    session = _session()
    first_turn_id = uuid.uuid4()
    session.claim_turn(
        turn_id=first_turn_id,
        expected_lifecycle_revision=1,
        now=_now(),
    )

    session.release_turn(
        turn_id=first_turn_id,
        expected_lifecycle_revision=1,
        content_changed=False,
        now=_now(),
    )

    assert session.active_turn_id is None
    assert session.content_revision == 0
    assert session.lifecycle_revision == 1

    second_turn_id = uuid.uuid4()
    session.claim_turn(
        turn_id=second_turn_id,
        expected_lifecycle_revision=1,
        now=_now(),
    )
    session.release_turn(
        turn_id=second_turn_id,
        expected_lifecycle_revision=1,
        content_changed=True,
        now=_now(),
    )

    assert session.content_revision == 1
    assert session.lifecycle_revision == 1


def test_delete_pending_blocks_late_completion_and_clears_active_claim():
    session = _session()
    turn_id = uuid.uuid4()
    session.claim_turn(
        turn_id=turn_id,
        expected_lifecycle_revision=1,
        now=_now(),
    )

    session.request_delete(expected_lifecycle_revision=1, now=_now())

    assert session.lifecycle is SessionLifecycle.DELETE_PENDING
    assert session.active_turn_id is None
    assert session.lifecycle_revision == 2

    with pytest.raises(StaleRevisionError):
        session.release_turn(
            turn_id=turn_id,
            expected_lifecycle_revision=1,
            content_changed=True,
            now=_now(),
        )


def test_authenticated_session_requires_subject_binding():
    with pytest.raises(ValueError, match="subject binding"):
        ConversationSession.create(
            session_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            app_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            deployment_id=uuid.uuid4(),
            deployment_version=3,
            deployment_snapshot_hash=None,
            mapping_version="mapping-v1",
            memory_policy_version="memory-v1",
            memory_contract_version="conversation-memory-v1",
            storage_generation=1,
            audience_kind=AudienceKind.AUTHENTICATED_INTERNAL_CHATBOT,
            subject_type=None,
            subject_id=None,
            idle_expires_at=_now() + timedelta(hours=24),
            absolute_expires_at=_now() + timedelta(days=7),
            now=_now(),
        )


def test_request_identity_is_hash_only_and_detects_conflicting_fingerprint():
    identity = _request_identity()

    assert "prompt" not in repr(identity).lower()
    identity.ensure_replay_matches("b" * 64)

    with pytest.raises(DuplicateRequestConflictError):
        identity.ensure_replay_matches("d" * 64)

    with pytest.raises(ValueError, match="SHA-256"):
        RequestIdentity(
            idempotency_key_hash="raw-idempotency-key",
            request_fingerprint="b" * 64,
        )


def test_protected_content_redacts_ciphertext_from_repr_and_enforces_bounds():
    content = _protected_content()

    assert "encrypted-user-projection" not in repr(content)

    with pytest.raises(ValueError, match="plaintext_byte_length"):
        ProtectedContent(
            ciphertext=b"encrypted",
            key_version="key-v1",
            format_version="memory-envelope-v1",
            content_digest="c" * 64,
            plaintext_byte_length=16_385,
        )

    with pytest.raises(ValueError, match="projection"):
        ProtectedEntryContent(display=None, model=None)

    projections = ProtectedEntryContent(
        display=_protected_content(),
        model=ProtectedContent(
            ciphertext=b"different-encrypted-model-projection",
            key_version="model-key-v2",
            format_version="memory-envelope-v2",
            content_digest="d" * 64,
            plaintext_byte_length=96,
        ),
    )
    assert projections.display != projections.model
    assert "different-encrypted" not in repr(projections)


def test_turn_allows_only_ordered_terminal_transition():
    turn = ConversationTurn.start(
        turn_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        sequence=1,
        started_lifecycle_revision=1,
        request_identity=_request_identity(),
        user_entry_id=uuid.uuid4(),
        dispatch_id=uuid.uuid4(),
        now=_now(),
    )

    turn.mark_queued(expected_version=1, now=_now())
    turn.mark_running(
        expected_version=2,
        execution_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        now=_now(),
    )
    assistant_entry_id = uuid.uuid4()
    turn.complete(
        expected_version=3,
        assistant_entry_id=assistant_entry_id,
        now=_now(),
    )

    assert turn.status is TurnStatus.COMPLETED
    assert turn.version == 4
    assert turn.assistant_entry_id == assistant_entry_id

    with pytest.raises(InvalidTurnTransitionError):
        turn.fail(
            expected_version=4,
            safe_reason_code="memory.execution_failed",
            now=_now(),
        )

    with pytest.raises(StaleTurnVersionError) as stale:
        ConversationTurn.start(
            turn_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            sequence=1,
            started_lifecycle_revision=1,
            request_identity=_request_identity("d"),
            user_entry_id=uuid.uuid4(),
            dispatch_id=uuid.uuid4(),
            now=_now(),
        ).mark_queued(expected_version=2, now=_now())
    assert stale.value.code == "memory.stale_turn_version"


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_terminal_turn_states_are_absorbing_for_every_late_transition(
    terminal_status: str,
):
    turn = ConversationTurn.start(
        turn_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        sequence=1,
        started_lifecycle_revision=1,
        request_identity=_request_identity(),
        user_entry_id=uuid.uuid4(),
        dispatch_id=uuid.uuid4(),
        now=_now(),
    )
    turn.mark_queued(expected_version=1, now=_now())
    turn.mark_running(
        expected_version=2,
        execution_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        now=_now(),
    )
    if terminal_status == "completed":
        turn.complete(
            expected_version=3,
            assistant_entry_id=uuid.uuid4(),
            now=_now(),
        )
    elif terminal_status == "failed":
        turn.fail(
            expected_version=3,
            safe_reason_code="memory.execution_failed",
            now=_now(),
        )
    else:
        turn.cancel(
            expected_version=3,
            safe_reason_code="memory.execution_cancelled",
            now=_now(),
        )

    terminal_version = turn.version
    late_transitions = (
        lambda: turn.mark_queued(expected_version=terminal_version, now=_now()),
        lambda: turn.mark_running(
            expected_version=terminal_version,
            execution_id=uuid.uuid4(),
            attempt_id=uuid.uuid4(),
            now=_now(),
        ),
        lambda: turn.complete(
            expected_version=terminal_version,
            assistant_entry_id=uuid.uuid4(),
            now=_now(),
        ),
        lambda: turn.fail(
            expected_version=terminal_version,
            safe_reason_code="memory.execution_failed",
            now=_now(),
        ),
        lambda: turn.cancel(
            expected_version=terminal_version,
            safe_reason_code="memory.execution_cancelled",
            now=_now(),
        ),
    )
    for transition in late_transitions:
        with pytest.raises(InvalidTurnTransitionError):
            transition()
        assert turn.status.value == terminal_status
        assert turn.version == terminal_version


def test_dispatch_claim_uses_fencing_generation_and_rejects_stale_owner():
    job = MemoryTurnDispatchJob.pending(
        dispatch_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
        max_attempts=5,
        now=_now(),
    )

    generation = job.claim(
        owner="dispatcher-a",
        deadline=_now() + timedelta(seconds=30),
        now=_now(),
    )
    assert generation == 1

    with pytest.raises(DispatchStateConflictError) as stale:
        job.mark_published(
            owner="dispatcher-a",
            claim_generation=0,
            broker_message_id="opaque-message-ref",
            now=_now(),
        )
    assert stale.value.code == "memory.dispatch_state_conflict"

    job.mark_published(
        owner="dispatcher-a",
        claim_generation=1,
        broker_message_id="opaque-message-ref",
        now=_now(),
    )
    assert job.status.value == "published"


def test_expired_dispatch_claim_retries_then_reaches_safe_terminal_state():
    job = MemoryTurnDispatchJob.pending(
        dispatch_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
        max_attempts=2,
        now=_now(),
    )
    first_deadline = _now() + timedelta(seconds=30)
    job.claim(owner="dispatcher-a", deadline=first_deadline, now=_now())

    with pytest.raises(DispatchStateConflictError):
        job.recover_expired_claim(
            now=first_deadline - timedelta(seconds=1),
            retry_at=first_deadline + timedelta(seconds=10),
            safe_reason_code="memory.dispatch_claim_expired",
        )

    retry_at = first_deadline + timedelta(seconds=10)
    job.recover_expired_claim(
        now=first_deadline,
        retry_at=retry_at,
        safe_reason_code="memory.dispatch_claim_expired",
    )
    assert job.status.value == "reconcile_required"
    assert job.claim_owner is None
    assert job.claim_deadline_at is None

    with pytest.raises(DispatchStateConflictError):
        job.claim(
            owner="dispatcher-b",
            deadline=retry_at + timedelta(seconds=30),
            now=retry_at - timedelta(seconds=1),
        )

    second_deadline = retry_at + timedelta(seconds=30)
    generation = job.claim(
        owner="dispatcher-b",
        deadline=second_deadline,
        now=retry_at,
    )
    assert generation == 2
    job.recover_expired_claim(
        now=second_deadline,
        retry_at=None,
        safe_reason_code="memory.dispatch_attempts_exhausted",
    )

    assert job.status.value == "terminal"
    assert job.terminal_at == second_deadline
    assert job.safe_failure_reason == "memory.dispatch_attempts_exhausted"
