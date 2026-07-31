import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Event

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from apps.gateway.adapters.db.agent_builder_repository import AgentBuilderRepository
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    GraphMutationBuilder,
    apply_graph_operations,
    canonical_graph_hash,
    materialize_candidate_graph,
)
from apps.gateway.services.agent_builder.parameter_task_service import (
    ParameterTaskService,
)
from apps.gateway.services.agent_builder.mutation_lifecycle import (
    GraphMutationLifecycleService,
)
from apps.gateway.services.agent_builder_service import AgentBuilderService
from apps.gateway.services import workflow_service as workflow_service_module
from apps.gateway.services.workflow_service import WorkflowService
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.agent_builder import AgentBuilderRequest, AgentBuilderSession
from apps.shared.db.models.app import App
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_node_secret import WorkflowNodeSecret
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from apps.shared.audit.actions import AuditAction
from apps.shared.schemas.agent_builder import (
    GraphMutationCompletionContext,
    GraphMutationSafeEnvelope,
)
from apps.shared.schemas.agent_builder import (
    AgentBuilderParameterGroup,
    AgentBuilderParameterTask,
    AgentBuilderParameterTaskDecisionRequest,
    AgentBuilderSessionCreateRequest,
    GraphMutationAcknowledgementRequest,
)
from apps.shared.schemas.workflow import WorkflowDraftRequest
from apps.shared.services.credential_encryption import CredentialEncryptionService
from apps.shared.services.workflow_node_secret_service import (
    WorkflowNodeSecretService,
    is_workflow_node_secret_reference,
)


ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_DISPOSABLE_DB_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DISPOSABLE_DB_PREFIX = "mbased_workflow_cas"
pytestmark = pytest.mark.skipif(
    os.getenv(RUN_DISPOSABLE_DB_ENV) != "1",
    reason=(
        f"set {RUN_DISPOSABLE_DB_ENV}=1 to run disposable PostgreSQL workflow CAS tests"
    ),
)


def _alembic_python() -> str:
    candidates = (
        ROOT_DIR / "apps" / "gateway" / ".venv" / "Scripts" / "python.exe",
        ROOT_DIR / "apps" / "gateway" / ".venv" / "bin" / "python",
    )
    for path in candidates:
        try:
            if path.exists():
                return str(path)
        except OSError:
            continue
    return sys.executable


def _run_alembic(database: str, config: DisposablePostgresConfig) -> None:
    result = subprocess.run(
        [
            _alembic_python(),
            "-m",
            "alembic",
            "-c",
            "apps/shared/alembic.ini",
            "upgrade",
            "heads",
        ],
        cwd=ROOT_DIR,
        env=config.subprocess_environment(database=database, root_dir=ROOT_DIR),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "alembic failed for disposable workflow CAS database; "
            "stdout/stderr omitted to avoid leaking local configuration"
        )


@pytest.fixture
def db_session(disposable_cas_engine):
    connection = disposable_cas_engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _node(node_id, node_type):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"title": node_id},
    }


def _draft_request(payload):
    data = dict(payload)
    context = data.get("mutation_context")
    if isinstance(context, dict):
        data.setdefault("expected_graph_hash", context["expected_base_graph_hash"])
        data.setdefault("expected_updated_at", context["expected_workflow_updated_at"])
    return WorkflowDraftRequest.model_validate(data)


def _workflow_node_secret_encryption() -> CredentialEncryptionService:
    return CredentialEncryptionService(
        {"test": Fernet.generate_key().decode("ascii")},
        "test",
        subject_label="Workflow node secret",
    )


def _normal_draft_request(graph, *, expected_graph_hash, expected_updated_at):
    return WorkflowDraftRequest.model_validate(
        {
            **graph,
            "expected_graph_hash": expected_graph_hash,
            "expected_updated_at": expected_updated_at,
        }
    )


def _fixture(db):
    suffix = uuid.uuid4().hex
    user = User(
        email=f"cas-{suffix}@example.invalid",
        name="CAS Actor",
        social_provider="local",
    )
    db.add(user)
    db.flush()
    organization = Organization(
        name=f"CAS {suffix}", created_by=user.id, managed_by=user.id
    )
    db.add(organization)
    db.flush()
    db.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            membership_state="active",
            organization_auth_state="manager",
        )
    )
    app = App(
        organization_id=organization.id,
        name="CAS App",
        url_slug=f"cas-{suffix}",
        auth_secret=None,
        created_by=user.id,
    )
    db.add(app)
    db.flush()
    now = datetime.now(timezone.utc)
    workflow = Workflow(
        organization_id=organization.id,
        app_id=app.id,
        created_by=user.id,
        graph={"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        updated_at=now,
    )
    db.add(workflow)
    db.flush()
    session = AgentBuilderSession(
        organization_id=organization.id,
        user_id=user.id,
        workflow_id=workflow.id,
        app_id=app.id,
    )
    db.add(session)
    db.flush()
    request_row = AgentBuilderRequest(
        session_id=session.id,
        organization_id=organization.id,
        user_id=user.id,
        status="graph_mutation_ready",
        response_payload={},
    )
    db.add(request_row)
    db.flush()
    return user, workflow, request_row


@pytest.fixture(scope="module")
def disposable_cas_engine():
    try:
        config = DisposablePostgresConfig.from_environment()
    except DisposablePostgresConfigurationError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL connection settings are not safely configured",
            pytrace=False,
        ) from None

    database = f"{DISPOSABLE_DB_PREFIX}_{uuid.uuid4().hex[:12]}"
    quoted_database = quote_disposable_database_name(
        database,
        prefix=DISPOSABLE_DB_PREFIX,
    )
    admin_engine = create_engine(
        config.database_url(config.maintenance_database),
        isolation_level="AUTOCOMMIT",
    )
    database_created = False
    test_engine = None
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {quoted_database}"))
        database_created = True
        extension_engine = create_engine(
            config.database_url(database),
            isolation_level="AUTOCOMMIT",
        )
        try:
            with extension_engine.connect() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        finally:
            extension_engine.dispose()
        _run_alembic(database, config)
        test_engine = create_engine(config.database_url(database))
        yield test_engine
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable or rejected the connection; "
            "connection details omitted",
            pytrace=False,
        ) from None
    finally:
        if test_engine is not None:
            test_engine.dispose()
        if database_created:
            try:
                with admin_engine.connect() as connection:
                    connection.execute(
                        text(
                            """
                            SELECT pg_terminate_backend(pid)
                            FROM pg_stat_activity
                            WHERE datname = :database
                              AND pid <> pg_backend_pid()
                            """
                        ),
                        {"database": database},
                    )
                    connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup could not connect; "
                    "connection details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()


