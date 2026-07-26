from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from apps.memory.adapters.persistence.repository import (
    SqlAlchemyConversationMemoryRepository,
)
from apps.memory.application.context import (
    ContextAttemptState,
    ContextCandidatePair,
    ContextEntryReference,
    ContextLeaseState,
    MemoryContextLease,
    MemoryContextPlan,
    MemoryContextProviderAttempt,
)
from apps.memory.domain.conversation import (
    CONVERSATION_SOURCE_FREE_PROOF_VERSION,
    EntryType,
    TurnStatus,
)
from apps.shared.db.models.conversation_memory import (
    MemoryContextLeaseRecord,
    MemoryContextPlanRecord,
    MemoryContextProviderAttemptRecord,
)


def _now() -> datetime:
    return datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _mapping_rows_result(rows):
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


def _query_result(row):
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    return result


def _rowcount_result(rowcount: int):
    result = MagicMock()
    result.rowcount = rowcount
    return result


def _reference(
    *,
    turn_id: uuid.UUID,
    sequence: int,
    entry_type: EntryType,
    proof_version: str | None = CONVERSATION_SOURCE_FREE_PROOF_VERSION,
) -> ContextEntryReference:
    return ContextEntryReference(
        entry_id=uuid.uuid4(),
        turn_id=turn_id,
        sequence=sequence,
        entry_type=entry_type,
        content_revision=1,
        content_digest=uuid.uuid4().hex * 2,
        dependency_proof_version=proof_version,
    )


def _pair(
    turn_sequence: int,
    *,
    proof_version: str | None = CONVERSATION_SOURCE_FREE_PROOF_VERSION,
) -> ContextCandidatePair:
    turn_id = uuid.uuid4()
    return ContextCandidatePair(
        turn_id=turn_id,
        turn_sequence=turn_sequence,
        status=TurnStatus.COMPLETED,
        user=_reference(
            turn_id=turn_id,
            sequence=turn_sequence * 2 - 1,
            entry_type=EntryType.USER_TURN,
            proof_version=proof_version,
        ),
        assistant=_reference(
            turn_id=turn_id,
            sequence=turn_sequence * 2,
            entry_type=EntryType.ASSISTANT_TURN,
            proof_version=proof_version,
        ),
        dependency_count=0,
    )


def _candidate_row(pair: ContextCandidatePair) -> dict[str, object]:
    assert pair.user is not None
    assert pair.assistant is not None
    return {
        "turn_id": pair.turn_id,
        "turn_sequence": pair.turn_sequence,
        "turn_status": pair.status.value,
        "user_entry_id": pair.user.entry_id,
        "user_turn_id": pair.user.turn_id,
        "user_sequence": pair.user.sequence,
        "user_entry_type": pair.user.entry_type.value,
        "user_content_revision": pair.user.content_revision,
        "user_content_digest": pair.user.content_digest,
        "user_dependency_proof_version": pair.user.dependency_proof_version,
        "assistant_entry_id": pair.assistant.entry_id,
        "assistant_turn_id": pair.assistant.turn_id,
        "assistant_sequence": pair.assistant.sequence,
        "assistant_entry_type": pair.assistant.entry_type.value,
        "assistant_content_revision": pair.assistant.content_revision,
        "assistant_content_digest": pair.assistant.content_digest,
        "assistant_dependency_proof_version": (pair.assistant.dependency_proof_version),
        "dependency_count": pair.dependency_count,
    }


def test_candidate_query_is_reference_only_and_preserves_newest_first_barrier():
    newest = _pair(3)
    incomplete = _pair(2, proof_version=None)
    older = _pair(1)
    db = MagicMock(spec=Session)
    db.execute.return_value = _mapping_rows_result(
        [_candidate_row(newest), _candidate_row(incomplete), _candidate_row(older)]
    )

    candidates = SqlAlchemyConversationMemoryRepository(
        db
    ).list_prior_context_candidates(
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        before_turn_sequence=4,
        limit=3,
    )

    assert [candidate.turn_sequence for candidate in candidates] == [3, 2, 1]
    assert [candidate.eligible for candidate in candidates] == [True, False, True]
    statement = db.execute.call_args.args[0]
    compiled_sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "conversation_turns.status" in compiled_sql
    assert "ORDER BY conversation_turns.sequence DESC" in compiled_sql
    assert "context_user_entry.invalidated_at IS NULL" in compiled_sql
    assert "context_user_entry.expires_at" in compiled_sql
    assert "context_assistant_entry.invalidated_at IS NULL" in compiled_sql
    assert "context_assistant_entry.expires_at" in compiled_sql
    assert "display_ciphertext" not in compiled_sql
    assert "model_ciphertext" not in compiled_sql
    assert "model_content_digest" in compiled_sql


