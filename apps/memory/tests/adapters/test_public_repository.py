from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from apps.memory.adapters.persistence.repository import (
    SqlAlchemyConversationMemoryRepository,
)
from apps.memory.domain.public_access import ConversationIdempotency
from apps.shared.db.models.app import App
from apps.shared.db.models.conversation_memory import (
    ConversationIdempotencyRecord,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment


def _now() -> datetime:
    return datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def _query_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def test_public_deployment_read_is_non_locking_and_ignores_embed_policy():
    organization_id = uuid.uuid4()
    app = App(
        id=uuid.uuid4(),
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        active_deployment_id=uuid.uuid4(),
        url_slug="public-chatbot",
        auth_secret="",
        name="Public Chatbot",
        created_by=uuid.uuid4(),
    )
    workflow = Workflow(
        id=app.workflow_id,
        organization_id=organization_id,
        app_id=app.id,
        created_by=uuid.uuid4(),
    )
    deployment = WorkflowDeployment(
        id=app.active_deployment_id,
        app_id=app.id,
        version=3,
        type=DeploymentType.CHATBOT,
        graph_snapshot={},
        config={"memory_policy_version": "memory-v2"},
        browser_access_policy={
            "embedding": {"parent_origins": ["https://parent.example"]}
        },
        created_by=uuid.uuid4(),
        is_active=True,
    )
    db = MagicMock(spec=Session)
    db.execute.return_value = MagicMock()
    db.execute.return_value.one_or_none.return_value = (app, workflow, deployment)
    repository = SqlAlchemyConversationMemoryRepository(db)

    binding = repository.resolve_public_deployment("public-chatbot")

    assert binding is not None
    assert binding.organization_id == organization_id
    assert binding.deployment_id == deployment.id
    assert binding.deployment_version == 3
    assert binding.mapping_version == "mapping-v1"
    assert binding.memory_policy_version == "memory-v2"
    statement = db.execute.call_args.args[0]
    assert "FOR UPDATE" not in str(statement.compile(dialect=postgresql.dialect()))


def test_memory_runtime_intent_is_detected_before_contract_validation():
    organization_id = uuid.uuid4()
    app = App(
        id=uuid.uuid4(),
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        active_deployment_id=uuid.uuid4(),
        url_slug="public-chatbot",
        auth_secret="",
        name="Public Chatbot",
        created_by=uuid.uuid4(),
    )
    workflow = Workflow(
        id=app.workflow_id,
        organization_id=organization_id,
        app_id=app.id,
        created_by=uuid.uuid4(),
    )
    deployment = WorkflowDeployment(
        id=app.active_deployment_id,
        app_id=app.id,
        version=3,
        type=DeploymentType.CHATBOT,
        graph_snapshot={
            "nodes": [
                {
                    "id": "llm",
                    "type": "llmNode",
                    "data": {"memory": {"enabled": "true"}},
                }
            ],
            "edges": [],
        },
        config={},
        created_by=uuid.uuid4(),
        is_active=True,
    )
    db = MagicMock(spec=Session)
    db.execute.return_value.one_or_none.return_value = (app, workflow, deployment)
    repository = SqlAlchemyConversationMemoryRepository(db)

    assert (
        repository.public_deployment_requires_conversation_runtime(
            "public-chatbot"
        )
        is True
    )
    statement = db.execute.call_args.args[0]
    assert "FOR UPDATE" not in str(statement.compile(dialect=postgresql.dialect()))


def test_public_deployment_mutation_lock_is_explicit():
    organization_id = uuid.uuid4()
    app = App(
        id=uuid.uuid4(),
        organization_id=organization_id,
        workflow_id=uuid.uuid4(),
        active_deployment_id=uuid.uuid4(),
        url_slug="public-chatbot",
        auth_secret="",
        name="Public Chatbot",
        created_by=uuid.uuid4(),
    )
    workflow = Workflow(
        id=app.workflow_id,
        organization_id=organization_id,
        app_id=app.id,
        created_by=uuid.uuid4(),
    )
    deployment = WorkflowDeployment(
        id=app.active_deployment_id,
        app_id=app.id,
        version=3,
        type=DeploymentType.CHATBOT,
        graph_snapshot={},
        config={},
        created_by=uuid.uuid4(),
        is_active=True,
    )
    db = MagicMock(spec=Session)
    db.execute.return_value.one_or_none.return_value = (app, workflow, deployment)
    repository = SqlAlchemyConversationMemoryRepository(db)

    assert repository.lock_public_deployment("public-chatbot") is not None

    statement = db.execute.call_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))