def _seed_committed_cas_fixture(test_engine):
    session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    base_graph = {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
    ids = {
        "user": uuid.uuid4(),
        "organization": uuid.uuid4(),
        "app": uuid.uuid4(),
        "workflow": uuid.uuid4(),
        "session": uuid.uuid4(),
        "request": uuid.uuid4(),
    }
    with session_factory() as db:
        db.add(
            User(
                id=ids["user"],
                email=f"cas-race-{suffix}@example.invalid",
                name="CAS Race Actor",
                social_provider="local",
            )
        )
        db.flush()
        db.add(
            Organization(
                id=ids["organization"],
                name=f"CAS Race {suffix}",
                created_by=ids["user"],
                managed_by=ids["user"],
            )
        )
        db.flush()
        db.add(
            OrganizationMembership(
                organization_id=ids["organization"],
                user_id=ids["user"],
                membership_state="active",
                organization_auth_state="manager",
            )
        )
        db.add(
            App(
                id=ids["app"],
                organization_id=ids["organization"],
                name="CAS Race App",
                url_slug=f"cas-race-{suffix}",
                auth_secret=None,
                created_by=ids["user"],
            )
        )
        db.flush()
        now = datetime.now(timezone.utc)
        db.add(
            Workflow(
                id=ids["workflow"],
                organization_id=ids["organization"],
                app_id=ids["app"],
                created_by=ids["user"],
                graph=base_graph,
                updated_at=now,
            )
        )
        db.flush()
        db.add(
            AgentBuilderSession(
                id=ids["session"],
                organization_id=ids["organization"],
                user_id=ids["user"],
                workflow_id=ids["workflow"],
                app_id=ids["app"],
                protocol_version="direct_edit_v1",
            )
        )
        db.flush()
        db.add(
            AgentBuilderRequest(
                id=ids["request"],
                session_id=ids["session"],
                organization_id=ids["organization"],
                user_id=ids["user"],
                status="graph_mutation_ready",
                response_payload={},
            )
        )
        db.commit()
    return ids


def _store_committed_agent_builder_envelope(test_engine, ids):
    session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    with session_factory() as db:
        workflow = db.get(Workflow, ids["workflow"])
        request_row = db.get(AgentBuilderRequest, ids["request"])
        mutation = GraphMutationBuilder().build(
            operation_id=uuid.uuid4(),
            kind="initial_graph",
            generation_mode="configure_and_generate",
            workflow_id=workflow.id,
            base_graph=workflow.graph,
            expected_workflow_updated_at=workflow.updated_at,
            operations=[
                {"op": "add_node", "node": _node("agent-start", "startNode")},
                {"op": "add_node", "node": _node("agent-answer", "answerNode")},
                {
                    "op": "add_edge",
                    "edge": {
                        "id": "agent-edge",
                        "source": "agent-start",
                        "target": "agent-answer",
                    },
                },
            ],
        )
        AgentBuilderRepository().store_envelope(
            request_row,
            GraphMutationSafeEnvelope.from_mutation(mutation),
        )
        db.commit()
        return mutation


def _race_save_draft(session_factory, *, workflow_id, user_id, request, label, barrier):
    with session_factory() as db:
        barrier.wait(timeout=10)
        try:
            return {
                "label": label,
                "outcome": "success",
                "result": WorkflowService.save_draft(
                    db,
                    str(workflow_id),
                    request,
                    user_id=str(user_id),
                ),
            }
        except HTTPException as exc:
            db.rollback()
            return {
                "label": label,
                "outcome": "conflict",
                "status_code": exc.status_code,
                "detail": exc.detail,
            }


def test_actual_postgresql_autosync_race_has_single_cas_winner(
    disposable_cas_engine,
):
    ids = _seed_committed_cas_fixture(disposable_cas_engine)
    session_factory = sessionmaker(bind=disposable_cas_engine, expire_on_commit=False)
    with session_factory() as db:
        workflow = db.get(Workflow, ids["workflow"])
        base_hash = canonical_graph_hash(workflow.graph)
        base_updated_at = workflow.updated_at
    candidates = {
        "autosync-a": {
            "nodes": [_node("autosync-a", "startNode")],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        },
        "autosync-b": {
            "nodes": [_node("autosync-b", "answerNode")],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        },
    }
    barrier = Barrier(2)

    def save(label):
        return _race_save_draft(
            session_factory,
            workflow_id=ids["workflow"],
            user_id=ids["user"],
            request=_normal_draft_request(
                candidates[label],
                expected_graph_hash=base_hash,
                expected_updated_at=base_updated_at,
            ),
            label=label,
            barrier=barrier,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, candidates))

    successes = [item for item in outcomes if item["outcome"] == "success"]
    conflicts = [item for item in outcomes if item["outcome"] == "conflict"]

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0]["status_code"] == 409
    assert conflicts[0]["detail"] == "stale_graph"

    winner = successes[0]
    loser = conflicts[0]
    with session_factory() as db:
        persisted = db.get(Workflow, ids["workflow"])
        persisted_node_ids = [node["id"] for node in persisted.graph["nodes"]]

    assert canonical_graph_hash(candidates[winner["label"]]) == winner["result"][
        "graph_hash"
    ]
    assert canonical_graph_hash(persisted.graph) == winner["result"]["graph_hash"]
    assert persisted.updated_at.isoformat() == winner["result"]["updated_at"]
    assert persisted_node_ids == [candidates[winner["label"]]["nodes"][0]["id"]]
    assert candidates[loser["label"]]["nodes"][0]["id"] not in persisted_node_ids


