from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.memory.adapters.persistence import repository as persistence_repository
from apps.memory.adapters.persistence.repository import (
    SqlAlchemyConversationMemoryRepository,
    SqlAlchemyMemoryUnitOfWork,
)
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationMemoryEntry,
    ConversationPurgeJob,
    ConversationSession,
    ConversationTurn,
    MemoryTurnDispatchJob,
    ProtectedContent,
    ProtectedEntryContent,
    RequestIdentity,
)
from apps.memory.domain.errors import (
    MemoryAdapterUnavailableError,
    StaleRevisionError,
)
from apps.memory.domain.public_access import ConversationAccessGrant
from apps.shared.db.models.conversation_memory import (
    ConversationMemoryEntryRecord,
    ConversationPurgeJobRecord,
    ConversationSessionRecord,
    ConversationTurnRecord,
    MemoryTurnDispatchJobRecord,
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
        deployment_version=4,
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


def _protected_content(seed: bytes = b"ciphertext") -> ProtectedEntryContent:
    return ProtectedEntryContent(
        display=ProtectedContent(
            ciphertext=b"display-" + seed,
            key_version="display-key-v1",
            format_version="memory-envelope-v1",
            content_digest="c" * 64,
            plaintext_byte_length=96,
        ),
        model=ProtectedContent(
            ciphertext=b"model-" + seed,
            key_version="model-key-v2",
            format_version="memory-envelope-v2",
            content_digest="d" * 64,
            plaintext_byte_length=128,
        ),
    )


def _turn(session: ConversationSession) -> ConversationTurn:
    return ConversationTurn.start(
        turn_id=uuid.uuid4(),
        organization_id=session.organization_id,
        session_id=session.id,
        sequence=1,
        started_lifecycle_revision=session.lifecycle_revision,
        request_identity=RequestIdentity(
            idempotency_key_hash="a" * 64,
            request_fingerprint="b" * 64,
        ),
        user_entry_id=uuid.uuid4(),
        dispatch_id=uuid.uuid4(),
        now=_now(),
    )


def _query_result(row):
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    return result


def _rowcount_result(rowcount: int):
    result = MagicMock()
    result.rowcount = rowcount
    return result


def _scalar_rows_result(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


def test_demo_resource_cleanup_deletes_only_matching_conversation_sessions():
    db = MagicMock(spec=Session)
    db.query.return_value.filter.return_value.delete.return_value = 3

    deleted = persistence_repository.delete_conversation_sessions_for_resources(
        db,
        app_ids=[uuid.uuid4()],
        workflow_ids=[uuid.uuid4()],
        deployment_ids=[uuid.uuid4()],
    )

    assert deleted == 3
    db.query.assert_called_once_with(ConversationSessionRecord)
    condition = db.query.return_value.filter.call_args.args[0]
    condition_sql = str(condition.compile(dialect=postgresql.dialect()))
    assert "conversation_sessions.app_id" in condition_sql
    assert "conversation_sessions.workflow_id" in condition_sql
    assert "conversation_sessions.deployment_id" in condition_sql
    assert " OR " in condition_sql
    db.query.return_value.filter.return_value.delete.assert_called_once_with(
        synchronize_session=False
    )


def test_demo_resource_cleanup_with_empty_scope_never_deletes_sessions():
    db = MagicMock(spec=Session)

    deleted = persistence_repository.delete_conversation_sessions_for_resources(
        db,
        app_ids=[],
        workflow_ids=[],
        deployment_ids=[],
    )

    assert deleted == 0
    db.query.assert_not_called()


def test_access_grant_lookup_accepts_a_bounded_versioned_verifier_set():
    session = _session()
    grant = ConversationAccessGrant.issue(
        grant_id=uuid.uuid4(),
        organization_id=session.organization_id,
        session_id=session.id,
        deployment_id=session.deployment_id,
        deployment_version=session.deployment_version,
        audience_kind=session.audience_kind,
        verifier_hash="e" * 64,
        verifier_key_version="cap-v1",
        expires_at=_now() + timedelta(hours=24),
        now=_now(),
    )
    seed_db = MagicMock(spec=Session)
    SqlAlchemyConversationMemoryRepository(seed_db).add_access_grant(grant)
    record = seed_db.add.call_args.args[0]
    db = MagicMock(spec=Session)
    db.execute.return_value = _scalar_rows_result([record])
    candidates = (("cap-v2", "f" * 64), ("cap-v1", "e" * 64))

    loaded = SqlAlchemyConversationMemoryRepository(db).lock_access_grant(
        verifier_candidates=candidates
    )

    statement = db.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert loaded is not None
    assert loaded.id == grant.id
    assert "verifier_key_version" in str(compiled)
    assert list(candidates) in compiled.params.values()
    assert "FOR UPDATE" in str(compiled)


def test_purge_receipt_lookup_accepts_a_bounded_versioned_verifier_set():
    session = _session()
    purge = ConversationPurgeJob.pending(
        purge_job_id=uuid.uuid4(),
        organization_id=session.organization_id,
        session_id=session.id,
        session_reference_digest="d" * 64,
        app_id=session.app_id,
        deployment_id=session.deployment_id,
        deployment_version=session.deployment_version,
        audience_kind=session.audience_kind,
        receipt_verifier_hash="e" * 64,
        receipt_verifier_key_version="cap-v1",
        receipt_expires_at=_now() + timedelta(days=8),
        max_attempts=5,
        now=_now(),
    )
    seed_db = MagicMock(spec=Session)
    SqlAlchemyConversationMemoryRepository(seed_db).add_purge_job(purge)
    record = seed_db.add.call_args.args[0]
    db = MagicMock(spec=Session)
    db.execute.return_value = _scalar_rows_result([record])
    candidates = (("cap-v2", "f" * 64), ("cap-v1", "e" * 64))

    loaded = SqlAlchemyConversationMemoryRepository(db).find_purge_job(
        verifier_candidates=candidates
    )

    compiled = db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert loaded is not None
    assert loaded.id == purge.id
    assert loaded.app_id == purge.app_id
    assert "receipt_verifier_key_version" in str(compiled)
    assert list(candidates) in compiled.params.values()


def test_session_lock_is_tenant_scoped_and_save_uses_revision_cas():
    domain_session = _session()
    record = ConversationSessionRecord(
        id=domain_session.id,
        organization_id=domain_session.organization_id,
        app_id=domain_session.app_id,
        workflow_id=domain_session.workflow_id,
        deployment_id=domain_session.deployment_id,
        deployment_version=domain_session.deployment_version,
        deployment_snapshot_hash=domain_session.deployment_snapshot_hash,
        mapping_version=domain_session.mapping_version,
        memory_policy_version=domain_session.memory_policy_version,
        memory_contract_version=domain_session.memory_contract_version,
        storage_generation=domain_session.storage_generation,
        audience_kind=domain_session.audience_kind.value,
        subject_type=None,
        subject_id=None,
        lifecycle=domain_session.lifecycle.value,
        lifecycle_revision=1,
        content_revision=0,
        active_turn_id=None,
        next_turn_sequence=1,
        idle_expires_at=domain_session.idle_expires_at,
        absolute_expires_at=domain_session.absolute_expires_at,
        created_at=_now(),
        updated_at=_now(),
    )
    db = MagicMock(spec=Session)
    db.execute.side_effect = [_query_result(record), _rowcount_result(1)]
    repository = SqlAlchemyConversationMemoryRepository(db)

    loaded = repository.lock_session(
        organization_id=record.organization_id,
        session_id=record.id,
    )
    assert loaded is not None
    turn_id = uuid.uuid4()
    loaded.claim_turn(
        turn_id=turn_id,
        expected_lifecycle_revision=1,
        now=_now(),
    )
    repository.save_session(loaded)

    select_statement = db.execute.call_args_list[0].args[0]
    select_sql = str(select_statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in select_sql
    assert "conversation_sessions.organization_id" in select_sql
    assert "conversation_sessions.id" in select_sql

    update_statement = db.execute.call_args_list[1].args[0]
    update_sql = str(update_statement.compile(dialect=postgresql.dialect()))
    assert "conversation_sessions.lifecycle_revision" in update_sql
    assert "conversation_sessions.content_revision" in update_sql
    assert update_statement.compile().params["active_turn_id"] == turn_id
    db.commit.assert_not_called()


def test_session_save_rejects_stale_database_revision():
    domain_session = _session()
    db = MagicMock(spec=Session)
    db.execute.side_effect = [
        _query_result(
            ConversationSessionRecord(
                id=domain_session.id,
                organization_id=domain_session.organization_id,
                app_id=domain_session.app_id,
                workflow_id=domain_session.workflow_id,
                deployment_id=domain_session.deployment_id,
                deployment_version=domain_session.deployment_version,
                deployment_snapshot_hash=None,
                mapping_version=domain_session.mapping_version,
                memory_policy_version=domain_session.memory_policy_version,
                memory_contract_version=domain_session.memory_contract_version,
                storage_generation=1,
                audience_kind=domain_session.audience_kind.value,
                lifecycle="active",
                lifecycle_revision=1,
                content_revision=0,
                next_turn_sequence=1,
                idle_expires_at=domain_session.idle_expires_at,
                absolute_expires_at=domain_session.absolute_expires_at,
                created_at=_now(),
                updated_at=_now(),
            )
        ),
        _rowcount_result(0),
    ]
    repository = SqlAlchemyConversationMemoryRepository(db)
    loaded = repository.lock_session(
        organization_id=domain_session.organization_id,
        session_id=domain_session.id,
    )
    assert loaded is not None

    with pytest.raises(StaleRevisionError):
        repository.save_session(loaded)


def test_entry_mapping_persists_only_protected_envelope_and_round_trips():
    domain_session = _session()
    turn = _turn(domain_session)
    entry = ConversationMemoryEntry.provisional_user(
        entry_id=turn.user_entry_id,
        organization_id=turn.organization_id,
        session_id=turn.session_id,
        turn_id=turn.id,
        sequence=1,
        channel="chat",
        content=_protected_content(b"encrypted-user"),
        idempotency_key_hash=turn.request_identity.idempotency_key_hash,
        now=_now(),
    )
    db = MagicMock(spec=Session)
    repository = SqlAlchemyConversationMemoryRepository(db)

    repository.add_entry(entry)

    record = db.add.call_args.args[0]
    assert isinstance(record, ConversationMemoryEntryRecord)
    assert record.display_ciphertext == b"display-encrypted-user"
    assert record.display_key_version == "display-key-v1"
    assert record.display_content_digest == "c" * 64
    assert record.model_ciphertext == b"model-encrypted-user"
    assert record.model_key_version == "model-key-v2"
    assert record.model_content_digest == "d" * 64
    assert not hasattr(record, "content")

    db.execute.return_value = _query_result(record)
    loaded = repository.get_entry(
        organization_id=entry.organization_id,
        session_id=entry.session_id,
        entry_id=entry.id,
    )
    assert loaded is not None
    assert loaded.content == entry.content
    assert "encrypted-user" not in repr(loaded.content)


def test_add_operations_map_domain_objects_without_committing():
    session = _session()
    turn = _turn(session)
    entry = ConversationMemoryEntry.provisional_user(
        entry_id=turn.user_entry_id,
        organization_id=turn.organization_id,
        session_id=turn.session_id,
        turn_id=turn.id,
        sequence=1,
        channel="chat",
        content=_protected_content(),
        idempotency_key_hash=turn.request_identity.idempotency_key_hash,
        now=_now(),
    )
    dispatch = MemoryTurnDispatchJob.pending(
        dispatch_id=turn.dispatch_id,
        organization_id=turn.organization_id,
        session_id=turn.session_id,
        turn_id=turn.id,
        memory_contract_version=session.memory_contract_version,
        storage_generation=session.storage_generation,
        minimum_worker_capability="memory-runtime-v1",
        max_attempts=5,
        now=_now(),
    )
    purge = ConversationPurgeJob.pending(
        purge_job_id=uuid.uuid4(),
        organization_id=session.organization_id,
        session_id=session.id,
        session_reference_digest="d" * 64,
        app_id=session.app_id,
        deployment_id=session.deployment_id,
        deployment_version=session.deployment_version,
        audience_kind=session.audience_kind,
        receipt_verifier_hash="e" * 64,
        receipt_verifier_key_version="key-v1",
        receipt_expires_at=_now() + timedelta(days=1),
        max_attempts=7,
        now=_now(),
    )
    db = MagicMock(spec=Session)
    repository = SqlAlchemyConversationMemoryRepository(db)

    repository.add_session(session)
    repository.add_turn(turn)
    repository.add_entry(entry)
    repository.add_dispatch_job(dispatch)
    repository.add_purge_job(purge)

    added_types = [type(call.args[0]) for call in db.add.call_args_list]
    assert added_types == [
        ConversationSessionRecord,
        ConversationTurnRecord,
        ConversationMemoryEntryRecord,
        MemoryTurnDispatchJobRecord,
        ConversationPurgeJobRecord,
    ]
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_dispatch_lock_and_save_use_status_and_generation_cas():
    domain_session = _session()
    turn = _turn(domain_session)
    dispatch = MemoryTurnDispatchJob.pending(
        dispatch_id=turn.dispatch_id,
        organization_id=turn.organization_id,
        session_id=turn.session_id,
        turn_id=turn.id,
        memory_contract_version=domain_session.memory_contract_version,
        storage_generation=domain_session.storage_generation,
        minimum_worker_capability="memory-runtime-v1",
        max_attempts=5,
        now=_now(),
    )
    seed_db = MagicMock(spec=Session)
    SqlAlchemyConversationMemoryRepository(seed_db).add_dispatch_job(dispatch)
    record = seed_db.add.call_args.args[0]

    db = MagicMock(spec=Session)
    db.execute.side_effect = [_query_result(record), _rowcount_result(1)]
    repository = SqlAlchemyConversationMemoryRepository(db)
    loaded = repository.lock_dispatch_job(
        organization_id=dispatch.organization_id,
        dispatch_id=dispatch.id,
    )
    assert loaded is not None
    loaded.claim(
        owner="dispatcher-a",
        deadline=_now() + timedelta(seconds=30),
        now=_now(),
    )
    repository.save_dispatch_job(loaded)

    select_sql = str(
        db.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect())
    )
    assert "FOR UPDATE" in select_sql
    update_statement = db.execute.call_args_list[1].args[0]
    update_sql = str(update_statement.compile(dialect=postgresql.dialect()))
    assert "memory_turn_dispatch_jobs.status" in update_sql
    assert "memory_turn_dispatch_jobs.claim_generation" in update_sql
    assert update_statement.compile().params["claim_generation"] == 1


def test_unit_of_work_owns_transaction_commit_and_rollback():
    db = MagicMock(spec=Session)
    first_transaction = MagicMock()
    second_transaction = MagicMock()
    db.begin.side_effect = [first_transaction, second_transaction]
    uow = SqlAlchemyMemoryUnitOfWork(db)

    uow.begin()
    uow.commit()
    first_transaction.commit.assert_called_once_with()

    uow.begin()
    uow.rollback()
    second_transaction.rollback.assert_called_once_with()

    with pytest.raises(RuntimeError, match="not active"):
        uow.commit()


def test_unit_of_work_redacts_sqlalchemy_failure_details():
    db = MagicMock(spec=Session)
    transaction = MagicMock()
    transaction.commit.side_effect = SQLAlchemyError(
        "synthetic sensitive database parameter"
    )
    db.begin.return_value = transaction
    uow = SqlAlchemyMemoryUnitOfWork(db)

    uow.begin()
    with pytest.raises(MemoryAdapterUnavailableError) as failure:
        uow.commit()

    assert str(failure.value) == "memory.adapter_unavailable"
    assert failure.value.__cause__ is None
    assert "sensitive" not in str(failure.value)
    uow.rollback()


def test_due_dispatch_scan_is_bounded_ordered_and_skip_locked():
    db = MagicMock(spec=Session)
    db.execute.return_value.scalars.return_value.all.return_value = []
    repository = SqlAlchemyConversationMemoryRepository(db)

    assert repository.list_due_dispatch_jobs(now=_now(), limit=100) == ()

    statement = db.execute.call_args.args[0]
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "memory_turn_dispatch_jobs.status = 'pending'" in compiled
    assert "memory_turn_dispatch_jobs.status = 'reconcile_required'" in compiled
    assert "memory_turn_dispatch_jobs.status = 'terminal'" in compiled
    assert "memory_turn_dispatch_jobs.status = 'claimed'" in compiled
    assert "next_attempt_at <=" in compiled
    assert "claim_deadline_at <=" in compiled
    assert "ORDER BY coalesce" in compiled
    assert "LIMIT 100" in compiled
    assert "FOR UPDATE OF memory_turn_dispatch_jobs SKIP LOCKED" in compiled


@pytest.mark.parametrize("limit", (0, 501))
def test_due_dispatch_scan_rejects_unbounded_limits(limit: int):
    repository = SqlAlchemyConversationMemoryRepository(MagicMock(spec=Session))

    with pytest.raises(ValueError, match="between 1 and 500"):
        repository.list_due_dispatch_jobs(now=_now(), limit=limit)