def test_public_app_resolver_uses_stable_app_identity_without_deployment_lock():
    organization_id = uuid.uuid4()
    app = App(
        id=uuid.uuid4(),
        organization_id=organization_id,
        url_slug="public-chatbot",
        auth_secret="",
        name="Public Chatbot",
        created_by=uuid.uuid4(),
    )
    db = MagicMock(spec=Session)
    db.execute.return_value.scalar_one_or_none.return_value = app
    repository = SqlAlchemyConversationMemoryRepository(db)

    binding = repository.resolve_public_app("public-chatbot")

    assert binding is not None
    assert (binding.organization_id, binding.app_id) == (organization_id, app.id)
    statement = db.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "workflow_deployments" not in compiled
    assert "FOR UPDATE" not in compiled


def test_idempotency_reservation_uses_database_conflict_gate_before_secret_creation():
    record = ConversationIdempotency.pending(
        record_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        operation="conversation.create",
        scope_digest="a" * 64,
        idempotency_key_hash="b" * 64,
        request_fingerprint="c" * 64,
        retention_expires_at=_now() + timedelta(days=1),
        now=_now(),
    )
    db = MagicMock(spec=Session)
    db.execute.return_value = _query_result(record.id)
    repository = SqlAlchemyConversationMemoryRepository(db)

    reservation = repository.reserve_idempotency(record)

    assert reservation.created is True
    statement = db.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert (
        "ON CONFLICT ON CONSTRAINT uq_conv_idempotency_scope_key DO NOTHING" in compiled
    )
    assert "RETURNING conversation_idempotency_records.id" in compiled


def test_expired_idempotency_claim_is_deleted_and_replaced_under_the_scope_lock():
    record = ConversationIdempotency.pending(
        record_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        operation="conversation.create",
        scope_digest="a" * 64,
        idempotency_key_hash="b" * 64,
        request_fingerprint="c" * 64,
        retention_expires_at=_now() + timedelta(days=1),
        now=_now(),
    )
    expired = ConversationIdempotencyRecord(
        id=uuid.uuid4(),
        organization_id=record.organization_id,
        operation=record.operation,
        scope_digest=record.scope_digest,
        idempotency_key_hash=record.idempotency_key_hash,
        request_fingerprint="d" * 64,
        status="pending",
        retention_expires_at=_now() - timedelta(seconds=1),
        created_at=_now() - timedelta(days=1),
        updated_at=_now() - timedelta(days=1),
    )
    delete_result = MagicMock()
    delete_result.rowcount = 1
    db = MagicMock(spec=Session)
    db.execute.side_effect = [
        _query_result(None),
        _query_result(expired),
        delete_result,
        _query_result(record.id),
    ]
    repository = SqlAlchemyConversationMemoryRepository(db)

    reservation = repository.reserve_idempotency(record)

    assert reservation.created is True
    assert reservation.record is record
    assert len(db.execute.call_args_list) == 4
    delete_statement = db.execute.call_args_list[2].args[0]
    delete_sql = str(delete_statement.compile(dialect=postgresql.dialect()))
    assert "DELETE FROM conversation_idempotency_records" in delete_sql
    assert "retention_expires_at" in delete_sql