def test_draft_read_secret_migration_cannot_overwrite_concurrent_cas_save(
    disposable_cas_engine,
    monkeypatch,
):
    ids = _seed_committed_cas_fixture(disposable_cas_engine)
    session_factory = sessionmaker(bind=disposable_cas_engine, expire_on_commit=False)
    encryption = _workflow_node_secret_encryption()
    with session_factory() as db:
        workflow = db.get(Workflow, ids["workflow"])
        reference = WorkflowNodeSecretService.create_reference(
            db,
            encryption=encryption,
            workflow_id=workflow.id,
            organization_id=workflow.organization_id,
            user_id=ids["user"],
            node_id="slack-1",
            node_type="slackPostNode",
            parameter_key="bot_token",
            secret_value="synthetic-current-token",
        )
        workflow.graph = {
            "nodes": [
                {
                    "id": "slack-1",
                    "type": "slackPostNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "title": "Slack",
                        "slackMode": "api",
                        "authConfig": {"token": "synthetic-legacy-token"},
                        "channel": "C-OLD",
                        "message": "hello",
                    },
                }
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
        db.commit()
        db.refresh(workflow)
        base_hash = canonical_graph_hash(workflow.graph)
        base_updated_at = workflow.updated_at

    latest_graph = {
        "nodes": [
            {
                "id": "slack-1",
                "type": "slackPostNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "Slack",
                    "slackMode": "api",
                    "authConfig": {"token": reference},
                    "channel": "C-LATEST",
                    "message": "hello",
                },
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    save_request = _normal_draft_request(
        latest_graph,
        expected_graph_hash=base_hash,
        expected_updated_at=base_updated_at,
    )
    migration_entered = Event()
    allow_migration = Event()
    real_migrate = workflow_service_module.migrate_legacy_workflow_graph_secrets

    def blocking_migration(*args, **kwargs):
        migration_entered.set()
        assert allow_migration.wait(timeout=10)
        return real_migrate(*args, **kwargs)

    monkeypatch.setattr(
        workflow_service_module,
        "migrate_legacy_workflow_graph_secrets",
        blocking_migration,
    )
    monkeypatch.setattr(
        workflow_service_module,
        "get_workflow_node_secret_encryption_service",
        lambda: encryption,
    )

    def read_draft():
        with session_factory() as db:
            return WorkflowService.get_draft(db, str(ids["workflow"]))

    def save_latest():
        with session_factory() as db:
            try:
                return {
                    "outcome": "success",
                    "result": WorkflowService.save_draft(
                        db,
                        str(ids["workflow"]),
                        save_request,
                        user_id=str(ids["user"]),
                    ),
                }
            except HTTPException as exc:
                db.rollback()
                return {
                    "outcome": "conflict",
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                }

    with ThreadPoolExecutor(max_workers=2) as executor:
        read_future = executor.submit(read_draft)
        assert migration_entered.wait(timeout=10)
        save_future = executor.submit(save_latest)
        try:
            early_save = save_future.result(timeout=1)
        except FutureTimeoutError:
            early_save = None
        allow_migration.set()
        read_future.result(timeout=10)
        save_outcome = early_save or save_future.result(timeout=10)

    with session_factory() as db:
        persisted = db.get(Workflow, ids["workflow"])
        secret_rows = db.query(WorkflowNodeSecret).all()

    if save_outcome["outcome"] == "success":
        assert canonical_graph_hash(persisted.graph) == save_outcome["result"][
            "graph_hash"
        ]
        assert persisted.graph["nodes"][0]["data"]["channel"] == "C-LATEST"
    else:
        assert save_outcome["status_code"] == 409
        assert save_outcome["detail"] == "stale_graph"
        assert persisted.graph["nodes"][0]["data"]["channel"] == "C-OLD"
    stored_token = persisted.graph["nodes"][0]["data"]["authConfig"]["token"]
    assert is_workflow_node_secret_reference(stored_token)
    assert "synthetic-legacy-token" not in str(persisted.graph)
    assert len(secret_rows) in {1, 2}


def test_save_draft_rejects_unknown_workflow_node_secret_reference(
    disposable_cas_engine,
):
    ids = _seed_committed_cas_fixture(disposable_cas_engine)
    session_factory = sessionmaker(bind=disposable_cas_engine, expire_on_commit=False)
    with session_factory() as db:
        workflow = db.get(Workflow, ids["workflow"])
        base_hash = canonical_graph_hash(workflow.graph)
        base_updated_at = workflow.updated_at

    graph = {
        "nodes": [
            {
                "id": "github-1",
                "type": "githubNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "GitHub",
                    "action": "get_pr",
                    "api_token": f"workflow-node-secret://{uuid.uuid4()}",
                    "repo_owner": "octo",
                    "repo_name": "repo",
                    "pr_number": "15",
                },
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    request = _normal_draft_request(
        graph,
        expected_graph_hash=base_hash,
        expected_updated_at=base_updated_at,
    )

    with session_factory() as db, pytest.raises(HTTPException) as captured:
        WorkflowService.save_draft(
            db,
            str(ids["workflow"]),
            request,
            user_id=str(ids["user"]),
        )

    assert captured.value.status_code == 422
    assert captured.value.detail == "workflow.node_secret_reference_invalid"


def test_actual_postgresql_autosync_and_agent_builder_race_has_no_silent_overwrite(
    disposable_cas_engine,
):
    ids = _seed_committed_cas_fixture(disposable_cas_engine)
    mutation = _store_committed_agent_builder_envelope(disposable_cas_engine, ids)
    session_factory = sessionmaker(bind=disposable_cas_engine, expire_on_commit=False)
    autosync_graph = {
        "nodes": [_node("manual-autosync", "templateNode")],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    with session_factory() as db:
        workflow = db.get(Workflow, ids["workflow"])
        agent_graph = apply_graph_operations(workflow.graph, mutation.operations)
    requests = {
        "autosync": _normal_draft_request(
            autosync_graph,
            expected_graph_hash=mutation.base_graph_hash,
            expected_updated_at=mutation.expected_workflow_updated_at,
        ),
        "agent": _draft_request(
            {
                **agent_graph,
                "mutation_context": {
                    "operation_id": mutation.operation_id,
                    "action": "apply",
                    "expected_base_graph_hash": mutation.base_graph_hash,
                    "expected_workflow_updated_at": (
                        mutation.expected_workflow_updated_at
                    ),
                    "catalog_version": 3,
                },
            }
        ),
    }
    barrier = Barrier(2)

    def save(label):
        return _race_save_draft(
            session_factory,
            workflow_id=ids["workflow"],
            user_id=ids["user"],
            request=requests[label],
            label=label,
            barrier=barrier,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, requests))

    successes = [item for item in outcomes if item["outcome"] == "success"]
    conflicts = [item for item in outcomes if item["outcome"] == "conflict"]

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0]["status_code"] == 409
    assert conflicts[0]["detail"] == "stale_graph"

    winner = successes[0]
    with session_factory() as db:
        persisted = db.get(Workflow, ids["workflow"])
        request_row = db.get(AgentBuilderRequest, ids["request"])
        envelope = AgentBuilderRepository().find_envelope(
            request_row,
            mutation.operation_id,
        )
        boundary = AgentBuilderRepository().load_history_boundary(request_row)

    persisted_node_ids = {node["id"] for node in persisted.graph["nodes"]}
    assert canonical_graph_hash(persisted.graph) == winner["result"]["graph_hash"]
    assert persisted.updated_at.isoformat() == winner["result"]["updated_at"]

    if winner["label"] == "agent":
        assert winner["result"]["operation_id"] == str(mutation.operation_id)
        assert winner["result"]["graph_hash"] == mutation.expected_result_graph_hash
        assert persisted_node_ids == {"agent-start", "agent-answer"}
        assert "manual-autosync" not in persisted_node_ids
        assert envelope["status"] == "pending_ack"
        assert envelope["result_graph_hash"] == mutation.expected_result_graph_hash
        assert envelope["saved_workflow_updated_at"] == winner["result"]["updated_at"]
        assert boundary["status"] == "pending_ack"
    else:
        assert "operation_id" not in winner["result"]
        assert persisted_node_ids == {"manual-autosync"}
        assert {"agent-start", "agent-answer"}.isdisjoint(persisted_node_ids)
        assert envelope["status"] == "pending_apply"
        assert envelope.get("result_graph_hash") is None
        assert boundary["status"] == "pending_apply"
        assert boundary["pending_operation_id"] == str(mutation.operation_id)
        assert boundary["latest_final_graph"] is None


def test_actual_postgresql_stale_identity_map_conflicts_and_preserves_new_graph(
    disposable_cas_engine,
):
    ids = _seed_committed_cas_fixture(disposable_cas_engine)
    session_factory = sessionmaker(bind=disposable_cas_engine, expire_on_commit=False)
    graph_b = {
        "nodes": [_node("session-b", "answerNode")],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    graph_a = {
        "nodes": [_node("session-a", "startNode")],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }

    session_a = session_factory()
    try:
        workflow_a = session_a.get(Workflow, ids["workflow"])
        base_hash = canonical_graph_hash(workflow_a.graph)
        base_updated_at = workflow_a.updated_at

        with session_factory() as session_b:
            workflow_b = session_b.get(Workflow, ids["workflow"])
            workflow_b.graph = graph_b
            workflow_b.updated_by = ids["user"]
            session_b.commit()
            session_b_updated_at = workflow_b.updated_at

        assert workflow_a.graph != graph_b
        assert workflow_a.updated_at == base_updated_at

        with pytest.raises(HTTPException) as exc:
            WorkflowService.save_draft(
                session_a,
                str(ids["workflow"]),
                _normal_draft_request(
                    graph_a,
                    expected_graph_hash=base_hash,
                    expected_updated_at=base_updated_at,
                ),
                user_id=str(ids["user"]),
            )

        assert exc.value.status_code == 409
        assert exc.value.detail == "stale_graph"
        session_a.rollback()
    finally:
        session_a.close()

    with session_factory() as session_c:
        persisted = session_c.get(Workflow, ids["workflow"])
        assert persisted.graph == graph_b
        assert canonical_graph_hash(persisted.graph) == canonical_graph_hash(graph_b)
        assert persisted.updated_at == session_b_updated_at


def test_first_cas_save_wins_and_same_operation_retry_returns_canonical_result(db_session):
    user, workflow, request_row = _fixture(db_session)
    mutation = GraphMutationBuilder().build(
        operation_id=uuid.uuid4(),
        kind="initial_graph",
        generation_mode="configure_and_generate",
        workflow_id=workflow.id,
        base_graph=workflow.graph,
        expected_workflow_updated_at=workflow.updated_at,
        operations=[
            {"op": "add_node", "node": _node("start", "startNode")},
            {"op": "add_node", "node": _node("answer", "answerNode")},
            {
                "op": "add_edge",
                "edge": {"id": "e1", "source": "start", "target": "answer"},
            },
        ],
    )
    AgentBuilderRepository().store_envelope(
        request_row, GraphMutationSafeEnvelope.from_mutation(mutation)
    )
    db_session.flush()
    result_graph = apply_graph_operations(workflow.graph, mutation.operations)
    payload = _draft_request(
        {
            **result_graph,
            "mutation_context": {
                "operation_id": mutation.operation_id,
                "action": "apply",
                "expected_base_graph_hash": mutation.base_graph_hash,
                "expected_workflow_updated_at": mutation.expected_workflow_updated_at,
                "catalog_version": 3,
            },
        }
    )

    result = WorkflowService.save_draft(
        db_session, str(workflow.id), payload, user_id=str(user.id)
    )
    assert result["graph_hash"] == mutation.expected_result_graph_hash
    session = db_session.get(AgentBuilderSession, request_row.session_id)
    session.protocol_version = "direct_edit_v1"
    recovered = AgentBuilderService(
        db_session,
        user=user,
        organization_id=workflow.organization_id,
    ).get_session(session.id)
    assert recovered.active_graph_mutation["status"] == "pending_ack"

    retried = WorkflowService.save_draft(
        db_session, str(workflow.id), payload, user_id=str(user.id)
    )

    assert retried["operation_id"] == result["operation_id"]
    assert retried["graph_hash"] == result["graph_hash"]
    assert retried["updated_at"] == result["updated_at"]


def test_normal_draft_save_returns_canonical_metadata_and_rejects_stale_retry(
    db_session,
):
    user, workflow, _request_row = _fixture(db_session)
    base_hash = canonical_graph_hash(workflow.graph)
    base_updated_at = workflow.updated_at
    first_graph = {
        "nodes": [_node("manual-start", "startNode")],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }

    saved = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        _normal_draft_request(
            first_graph,
            expected_graph_hash=base_hash,
            expected_updated_at=base_updated_at,
        ),
        user_id=str(user.id),
    )

    assert saved["status"] == "success"
    assert saved["workflow_id"] == str(workflow.id)
    assert saved["graph_hash"] == canonical_graph_hash(workflow.graph)
    assert saved["updated_at"] == workflow.updated_at.isoformat()

    stale_graph = {
        "nodes": [_node("stale-overwrite", "answerNode")],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    with pytest.raises(HTTPException) as exc:
        WorkflowService.save_draft(
            db_session,
            str(workflow.id),
            _normal_draft_request(
                stale_graph,
                expected_graph_hash=base_hash,
                expected_updated_at=base_updated_at,
            ),
            user_id=str(user.id),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "stale_graph"
    assert [node["id"] for node in workflow.graph["nodes"]] == ["manual-start"]


def test_agent_builder_save_makes_same_base_autosync_stale(db_session):
    user, workflow, request_row = _fixture(db_session)
    mutation = GraphMutationBuilder().build(
        operation_id=uuid.uuid4(),
        kind="initial_graph",
        generation_mode="configure_and_generate",
        workflow_id=workflow.id,
        base_graph=workflow.graph,
        expected_workflow_updated_at=workflow.updated_at,
        operations=[
            {"op": "add_node", "node": _node("agent-start", "startNode")},
            {"op": "add_node", "node": _node("agent-answer", "answerNode")},
            {
                "op": "add_edge",
                "edge": {
                    "id": "agent-edge",
                    "source": "agent-start",
                    "target": "agent-answer",
                },
            },
        ],
    )
    AgentBuilderRepository().store_envelope(
        request_row,
        GraphMutationSafeEnvelope.from_mutation(mutation),
    )
    db_session.flush()
    result_graph = apply_graph_operations(workflow.graph, mutation.operations)

    saved = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        _draft_request(
            {
                **result_graph,
                "mutation_context": {
                    "operation_id": mutation.operation_id,
                    "action": "apply",
                    "expected_base_graph_hash": mutation.base_graph_hash,
                    "expected_workflow_updated_at": (
                        mutation.expected_workflow_updated_at
                    ),
                    "catalog_version": 3,
                },
            }
        ),
        user_id=str(user.id),
    )

    with pytest.raises(HTTPException) as exc:
        WorkflowService.save_draft(
            db_session,
            str(workflow.id),
            _normal_draft_request(
                {
                    "nodes": [_node("autosync-stale", "templateNode")],
                    "edges": [],
                    "viewport": {"x": 0, "y": 0, "zoom": 1},
                },
                expected_graph_hash=mutation.base_graph_hash,
                expected_updated_at=mutation.expected_workflow_updated_at,
            ),
            user_id=str(user.id),
        )

    assert saved["graph_hash"] == mutation.expected_result_graph_hash
    assert exc.value.status_code == 409
    assert exc.value.detail == "stale_graph"
    assert {node["id"] for node in workflow.graph["nodes"]} == {
        "agent-start",
        "agent-answer",
    }


def test_null_and_direct_protocol_sessions_are_read_without_backfill(db_session):
    user, workflow, request_row = _fixture(db_session)
    legacy = db_session.get(AgentBuilderSession, request_row.session_id)
    legacy.protocol_version = None
    db_session.flush()
    service = AgentBuilderService(
        db_session,
        user=user,
        organization_id=workflow.organization_id,
    )

    legacy_response = service.get_session(legacy.id)
    direct_response = service.create_or_restore_session(
        AgentBuilderSessionCreateRequest(workflow_id=workflow.id)
    )

    assert legacy_response.status == "stale_protocol"
    assert legacy_response.protocol_version is None
    assert direct_response.protocol_version == "direct_edit_v1"
    assert direct_response.session_id != legacy.id
    assert legacy.protocol_version is None


def test_second_decision_for_same_task_version_is_rejected_by_persisted_lock(db_session):
    user, workflow, request_row = _fixture(db_session)
    workflow.graph = {
        "nodes": [
            {
                "id": "schedule",
                "type": "scheduleTrigger",
                "position": {"x": 0, "y": 0},
                "data": {"title": "Schedule", "timezone": "UTC"},
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    group_id = uuid.uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_schedule",
        node_id="schedule",
        node_type="scheduleTrigger",
        parameter_key="cron_expression",
        label="실행 일정",
        input_type="text",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="실행 일정이 필요합니다.",
        input_guidance="cron 식을 입력하세요.",
    )
    AgentBuilderRepository().store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="active",
            tasks=[task],
        ),
    )
    db_session.flush()
    service = ParameterTaskService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    )
    first = AgentBuilderParameterTaskDecisionRequest.model_validate(
        {
            "operation_id": str(uuid.uuid4()),
            "expected_task_version": 1,
            "action": "set",
            "value": {"kind": "text", "value": "0 9 * * *"},
        }
    )
    second = first.model_copy(update={"operation_id": uuid.uuid4()})

    issued = service.decide(request_row.session_id, task.task_id, first)
    assert issued.awaiting_persistence_ack is True
    with pytest.raises(HTTPException) as exc:
        service.decide(request_row.session_id, task.task_id, second)
    assert exc.value.status_code == 409
    assert exc.value.detail == "task_conflict"


def test_session_recovery_blocks_unapplied_parameter_operation_and_reopens_task(
    db_session,
):
    user, workflow, request_row = _fixture(db_session)
    session = db_session.get(AgentBuilderSession, request_row.session_id)
    session.protocol_version = "direct_edit_v1"
    workflow.graph = {
        "nodes": [
            {
                "id": "schedule",
                "type": "scheduleTrigger",
                "position": {"x": 0, "y": 0},
                "data": {"title": "Schedule", "timezone": "UTC"},
            }
        ],
        "edges": [],
    }
    group_id = uuid.uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_schedule",
        node_id="schedule",
        node_type="scheduleTrigger",
        parameter_key="cron_expression",
        label="실행 일정",
        input_type="text",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="실행 일정이 필요합니다.",
        input_guidance="cron 식을 입력하세요.",
    )
    repository = AgentBuilderRepository()
    repository.store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="active",
            tasks=[task],
        ),
    )
    db_session.flush()
    task_service = ParameterTaskService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    )
    first_operation = uuid.uuid4()
    task_service.decide(
        session.id,
        task.task_id,
        AgentBuilderParameterTaskDecisionRequest.model_validate(
            {
                "operation_id": first_operation,
                "expected_task_version": 1,
                "action": "set",
                "value": {"kind": "text", "value": "0 9 * * *"},
            }
        ),
    )

    recovered = AgentBuilderService(
        db_session,
        user=user,
        organization_id=workflow.organization_id,
    ).get_session(session.id)

    assert recovered.status == "operation_payload_unavailable"
    assert recovered.active_graph_mutation["status"] == "blocked"
    assert recovered.parameter_group is not None
    assert recovered.parameter_group.status == "active"
    assert recovered.parameter_group.tasks[0].status == "active"
    assert repository.find_pending_task_decision(request_row, first_operation) is None
    blocked_audit_count = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.action == "agent_builder_graph_mutation.blocked",
            AuditLog.target_id == str(workflow.id),
        )
        .count()
    )
    AgentBuilderService(
        db_session,
        user=user,
        organization_id=workflow.organization_id,
    ).get_session(session.id)
    assert (
        db_session.query(AuditLog)
        .filter(
            AuditLog.action == "agent_builder_graph_mutation.blocked",
            AuditLog.target_id == str(workflow.id),
        )
        .count()
        == blocked_audit_count
    )

    retried = task_service.decide(
        session.id,
        task.task_id,
        AgentBuilderParameterTaskDecisionRequest.model_validate(
            {
                "operation_id": uuid.uuid4(),
                "expected_task_version": 1,
                "action": "set",
                "value": {"kind": "text", "value": "0 10 * * *"},
            }
        ),
    )
    assert retried.awaiting_persistence_ack is True


