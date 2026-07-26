from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from apps.memory.adapters.persistence.repository import (
    SqlAlchemyConversationMemoryRepository,
    _access_grant_record,
    _dispatch_record,
    _session_record,
    _turn_record,
)
from apps.memory.application.execution import (
    ResolveConversationExecutionCommand,
    ResolveTerminalConversationExecutionCommand,
)
from apps.memory.domain.conversation import (
    AudienceKind,
    ConversationSession,
    ConversationTurn,
    MemoryTurnDispatchJob,
    RequestIdentity,
)
from apps.memory.domain.public_access import ConversationAccessGrant
from apps.shared.db.models.app import App
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import (
    DeploymentType,
    WorkflowDeployment,
)


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def _runtime_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "data": {
                    "variables": [
                        {
                            "id": "question",
                            "name": "question",
                            "type": "paragraph",
                            "required": True,
                            "max_length": 16_384,
                        }
                    ]
                },
            },
            {
                "id": "llm",
                "type": "llmNode",
                "data": {
                    "model_id": "fixed-model",
                    "task_type": "generate",
                    "system_prompt": "Answer safely.",
                    "user_prompt": "{{ question }}",
                    "referenced_variables": [
                        {
                            "name": "question",
                            "value_selector": ["start", "question"],
                        }
                    ],
                    "parameters": {"temperature": 0.2, "n": 1},
                    "memory": {
                        "enabled": True,
                        "channel": "conversation",
                        "readSource": "conversation_turns",
                        "writeMode": "none",
                        "maxTurns": 5,
                        "maxContextTokens": 1_200,
                        "strategy": "window",
                        "failurePolicy": "fail_node",
                    },
                },
            },
            {
                "id": "answer",
                "type": "answerNode",
                "data": {
                    "outputs": [
                        {
                            "variable": "answer",
                            "value_selector": ["llm", "text"],
                        }
                    ]
                },
            },
        ],
        "edges": [
            {"id": "start-llm", "source": "start", "target": "llm"},
            {"id": "llm-answer", "source": "llm", "target": "answer"},
        ],
    }


def _runtime_config() -> dict:
    return {
        "conversation_memory": {
            "contract_version": "conversation-memory-v1",
            "storage_generation": 1,
            "mapping_version": "conversation-mapping-v1",
            "memory_policy_version": "memory-policy-v1",
            "input": {"node_id": "start", "variable": "question"},
            "output": {"node_id": "answer", "variable": "answer"},
        }
    }


def _rows():
    organization_id = uuid.uuid4()
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    session = ConversationSession.create(
        session_id=uuid.uuid4(),
        organization_id=organization_id,
        app_id=app_id,
        workflow_id=workflow_id,
        deployment_id=deployment_id,
        deployment_version=3,
        deployment_snapshot_hash=None,
        mapping_version="conversation-mapping-v1",
        memory_policy_version="memory-policy-v1",
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        audience_kind=AudienceKind.PUBLIC_CHATBOT,
        subject_type=None,
        subject_id=None,
        idle_expires_at=NOW + timedelta(hours=1),
        absolute_expires_at=NOW + timedelta(days=1),
        now=NOW,
    )
    grant = ConversationAccessGrant.issue(
        grant_id=uuid.uuid4(),
        organization_id=organization_id,
        session_id=session.id,
        deployment_id=deployment_id,
        deployment_version=3,
        audience_kind=AudienceKind.PUBLIC_CHATBOT,
        verifier_hash="a" * 64,
        verifier_key_version="grant-v1",
        expires_at=NOW + timedelta(hours=1),
        now=NOW,
    )
    turn_id = uuid.uuid4()
    dispatch_id = uuid.uuid4()
    session.claim_turn(
        turn_id=turn_id,
        expected_lifecycle_revision=1,
        now=NOW,
    )
    turn = ConversationTurn.start(
        turn_id=turn_id,
        organization_id=organization_id,
        session_id=session.id,
        sequence=1,
        started_lifecycle_revision=1,
        request_identity=RequestIdentity("b" * 64, "c" * 64),
        user_entry_id=uuid.uuid4(),
        dispatch_id=dispatch_id,
        access_grant_id=grant.id,
        request_fingerprint_key_version="admission-v1",
        now=NOW,
    )
    dispatch = MemoryTurnDispatchJob.pending(
        dispatch_id=dispatch_id,
        organization_id=organization_id,
        session_id=session.id,
        turn_id=turn_id,
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
        max_attempts=3,
        now=NOW,
    )
    dispatch.claim(
        owner="gateway",
        deadline=NOW + timedelta(seconds=30),
        now=NOW,
    )
    app = App(
        id=app_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        active_deployment_id=deployment_id,
        url_slug="public-chatbot",
        auth_secret="",
        name="Public Chatbot",
        created_by=uuid.uuid4(),
    )
    workflow = Workflow(
        id=workflow_id,
        organization_id=organization_id,
        app_id=app_id,
        created_by=uuid.uuid4(),
    )
    deployment = WorkflowDeployment(
        id=deployment_id,
        app_id=app_id,
        version=3,
        type=DeploymentType.CHATBOT,
        graph_snapshot=_runtime_graph(),
        config=_runtime_config(),
        created_by=uuid.uuid4(),
        is_active=True,
    )
    command = ResolveConversationExecutionCommand(
        organization_id=organization_id,
        dispatch_id=dispatch_id,
        dispatch_claim_generation=dispatch.claim_generation,
        broker_message_id="message-1",
        turn_id=turn_id,
        memory_contract_version="conversation-memory-v1",
        storage_generation=1,
        minimum_worker_capability="memory-runtime-v1",
    )
    return (
        (
            _session_record(session),
            _turn_record(turn),
            _dispatch_record(dispatch),
            _access_grant_record(grant),
            app,
            workflow,
            deployment,
        ),
        command,
    )