def test_idempotency_lookup_excludes_expired_records_at_the_query_boundary():
    db = MagicMock(spec=Session)
    db.execute.return_value = _query_result(None)
    repository = SqlAlchemyConversationMemoryRepository(db)

    repository.find_idempotency(
        organization_id=uuid.uuid4(),
        operation="conversation.create",
        scope_digest="a" * 64,
        idempotency_key_hash="b" * 64,
        now=_now(),
    )

    compiled = db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "retention_expires_at" in str(compiled)
    assert _now() in compiled.params.values()


def test_authorized_idempotency_lookup_is_bound_to_app_key_and_verifier():
    organization_id = uuid.uuid4()
    app_id = uuid.uuid4()
    record = ConversationIdempotencyRecord(
        id=uuid.uuid4(),
        organization_id=organization_id,
        operation="conversation.delete",
        scope_digest="a" * 64,
        idempotency_key_hash="b" * 64,
        request_fingerprint="c" * 64,
        status="completed",
        authorization_app_id=app_id,
        authorization_verifier_key_version="cap-v1",
        authorization_verifier_hash="d" * 64,
        retention_expires_at=_now() + timedelta(days=1),
        created_at=_now(),
        updated_at=_now(),
    )
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [record]
    db = MagicMock(spec=Session)
    db.execute.return_value = rows
    repository = SqlAlchemyConversationMemoryRepository(db)
    candidates = (("cap-v2", "e" * 64), ("cap-v1", "d" * 64))

    loaded = repository.find_authorized_idempotency(
        organization_id=organization_id,
        app_id=app_id,
        operation="conversation.delete",
        idempotency_key_hash="b" * 64,
        verifier_candidates=candidates,
        now=_now(),
    )

    assert loaded is not None
    assert loaded.authorization_app_id == app_id
    assert loaded.authorization_verifier_hash == "d" * 64
    compiled = db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "authorization_app_id" in sql
    assert "authorization_verifier_key_version" in sql
    assert "authorization_verifier_hash" in sql
    assert "retention_expires_at" in sql
    assert _now() in compiled.params.values()
    assert list(candidates) in compiled.params.values()


def test_expired_secret_replay_cleanup_uses_a_bounded_skip_locked_delete():
    replay_ids = [uuid.uuid4(), uuid.uuid4()]
    selected = MagicMock()
    selected.scalars.return_value.all.return_value = replay_ids
    db = MagicMock(spec=Session)
    db.execute.side_effect = [selected, MagicMock()]
    repository = SqlAlchemyConversationMemoryRepository(db)

    deleted = repository.delete_expired_secret_replays(now=_now(), limit=500)

    assert deleted == 2
    select_statement = db.execute.call_args_list[0].args[0]
    delete_statement = db.execute.call_args_list[1].args[0]
    select_sql = str(select_statement.compile(dialect=postgresql.dialect()))
    delete_sql = str(delete_statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in select_sql
    assert "LIMIT" in select_sql
    assert "DELETE FROM conversation_secret_replays" in delete_sql


def test_expired_idempotency_cleanup_uses_a_bounded_skip_locked_delete():
    record_ids = [uuid.uuid4(), uuid.uuid4()]
    selected = MagicMock()
    selected.scalars.return_value.all.return_value = record_ids
    db = MagicMock(spec=Session)
    db.execute.side_effect = [selected, MagicMock()]
    repository = SqlAlchemyConversationMemoryRepository(db)

    deleted = repository.delete_expired_idempotency_records(now=_now(), limit=500)

    assert deleted == 2
    select_statement = db.execute.call_args_list[0].args[0]
    delete_statement = db.execute.call_args_list[1].args[0]
    select_sql = str(select_statement.compile(dialect=postgresql.dialect()))
    delete_sql = str(delete_statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in select_sql
    assert "LIMIT" in select_sql
    assert "retention_expires_at" in select_sql
    assert "DELETE FROM conversation_idempotency_records" in delete_sql