def test_parameter_acknowledgement_retry_is_idempotent(db_session):
    user, workflow, request_row = _fixture(db_session)
    workflow.graph = {
        "nodes": [
            {
                "id": "schedule",
                "type": "scheduleTrigger",
                "position": {"x": 0, "y": 0},
                "data": {"title": "Schedule", "timezone": "UTC"},
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    group_id = uuid.uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_schedule",
        node_id="schedule",
        node_type="scheduleTrigger",
        parameter_key="cron_expression",
        label="Schedule",
        input_type="text",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="A schedule is required.",
        input_guidance="Enter a cron expression.",
    )
    repository = AgentBuilderRepository()
    repository.store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="active",
            tasks=[task],
        ),
    )
    db_session.flush()
    operation_id = uuid.uuid4()
    issued = ParameterTaskService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    ).decide(
        request_row.session_id,
        task.task_id,
        AgentBuilderParameterTaskDecisionRequest.model_validate(
            {
                "operation_id": operation_id,
                "expected_task_version": 1,
                "action": "set",
                "value": {"kind": "text", "value": "0 9 * * *"},
            }
        ),
    )
    assert issued.graph_mutation is not None
    result_graph = apply_graph_operations(
        workflow.graph, issued.graph_mutation.operations
    )
    saved = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        _draft_request(
            {
                **result_graph,
                "mutation_context": {
                    "operation_id": operation_id,
                    "action": "apply",
                    "expected_base_graph_hash": (
                        issued.graph_mutation.base_graph_hash
                    ),
                    "expected_workflow_updated_at": (
                        issued.graph_mutation.expected_workflow_updated_at
                    ),
                    "catalog_version": 3,
                },
            }
        ),
        user_id=str(user.id),
    )
    acknowledgement = GraphMutationAcknowledgementRequest.model_validate(
        {
            "workflow_id": workflow.id,
            "graph_hash": saved["graph_hash"],
            "updated_at": saved["updated_at"],
        }
    )
    lifecycle = GraphMutationLifecycleService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    )

    first = lifecycle.acknowledge(
        request_row.session_id, operation_id, acknowledgement
    )
    audit_count = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "agent_builder.graph_mutation.acknowledged")
        .count()
    )
    session = db_session.get(AgentBuilderSession, request_row.session_id)
    session.protocol_version = "direct_edit_v1"
    recovered = AgentBuilderService(
        db_session,
        user=user,
        organization_id=workflow.organization_id,
    ).get_session(session.id)
    second = lifecycle.acknowledge(
        request_row.session_id, operation_id, acknowledgement
    )

    assert recovered.active_graph_mutation["operation_id"] == str(operation_id)
    assert recovered.active_graph_mutation["status"] == "acknowledged"
    assert recovered.active_graph_mutation["result_graph_hash"] == saved["graph_hash"]
    assert recovered.active_graph_mutation["saved_workflow_updated_at"] == saved[
        "updated_at"
    ]
    assert "operations" not in recovered.active_graph_mutation
    assert "value" not in recovered.active_graph_mutation
    assert second == first
    assert second.parameter_group is not None
    assert second.parameter_group.tasks[0].status == "completed"
    assert second.parameter_group.tasks[0].task_version == 2
    assert first.completed_task_id == task.task_id
    assert second.completed_task_id == task.task_id
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "agent_builder.graph_mutation.acknowledged")
        .count()
        == audit_count
    )