def test_execution_scope_resolves_and_locks_the_complete_tenant_binding() -> None:
    rows, command = _rows()
    result = MagicMock()
    result.one_or_none.return_value = rows
    db = MagicMock(spec=Session)
    db.execute.return_value = result
    repository = SqlAlchemyConversationMemoryRepository(db)

    scope = repository.resolve_execution_scope(command, for_update=True)

    assert scope is not None
    assert scope.deployment.runtime_contract_ready is True
    assert scope.deployment.organization_id == command.organization_id
    assert scope.turn.id == command.turn_id
    assert scope.dispatch.id == command.dispatch_id
    assert scope.grant.session_id == scope.session.id
    statement = db.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF" in compiled
    assert "conversation_sessions.organization_id" in compiled
    assert "conversation_turns.dispatch_id" in compiled
    assert "conversation_access_grants.id = conversation_turns.access_grant_id" in compiled
    assert "workflow_deployments.id = apps.active_deployment_id" in compiled


def test_execution_scope_returns_none_without_a_complete_joined_binding() -> None:
    _rows_value, command = _rows()
    result = MagicMock()
    result.one_or_none.return_value = None
    db = MagicMock(spec=Session)
    db.execute.return_value = result

    assert (
        SqlAlchemyConversationMemoryRepository(db).resolve_execution_scope(
            command,
            for_update=False,
        )
        is None
    )
    statement = db.execute.call_args.args[0]
    assert "FOR UPDATE" not in str(
        statement.compile(dialect=postgresql.dialect())
    )


def test_terminal_scope_uses_frozen_deployment_and_revoked_grant_without_active_join() -> None:
    rows, command = _rows()
    rows = list(rows)
    rows[3].state = "revoked"
    rows[4].active_deployment_id = uuid.uuid4()
    rows[6].is_active = False
    result = MagicMock()
    result.one_or_none.return_value = tuple(rows)
    db = MagicMock(spec=Session)
    db.execute.return_value = result
    repository = SqlAlchemyConversationMemoryRepository(db)
    admission_id = uuid.uuid5(
        command.dispatch_id,
        "conversation-workflow-admission-v1",
    )

    scope = repository.resolve_terminal_execution_scope(
        ResolveTerminalConversationExecutionCommand(
            organization_id=command.organization_id,
            dispatch_id=command.dispatch_id,
            dispatch_claim_generation=command.dispatch_claim_generation,
            broker_message_id=command.broker_message_id,
            turn_id=command.turn_id,
            memory_contract_version=command.memory_contract_version,
            storage_generation=command.storage_generation,
            minimum_worker_capability=command.minimum_worker_capability,
            workflow_admission_id=admission_id,
            execution_id=uuid.uuid5(
                admission_id,
                "conversation-execution-v1",
            ),
            attempt_id=uuid.uuid4(),
        ),
        for_update=False,
    )

    assert scope is not None
    assert scope.grant.state.value == "revoked"
    assert scope.deployment.deployment_id == rows[6].id
    assert scope.deployment.runtime_contract_ready is False
    statement = db.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "workflow_deployments.id = apps.active_deployment_id" not in compiled
    assert "AND workflow_deployments.is_active" not in compiled
    assert "FOR UPDATE" not in compiled


def test_terminal_scope_locks_all_mutable_rows_before_reference_finalization() -> None:
    rows, command = _rows()
    result = MagicMock()
    result.one_or_none.return_value = rows
    db = MagicMock(spec=Session)
    db.execute.return_value = result
    repository = SqlAlchemyConversationMemoryRepository(db)
    admission_id = uuid.uuid5(
        command.dispatch_id,
        "conversation-workflow-admission-v1",
    )

    scope = repository.resolve_terminal_execution_scope(
        ResolveTerminalConversationExecutionCommand(
            organization_id=command.organization_id,
            dispatch_id=command.dispatch_id,
            dispatch_claim_generation=command.dispatch_claim_generation,
            broker_message_id=command.broker_message_id,
            turn_id=command.turn_id,
            memory_contract_version=command.memory_contract_version,
            storage_generation=command.storage_generation,
            minimum_worker_capability=command.minimum_worker_capability,
            workflow_admission_id=admission_id,
            execution_id=uuid.uuid5(
                admission_id,
                "conversation-execution-v1",
            ),
            attempt_id=uuid.uuid4(),
        ),
        for_update=True,
    )

    assert scope is not None
    statement = db.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF" in compiled
    assert "conversation_sessions" in compiled
    assert "conversation_turns" in compiled
    assert "conversation_access_grants" in compiled
    assert "memory_turn_dispatch_jobs" in compiled
    assert (
        scope.session.organization_id,
        scope.session.id,
    ) in repository._session_baselines
    assert (
        scope.turn.organization_id,
        scope.turn.session_id,
        scope.turn.id,
    ) in repository._turn_baselines
    assert (
        scope.dispatch.organization_id,
        scope.dispatch.id,
    ) in repository._dispatch_baselines
    assert scope.grant.id in repository._access_grant_baselines