def test_context_plan_lease_and_attempt_round_trip_with_fencing_cas():
    organization_id = uuid.uuid4()
    session_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    pair = _pair(1)
    plan = MemoryContextPlan(
        id=uuid.uuid4(),
        organization_id=organization_id,
        session_id=session_id,
        turn_id=turn_id,
        ordered_pairs=(pair,),
        policy_version="conversation-window-v1",
        content_digest="a" * 64,
        lifecycle_revision=2,
        content_revision=3,
        turn_version=4,
        authorization_revision_set_digest="b" * 64,
        expires_at=_now() + timedelta(minutes=5),
    )
    lease = MemoryContextLease(
        id=uuid.uuid4(),
        organization_id=organization_id,
        session_id=session_id,
        turn_id=turn_id,
        plan_id=plan.id,
        node_invocation_id=uuid.uuid4(),
        provider_capability_reference="capability-ref",
        provider_capability_revision="capability-rev-1",
        provider_attempt_id=None,
        state=ContextLeaseState.ISSUED,
        claim_generation=0,
        claim_deadline_at=None,
        expires_at=plan.expires_at,
    )
    attempt = MemoryContextProviderAttempt(
        id=uuid.uuid4(),
        organization_id=organization_id,
        session_id=session_id,
        turn_id=turn_id,
        lease_id=lease.id,
        plan_id=plan.id,
        node_invocation_id=lease.node_invocation_id,
        provider_capability_reference=lease.provider_capability_reference,
        provider_capability_revision=lease.provider_capability_revision,
        status=ContextAttemptState.CLAIMED,
        version=1,
        claim_generation=1,
        claim_deadline_at=_now() + timedelta(minutes=1),
    )
    seed_db = MagicMock(spec=Session)
    seed_repository = SqlAlchemyConversationMemoryRepository(seed_db)

    seed_repository.add_context_plan(plan)
    seed_repository.add_context_lease(lease)
    seed_repository.add_context_attempt(attempt)

    plan_record, lease_record, attempt_record = [
        call.args[0] for call in seed_db.add.call_args_list
    ]
    assert isinstance(plan_record, MemoryContextPlanRecord)
    assert isinstance(lease_record, MemoryContextLeaseRecord)
    assert isinstance(attempt_record, MemoryContextProviderAttemptRecord)
    assert plan_record.channel == "conversation"
    assert "ciphertext" not in repr(plan_record.ordered_references)
    assert "plaintext" not in repr(plan_record.ordered_references)
    assert '"value"' not in repr(plan_record.ordered_references)

    db = MagicMock(spec=Session)
    db.execute.side_effect = [
        _query_result(plan_record),
        _query_result(lease_record),
        _rowcount_result(1),
        _query_result(attempt_record),
        _rowcount_result(1),
    ]
    repository = SqlAlchemyConversationMemoryRepository(db)

    loaded_plan = repository.find_context_plan(plan.id)
    loaded_lease = repository.lock_context_lease(lease.id)
    assert loaded_plan == plan
    assert loaded_lease == lease
    loaded_lease.claim(
        provider_attempt_id=attempt.id,
        deadline=attempt.claim_deadline_at,
        now=_now(),
    )
    repository.save_context_lease(loaded_lease)

    loaded_attempt = repository.lock_context_attempt(attempt.id)
    assert loaded_attempt == attempt
    loaded_attempt.mark_provider_started(
        expected_version=1,
        usage_reference="usage-ref",
        now=_now(),
    )
    repository.save_context_attempt(loaded_attempt)

    lease_select = db.execute.call_args_list[1].args[0]
    lease_update = db.execute.call_args_list[2].args[0]
    attempt_select = db.execute.call_args_list[3].args[0]
    attempt_update = db.execute.call_args_list[4].args[0]
    assert "FOR UPDATE" in str(lease_select.compile(dialect=postgresql.dialect()))
    assert "memory_context_leases.claim_generation" in str(
        lease_update.compile(dialect=postgresql.dialect())
    )
    assert "FOR UPDATE" in str(attempt_select.compile(dialect=postgresql.dialect()))
    assert "memory_context_provider_attempts.version" in str(
        attempt_update.compile(dialect=postgresql.dialect())
    )
    assert "memory_context_provider_attempts.claim_generation" in str(
        attempt_update.compile(dialect=postgresql.dialect())
    )
    assert attempt_update.compile().params["status"] == "provider_started"
    assert attempt_update.compile().params["version"] == 2
    assert attempt_update.compile().params["claim_generation"] == 1
    assert (
        attempt_update.compile().params["claim_deadline_at"]
        == attempt.claim_deadline_at
    )