@pytest.mark.parametrize(
    (
        "node_type",
        "parameter_key",
        "input_type",
        "decision_value",
        "initial_data",
        "expected_value",
    ),
    [
        (
            "slackPostNode",
            "channel",
            "text",
            {"kind": "text", "value": "C123"},
            {
                "title": "Slack",
                "body": '{"text":"{{result}}"}',
                "channel": "",
                "configuration_state": "unresolved",
            },
            "C123",
        ),
        (
            "githubNode",
            "pr_number",
            "number",
            {"kind": "number", "value": 15},
            {
                "title": "GitHub PR",
                "action": "get_pr",
                "api_token": "",
                "repo_owner": "octo",
                "repo_name": "repo",
                "pr_number": "",
                "referenced_variables": [],
                "configuration_state": "unresolved",
            },
            "15",
        ),
    ],
)
def test_external_parameter_decision_persists_and_acknowledges(
    db_session,
    node_type,
    parameter_key,
    input_type,
    decision_value,
    initial_data,
    expected_value,
):
    user, workflow, request_row = _fixture(db_session)
    session = db_session.get(AgentBuilderSession, request_row.session_id)
    session.protocol_version = "direct_edit_v1"
    workflow.graph = {
        "nodes": [
            {
                "id": "target",
                "type": node_type,
                "position": {"x": 0, "y": 0},
                "data": initial_data,
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    group_id = uuid.uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_target",
        node_id="target",
        node_type=node_type,
        parameter_key=parameter_key,
        label=parameter_key,
        input_type=input_type,
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="The runtime parameter is required.",
        input_guidance="Enter the runtime parameter.",
    )
    repository = AgentBuilderRepository()
    repository.store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="active",
            tasks=[task],
        ),
    )
    db_session.flush()

    operation_id = uuid.uuid4()
    issued = ParameterTaskService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    ).decide(
        session.id,
        task.task_id,
        AgentBuilderParameterTaskDecisionRequest.model_validate(
            {
                "operation_id": operation_id,
                "expected_task_version": 1,
                "action": "set",
                "value": decision_value,
            }
        ),
    )
    assert issued.graph_mutation is not None
    result_graph = apply_graph_operations(
        workflow.graph,
        issued.graph_mutation.operations,
    )
    saved = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        _draft_request(
            {
                **result_graph,
                "mutation_context": {
                    "operation_id": operation_id,
                    "action": "apply",
                    "expected_base_graph_hash": (
                        issued.graph_mutation.base_graph_hash
                    ),
                    "expected_workflow_updated_at": (
                        issued.graph_mutation.expected_workflow_updated_at
                    ),
                    "catalog_version": 3,
                },
            }
        ),
        user_id=str(user.id),
    )
    acknowledged = GraphMutationLifecycleService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    ).acknowledge(
        session.id,
        operation_id,
        GraphMutationAcknowledgementRequest.model_validate(
            {
                "workflow_id": workflow.id,
                "graph_hash": saved["graph_hash"],
                "updated_at": saved["updated_at"],
            }
        ),
    )

    persisted_node = next(
        node for node in workflow.graph["nodes"] if node["id"] == "target"
    )
    assert persisted_node["data"][parameter_key] == expected_value
    assert acknowledged.parameter_group is not None
    assert acknowledged.parameter_group.tasks[0].status == "completed"


def test_parameter_ack_reconciles_github_comment_task_from_canonical_graph(
    db_session,
):
    user, workflow, request_row = _fixture(db_session)
    session = db_session.get(AgentBuilderSession, request_row.session_id)
    session.protocol_version = "direct_edit_v1"
    github_token_reference = WorkflowNodeSecretService.create_reference(
        db_session,
        encryption=_workflow_node_secret_encryption(),
        workflow_id=workflow.id,
        organization_id=workflow.organization_id,
        user_id=user.id,
        node_id="github",
        node_type="githubNode",
        parameter_key="api_token",
        secret_value="synthetic-github-token",
    )
    workflow.graph = {
        "nodes": [
            {
                "id": "github",
                "type": "githubNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "GitHub PR",
                    "action": "get_pr",
                    "api_token": github_token_reference,
                    "repo_owner": "octo",
                    "repo_name": "repo",
                    "pr_number": "15",
                    "configuration_state": "resolved",
                },
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    group_id = uuid.uuid4()
    action_task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_github",
        node_id="github",
        node_type="githubNode",
        parameter_key="action",
        label="GitHub 작업",
        input_type="select",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="Select the GitHub action.",
        input_guidance="Select read or comment.",
        validation={"options": ["get_pr", "comment_pr"]},
    )
    comment_task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_github",
        node_id="github",
        node_type="githubNode",
        parameter_key="comment_body",
        label="댓글 내용",
        input_type="textarea",
        required=False,
        status="skipped",
        task_version=1,
        stable_order=1,
        reason="Enter the pull request comment.",
        input_guidance="Enter a non-empty comment.",
        validation={
            "visible_when": {
                "parameter_key": "action",
                "equals": "comment_pr",
            },
            "required_when": {
                "parameter_key": "action",
                "equals": "comment_pr",
            },
        },
    )
    AgentBuilderRepository().store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="active",
            tasks=[action_task, comment_task],
        ),
    )
    db_session.flush()

    operation_id = uuid.uuid4()
    issued = ParameterTaskService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    ).decide(
        session.id,
        action_task.task_id,
        AgentBuilderParameterTaskDecisionRequest.model_validate(
            {
                "operation_id": operation_id,
                "expected_task_version": 1,
                "action": "set",
                "value": {"kind": "select", "value": "comment_pr"},
            }
        ),
    )
    assert issued.graph_mutation is not None
    result_graph = apply_graph_operations(
        workflow.graph,
        issued.graph_mutation.operations,
    )
    saved = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        _draft_request(
            {
                **result_graph,
                "mutation_context": {
                    "operation_id": operation_id,
                    "action": "apply",
                    "expected_base_graph_hash": (
                        issued.graph_mutation.base_graph_hash
                    ),
                    "expected_workflow_updated_at": (
                        issued.graph_mutation.expected_workflow_updated_at
                    ),
                    "catalog_version": 3,
                },
            }
        ),
        user_id=str(user.id),
    )

    acknowledged = GraphMutationLifecycleService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    ).acknowledge(
        session.id,
        operation_id,
        GraphMutationAcknowledgementRequest.model_validate(
            {
                "workflow_id": workflow.id,
                "graph_hash": saved["graph_hash"],
                "updated_at": saved["updated_at"],
            }
        ),
    )

    assert acknowledged.parameter_group is not None
    acknowledged_comment = next(
        task
        for task in acknowledged.parameter_group.tasks
        if task.parameter_key == "comment_body"
    )
    assert acknowledged_comment.required is True
    assert acknowledged_comment.status == "active"
    assert acknowledged.next_task_id == acknowledged_comment.task_id


def test_knowledge_binding_acknowledgement_completes_binding_without_skipping_catalog_tasks(
    db_session,
):
    user, workflow, request_row = _fixture(db_session)
    knowledge_bases = [
        KnowledgeBase(
            organization_id=workflow.organization_id,
            user_id=user.id,
            name="Knowledge Alpha",
        ),
        KnowledgeBase(
            organization_id=workflow.organization_id,
            user_id=user.id,
            name="Knowledge Beta",
        ),
    ]
    db_session.add_all(knowledge_bases)
    db_session.flush()
    knowledge_references = [
        {"id": str(knowledge_base.id), "name": knowledge_base.name}
        for knowledge_base in knowledge_bases
    ]
    workflow.graph = {
        "nodes": [
            {
                "id": "llm",
                "type": "llmNode",
                "position": {"x": 0, "y": 0},
                "data": {"title": "LLM", "knowledgeBases": []},
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    group_id = uuid.uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_llm",
        node_id="llm",
        node_type="llmNode",
        parameter_key="knowledgeBases",
        label="Knowledge Base",
        input_type="knowledge_multi_select",
        required=False,
        status="active",
        task_version=1,
        stable_order=0,
        reason="Knowledge Base binding is optional.",
        input_guidance="Select zero or more Knowledge Bases.",
    )
    repository = AgentBuilderRepository()
    repository.store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="active",
            tasks=[task],
        ),
    )
    db_session.flush()
    db_session.refresh(workflow)
    resolution_id = "resolution-knowledge-binding"
    operation_id = uuid.uuid4()
    mutation = GraphMutationBuilder().build(
        operation_id=operation_id,
        kind="knowledge_binding",
        generation_mode="configure_and_generate",
        workflow_id=workflow.id,
        base_graph=workflow.graph,
        expected_workflow_updated_at=workflow.updated_at,
        operations=[
            {
                "op": "replace_node_data",
                "node_id": "llm",
                "data": {
                    "title": "LLM",
                    "knowledgeBases": knowledge_references,
                },
            }
        ],
        completion_context=GraphMutationCompletionContext(
            knowledge_resolution_id=resolution_id
        ),
    )
    repository.store_envelope(
        request_row,
        GraphMutationSafeEnvelope.from_mutation(mutation),
    )
    repository.store_knowledge_resolution(
        request_row,
        resolution_id=resolution_id,
        operation_id=operation_id,
        timing="after_graph",
        selected_candidate_ids=[reference["id"] for reference in knowledge_references],
    )
    db_session.flush()
    result_graph = apply_graph_operations(workflow.graph, mutation.operations)
    saved = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        _draft_request(
            {
                **result_graph,
                "mutation_context": {
                    "operation_id": operation_id,
                    "action": "apply",
                    "expected_base_graph_hash": mutation.base_graph_hash,
                    "expected_workflow_updated_at": (
                        mutation.expected_workflow_updated_at
                    ),
                    "catalog_version": 3,
                },
            }
        ),
        user_id=str(user.id),
    )
    acknowledgement = GraphMutationAcknowledgementRequest.model_validate(
        {
            "workflow_id": workflow.id,
            "graph_hash": saved["graph_hash"],
            "updated_at": saved["updated_at"],
        }
    )
    lifecycle = GraphMutationLifecycleService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    )

    first = lifecycle.acknowledge(
        request_row.session_id, operation_id, acknowledgement
    )
    second = lifecycle.acknowledge(
        request_row.session_id, operation_id, acknowledgement
    )

    assert first.parameter_group is not None
    assert first.parameter_group.status == "active"
    knowledge_task = next(
        task
        for task in first.parameter_group.tasks
        if task.parameter_key == "knowledgeBases"
    )
    assert knowledge_task.status == "completed"
    assert knowledge_task.task_version == 2
    assert first.completed_knowledge_resolution_id == resolution_id
    assert first.next_task_id is not None
    assert any(
        task.task_id == first.next_task_id and task.status == "active"
        for task in first.parameter_group.tasks
    )
    assert second == first
    boundary = repository.load_history_boundary(request_row)
    if boundary is not None:
        assert boundary["status"] != "reverted"


def test_whole_operation_revert_uses_latest_parameter_final_and_cancels_tasks(
    db_session,
):
    user, workflow, request_row = _fixture(db_session)
    session = db_session.get(AgentBuilderSession, request_row.session_id)
    session.protocol_version = "direct_edit_v1"
    repository = AgentBuilderRepository()
    group_id = uuid.uuid4()
    task = AgentBuilderParameterTask(
        task_id=uuid.uuid4(),
        group_id=group_id,
        step_id="step_schedule",
        node_id="schedule",
        node_type="scheduleTrigger",
        parameter_key="cron_expression",
        label="Schedule",
        input_type="text",
        required=True,
        status="active",
        task_version=1,
        stable_order=0,
        reason="A schedule is required.",
        input_guidance="Enter a cron expression.",
    )
    root = GraphMutationBuilder().build(
        operation_id=uuid.uuid4(),
        kind="initial_graph",
        generation_mode="configure_and_generate",
        workflow_id=workflow.id,
        base_graph=workflow.graph,
        expected_workflow_updated_at=workflow.updated_at,
        operations=[
            {
                "op": "add_node",
                "node": _node("schedule", "scheduleTrigger")
                | {"data": {"title": "Schedule", "timezone": "UTC"}},
            },
            {"op": "add_node", "node": _node("answer", "answerNode")},
            {
                "op": "add_edge",
                "edge": {
                    "id": "schedule-answer",
                    "source": "schedule",
                    "target": "answer",
                },
            },
        ],
    )
    repository.store_envelope(
        request_row,
        GraphMutationSafeEnvelope.from_mutation(root),
    )
    repository.store_parameter_group(
        request_row,
        AgentBuilderParameterGroup(
            group_id=group_id,
            status="pending_save",
            tasks=[task],
        ),
    )
    db_session.flush()
    root_graph = apply_graph_operations(workflow.graph, root.operations)
    root_saved = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        _draft_request(
            {
                **root_graph,
                "mutation_context": {
                    "operation_id": root.operation_id,
                    "action": "apply",
                    "expected_base_graph_hash": root.base_graph_hash,
                    "expected_workflow_updated_at": root.expected_workflow_updated_at,
                    "catalog_version": 3,
                },
            }
        ),
        user_id=str(user.id),
    )
    lifecycle = GraphMutationLifecycleService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    )
    lifecycle.acknowledge(
        session.id,
        root.operation_id,
        GraphMutationAcknowledgementRequest.model_validate(
            {
                "workflow_id": workflow.id,
                "graph_hash": root_saved["graph_hash"],
                "updated_at": root_saved["updated_at"],
            }
        ),
    )
    parameter_operation_id = uuid.uuid4()
    issued = ParameterTaskService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    ).decide(
        session.id,
        task.task_id,
        AgentBuilderParameterTaskDecisionRequest.model_validate(
            {
                "operation_id": parameter_operation_id,
                "expected_task_version": 1,
                "action": "set",
                "value": {"kind": "text", "value": "0 9 * * *"},
            }
        ),
    )
    assert issued.graph_mutation is not None
    parameter_graph = apply_graph_operations(
        workflow.graph,
        issued.graph_mutation.operations,
    )
    parameter_saved = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        _draft_request(
            {
                **parameter_graph,
                "mutation_context": {
                    "operation_id": parameter_operation_id,
                    "action": "apply",
                    "expected_base_graph_hash": issued.graph_mutation.base_graph_hash,
                    "expected_workflow_updated_at": (
                        issued.graph_mutation.expected_workflow_updated_at
                    ),
                    "catalog_version": 3,
                },
            }
        ),
        user_id=str(user.id),
    )
    completed = lifecycle.acknowledge(
        session.id,
        parameter_operation_id,
        GraphMutationAcknowledgementRequest.model_validate(
            {
                "workflow_id": workflow.id,
                "graph_hash": parameter_saved["graph_hash"],
                "updated_at": parameter_saved["updated_at"],
            }
        ),
    )

    repository.store_knowledge_resolution(
        request_row,
        resolution_id="history-boundary-knowledge",
        operation_id=parameter_operation_id,
        timing="after_graph",
        selected_candidate_ids=["kb-safe-id"],
        status="completed",
    )

    reopened = lifecycle.reopen_last_parameter_task(session.id)
    revert_request = _draft_request(
        {
            "nodes": [],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "mutation_context": {
                "operation_id": root.operation_id,
                "action": "revert",
                "expected_base_graph_hash": parameter_saved["graph_hash"],
                "expected_workflow_updated_at": parameter_saved["updated_at"],
                "catalog_version": 3,
            },
        }
    )
    reverted = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        revert_request,
        user_id=str(user.id),
    )

    assert completed.parameter_group is not None
    assert completed.parameter_group.status == "completed"
    assert reopened is not None
    assert reopened.status == "completed"
    assert reverted["graph_hash"] == root.base_graph_hash
    assert reverted["parameter_group"]["status"] == "canceled"
    assert reverted["parameter_group"]["tasks"][0]["status"] == "canceled"
    assert repository.find_envelope(request_row, root.operation_id)["status"] == (
        "reverted"
    )
    boundary = repository.load_history_boundary(request_row)
    assert boundary["operation_id"] == str(root.operation_id)
    assert boundary["latest_final_graph"]["graph_hash"] == parameter_saved["graph_hash"]
    assert boundary["latest_final_graph"]["workflow_updated_at"] == parameter_saved[
        "updated_at"
    ]
    assert boundary["status"] == "reverted"
    assert request_row.response_payload["knowledge_resolutions"][0]["status"] == (
        "canceled"
    )

    revert_audit_count = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.action
            == AuditAction.AGENT_BUILDER_GRAPH_MUTATION_REVERTED,
            AuditLog.target_id == str(workflow.id),
        )
        .count()
    )
    reverted_updated_at = reverted["updated_at"]
    retried_revert = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        revert_request,
        user_id=str(user.id),
    )

    assert retried_revert["graph_hash"] == reverted["graph_hash"]
    assert retried_revert["updated_at"] == reverted_updated_at
    assert retried_revert["parameter_group"]["status"] == "canceled"
    assert (
        db_session.query(AuditLog)
        .filter(
            AuditLog.action
            == AuditAction.AGENT_BUILDER_GRAPH_MUTATION_REVERTED,
            AuditLog.target_id == str(workflow.id),
        )
        .count()
        == revert_audit_count
    )

    redo_request = _draft_request(
        {
            **parameter_graph,
            "mutation_context": {
                "operation_id": root.operation_id,
                "action": "redo",
                "expected_base_graph_hash": reverted["graph_hash"],
                "expected_workflow_updated_at": reverted["updated_at"],
                "catalog_version": 3,
            },
        }
    )
    redone = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        redo_request,
        user_id=str(user.id),
    )

    assert redone["operation_id"] == str(root.operation_id)
    assert redone["graph_hash"] == parameter_saved["graph_hash"]
    assert repository.load_latest_parameter_group(request_row).status == "canceled"
    assert request_row.response_payload["knowledge_resolutions"][0]["status"] == (
        "canceled"
    )
    boundary = repository.load_history_boundary(request_row)
    assert boundary["operation_id"] == str(root.operation_id)
    assert boundary["status"] == "reverted"
    assert boundary["latest_final_graph"]["graph_hash"] == redone["graph_hash"]
    assert boundary["latest_final_graph"]["workflow_updated_at"] == redone[
        "updated_at"
    ]
    redo_audit_count = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.action == AuditAction.AGENT_BUILDER_APPLY_SAVE_SUCCEEDED,
            AuditLog.target_id == str(workflow.id),
        )
        .count()
    )

    retried_redo = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        redo_request,
        user_id=str(user.id),
    )

    assert retried_redo["graph_hash"] == redone["graph_hash"]
    assert retried_redo["updated_at"] == redone["updated_at"]
    assert repository.load_latest_parameter_group(request_row).status == "canceled"
    assert request_row.response_payload["knowledge_resolutions"][0]["status"] == (
        "canceled"
    )
    assert (
        db_session.query(AuditLog)
        .filter(
            AuditLog.action == AuditAction.AGENT_BUILDER_APPLY_SAVE_SUCCEEDED,
            AuditLog.target_id == str(workflow.id),
        )
        .count()
        == redo_audit_count
    )

    conflicting_redo = redo_request.model_copy(
        update={
            "mutation_context": redo_request.mutation_context.model_copy(
                update={
                    "expected_workflow_updated_at": datetime.fromisoformat(
                        redone["updated_at"]
                    )
                }
            )
        }
    )
    with pytest.raises(HTTPException) as exc:
        WorkflowService.save_draft(
            db_session,
            str(workflow.id),
            conflicting_redo,
            user_id=str(user.id),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail in {"stale_graph", "stale_workflow_updated_at"}

    different_candidate_redo = redo_request.model_copy(
        update={"nodes": [], "edges": []}
    )
    with pytest.raises(HTTPException) as exc:
        WorkflowService.save_draft(
            db_session,
            str(workflow.id),
            different_candidate_redo,
            user_id=str(user.id),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail in {"stale_graph", "redo_graph_hash_mismatch"}

    post_redo_revert_request = _draft_request(
        {
            "nodes": [],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "mutation_context": {
                "operation_id": root.operation_id,
                "action": "revert",
                "expected_base_graph_hash": redone["graph_hash"],
                "expected_workflow_updated_at": redone["updated_at"],
                "catalog_version": 3,
            },
        }
    )
    post_redo_revert = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        post_redo_revert_request,
        user_id=str(user.id),
    )
    post_redo_revert_audit_count = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.action
            == AuditAction.AGENT_BUILDER_GRAPH_MUTATION_REVERTED,
            AuditLog.target_id == str(workflow.id),
        )
        .count()
    )
    assert post_redo_revert_audit_count == revert_audit_count + 1
    canceled_group = repository.load_latest_parameter_group(request_row)
    canceled_resolution = request_row.response_payload["knowledge_resolutions"][0]

    assert post_redo_revert["graph_hash"] == root.base_graph_hash
    assert canceled_group.status == "canceled"
    assert canceled_group.tasks[0].status == "canceled"
    assert canceled_resolution["status"] == "canceled"

    retried_post_redo_revert = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        post_redo_revert_request,
        user_id=str(user.id),
    )

    assert retried_post_redo_revert["graph_hash"] == post_redo_revert["graph_hash"]
    assert retried_post_redo_revert["updated_at"] == post_redo_revert["updated_at"]
    retried_group = repository.load_latest_parameter_group(request_row)
    assert retried_group == canceled_group
    assert request_row.response_payload["knowledge_resolutions"][0] == (
        canceled_resolution
    )
    assert (
        db_session.query(AuditLog)
        .filter(
            AuditLog.action
            == AuditAction.AGENT_BUILDER_GRAPH_MUTATION_REVERTED,
            AuditLog.target_id == str(workflow.id),
        )
        .count()
        == post_redo_revert_audit_count
    )


def test_replace_workflow_revert_restores_entire_previous_graph(db_session):
    user, workflow, request_row = _fixture(db_session)
    previous_graph = materialize_candidate_graph(
        {
            "nodes": [
                _node("legacy-start", "startNode")
                | {"data": {"title": "Legacy start", "triggerType": "manual"}},
                _node("legacy-template", "templateNode")
                | {
                    "position": {"x": 200, "y": 0},
                    "data": {"title": "Legacy template", "template": "{{input}}"},
                },
                _node("legacy-answer", "answerNode")
                | {
                    "position": {"x": 400, "y": 0},
                    "data": {"title": "Legacy answer"},
                },
            ],
            "edges": [
                {
                    "id": "legacy-edge-1",
                    "source": "legacy-start",
                    "target": "legacy-template",
                },
                {
                    "id": "legacy-edge-2",
                    "source": "legacy-template",
                    "target": "legacy-answer",
                },
            ],
            "viewport": {"x": 12, "y": 24, "zoom": 0.8},
        }
    )
    workflow.graph = previous_graph
    db_session.flush()
    db_session.refresh(workflow)
    mutation = GraphMutationBuilder().build(
        operation_id=uuid.uuid4(),
        kind="replace_workflow",
        generation_mode="structure_only",
        workflow_id=workflow.id,
        base_graph=workflow.graph,
        expected_workflow_updated_at=workflow.updated_at,
        operations=[
            {"op": "remove_edge", "edge_id": "legacy-edge-1"},
            {"op": "remove_edge", "edge_id": "legacy-edge-2"},
            {"op": "remove_node", "node_id": "legacy-start"},
            {"op": "remove_node", "node_id": "legacy-template"},
            {"op": "remove_node", "node_id": "legacy-answer"},
            {"op": "add_node", "node": _node("new-start", "startNode")},
            {"op": "add_node", "node": _node("new-answer", "answerNode")},
            {
                "op": "add_edge",
                "edge": {
                    "id": "new-edge",
                    "source": "new-start",
                    "target": "new-answer",
                },
            },
        ],
    )
    repository = AgentBuilderRepository()
    repository.store_envelope(
        request_row,
        GraphMutationSafeEnvelope.from_mutation(mutation),
    )
    replacement_graph = apply_graph_operations(workflow.graph, mutation.operations)
    saved = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        _draft_request(
            {
                **replacement_graph,
                "mutation_context": {
                    "operation_id": mutation.operation_id,
                    "action": "apply",
                    "expected_base_graph_hash": mutation.base_graph_hash,
                    "expected_workflow_updated_at": (
                        mutation.expected_workflow_updated_at
                    ),
                    "catalog_version": 3,
                },
            }
        ),
        user_id=str(user.id),
    )
    GraphMutationLifecycleService(
        db_session,
        user_id=user.id,
        organization_id=workflow.organization_id,
    ).acknowledge(
        request_row.session_id,
        mutation.operation_id,
        GraphMutationAcknowledgementRequest.model_validate(
            {
                "workflow_id": workflow.id,
                "graph_hash": saved["graph_hash"],
                "updated_at": saved["updated_at"],
            }
        ),
    )

    reverted = WorkflowService.save_draft(
        db_session,
        str(workflow.id),
        _draft_request(
            {
                **previous_graph,
                "mutation_context": {
                    "operation_id": mutation.operation_id,
                    "action": "revert",
                    "expected_base_graph_hash": saved["graph_hash"],
                    "expected_workflow_updated_at": saved["updated_at"],
                    "catalog_version": 3,
                },
            }
        ),
        user_id=str(user.id),
    )

    assert reverted["graph_hash"] == mutation.base_graph_hash
    assert workflow.graph == previous_graph
    assert {node["id"] for node in workflow.graph["nodes"]} == {
        "legacy-start",
        "legacy-template",
        "legacy-answer",
    }
    assert {edge["id"] for edge in workflow.graph["edges"]} == {
        "legacy-edge-1",
        "legacy-edge-2",
    }
