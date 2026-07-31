import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from apps.gateway.services.agent_builder_service import (
    EXPECTED_APP_PRIMARY_WORKFLOW_ID,
    AgentBuilderService,
    calculate_graph_hash,
)
from apps.gateway.services.app_lifecycle_lock import (
    AppPrimaryChangedDuringMutationError,
    lock_app_for_lifecycle,
    lock_app_for_workflow_mutation,
)
from apps.gateway.services.deployment_service import DeploymentService
from apps.gateway.services.organization_member_service import OrganizationMemberService
from apps.gateway.services.workflow_budget_service import WorkflowBudgetService
from apps.gateway.services.workflow_budget_lock import lock_workflow_budget_scope
from apps.gateway.services.workflow_permission_lock import (
    lock_workflow_permission_scope,
)
from apps.shared.db.models.agent_builder import (
    AgentBuilderDraft,
    AgentBuilderRequest,
    AgentBuilderSession,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_MEMBERSHIP_REMOVED,
    OrganizationMembership,
)
from apps.shared.db.models.team import (
    Team,
    TeamWorkflowPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_budget import WorkflowBudget
from apps.shared.db.models.workflow_deployment import (
    DeploymentType,
    WorkflowDeployment,
)
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    app_auth_secret_verifier,
)
from apps.shared.domain.deployment_runtime_policy import (
    DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
)
from apps.shared.schemas.agent_builder import AgentBuilderApplyRequest
from apps.shared.schemas.deployment import DeploymentCreate
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from apps.workflow_engine.tasks import (
    PermanentDeploymentExecutionError,
    _canonical_workflow_execution_context,
)


ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mba254_primary"


def _run_alembic(database: str, config: DisposablePostgresConfig) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "apps/shared/alembic.ini",
            "upgrade",
            "heads",
        ],
        cwd=ROOT_DIR,
        env=config.subprocess_environment(database=database, root_dir=ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise pytest.fail.Exception(
            "disposable PostgreSQL migration failed; output omitted",
            pytrace=False,
        )


@pytest.fixture(scope="module")
def db_session():
    if os.getenv(RUN_ENV) != "1":
        pytest.skip(f"set {RUN_ENV}=1 to run disposable PostgreSQL integration")
    try:
        config = DisposablePostgresConfig.from_environment()
    except DisposablePostgresConfigurationError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL connection settings are not safely configured",
            pytrace=False,
        ) from None

    database = f"{DB_PREFIX}_{uuid.uuid4().hex[:12]}"
    quoted_database = quote_disposable_database_name(database, prefix=DB_PREFIX)
    admin_engine = create_engine(
        config.database_url(config.maintenance_database),
        isolation_level="AUTOCOMMIT",
    )
    database_created = False
    engine = None
    db = None
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {quoted_database}"))
        database_created = True
        extension_engine = create_engine(config.database_url(database))
        try:
            with extension_engine.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        finally:
            extension_engine.dispose()
        _run_alembic(database, config)
        engine = create_engine(config.database_url(database), pool_pre_ping=True)
        db = sessionmaker(bind=engine, expire_on_commit=False)()
        yield db
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable or rejected the connection; "
            "connection details omitted",
            pytrace=False,
        ) from None
    finally:
        if db is not None:
            db.close()
        if engine is not None:
            engine.dispose()
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
                    connection.execute(
                        text(f"DROP DATABASE IF EXISTS {quoted_database}")
                    )
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup could not connect; "
                    "connection details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()


def _user(prefix: str) -> User:
    suffix = uuid.uuid4().hex
    return User(
        email=f"{prefix}-{suffix}@example.invalid",
        name=f"Agent Builder Primary {prefix}",
        social_provider="local",
    )


def _ready_new_workflow_draft(
    db: Session,
    *,
    actor: User,
    organization: Organization,
    app: App,
    expected_primary_workflow_id: uuid.UUID,
) -> AgentBuilderDraft:
    graph = {
        "nodes": [
            {"id": "start", "type": "startNode", "data": {}},
            {"id": "answer", "type": "answerNode", "data": {}},
        ],
        "edges": [{"id": "start-answer", "source": "start", "target": "answer"}],
    }
    session = AgentBuilderSession(
        organization_id=organization.id,
        user_id=actor.id,
        workflow_id=expected_primary_workflow_id,
        app_id=app.id,
        status="active",
    )
    db.add(session)
    db.flush()
    request = AgentBuilderRequest(
        session_id=session.id,
        organization_id=organization.id,
        user_id=actor.id,
        status="done",
        message_summary="safe test summary",
        structured_request={},
        response_payload={},
    )
    db.add(request)
    db.flush()
    draft = AgentBuilderDraft(
        request_id=request.id,
        session_id=session.id,
        organization_id=organization.id,
        user_id=actor.id,
        draft_mode="new_workflow",
        workflow_id=None,
        app_id=app.id,
        preview_graph=graph,
        node_detail_previews=[],
        validation_result={"valid": True, "issues": []},
        draft_metadata={
            EXPECTED_APP_PRIMARY_WORKFLOW_ID: str(expected_primary_workflow_id),
            "generated_node_ids": ["start", "answer"],
        },
        base_graph_hash=calculate_graph_hash(graph),
        status="ready",
    )
    db.add(draft)
    db.flush()
    return draft


def _app_context(db: Session):
    actor = _user("actor")
    collaborator = _user("collaborator")
    db.add_all([actor, collaborator])
    db.flush()
    organization = Organization(
        name=f"Agent Builder Primary {uuid.uuid4().hex}",
        created_by=actor.id,
        managed_by=actor.id,
    )
    db.add(organization)
    db.flush()
    db.add_all(
        [
            OrganizationMembership(
                organization_id=organization.id,
                user_id=actor.id,
                membership_state="active",
                organization_auth_state="manager",
                invited_by=actor.id,
            ),
            OrganizationMembership(
                organization_id=organization.id,
                user_id=collaborator.id,
                membership_state="active",
                organization_auth_state="member",
                invited_by=actor.id,
            ),
        ]
    )
    app = App(
        organization_id=organization.id,
        name="Agent Builder Primary Test",
        url_slug=f"agent-builder-primary-{uuid.uuid4().hex}",
        auth_secret=None,
        created_by=actor.id,
    )
    db.add(app)
    db.flush()
    workflow = Workflow(
        organization_id=organization.id,
        app_id=app.id,
        created_by=actor.id,
        graph={"nodes": [], "edges": []},
    )
    db.add(workflow)
    db.flush()
    app.workflow_id = workflow.id
    db.add_all(
        [
            UserWorkflowPermission(
                grantee_organization_id=organization.id,
                workflow_id=workflow.id,
                user_id=actor.id,
                auth_state="builder",
                assigned_by=actor.id,
                options={"source": "actor"},
                flags=1,
            ),
            UserWorkflowPermission(
                grantee_organization_id=organization.id,
                workflow_id=workflow.id,
                user_id=collaborator.id,
                auth_state="viewer",
                assigned_by=actor.id,
                options={"source": "collaborator"},
                flags=2,
            ),
        ]
    )
    team = Team(
        organization_id=organization.id,
        name=f"Primary Team {uuid.uuid4().hex}",
        created_by=actor.id,
    )
    db.add(team)
    db.flush()
    db.add(
        TeamWorkflowPermission(
            grantee_organization_id=organization.id,
            workflow_id=workflow.id,
            team_id=team.id,
            auth_state="operator",
            assigned_by=actor.id,
            options={"source": "team"},
            flags=4,
        )
    )
    db.flush()
    return actor, collaborator, organization, app, workflow, team


def test_new_workflow_apply_atomically_promotes_primary_and_inherits_permissions(
    db_session,
):
    actor, collaborator, organization, app, old_workflow, team = _app_context(
        db_session
    )
    draft = _ready_new_workflow_draft(
        db_session,
        actor=actor,
        organization=organization,
        app=app,
        expected_primary_workflow_id=old_workflow.id,
    )
    service = AgentBuilderService(
        db_session,
        user=actor,
        organization_id=organization.id,
    )

    response = service.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(draft.preview_graph),
        ),
    )

    assert response.outcome == "saved", (
        response.block_reason,
        response.stale_state,
        response.notices,
    )
    db_session.refresh(app)
    assert app.workflow_id == response.saved_workflow_id
    inherited_users = (
        db_session.query(UserWorkflowPermission)
        .filter(UserWorkflowPermission.workflow_id == response.saved_workflow_id)
        .all()
    )
    assert {row.user_id: row.auth_state for row in inherited_users} == {
        actor.id: "manager",
        collaborator.id: "viewer",
    }
    inherited_team = (
        db_session.query(TeamWorkflowPermission)
        .filter(TeamWorkflowPermission.workflow_id == response.saved_workflow_id)
        .one()
    )
    assert inherited_team.team_id == team.id
    assert inherited_team.auth_state == "operator"
    assert inherited_team.options == {"source": "team"}
    assert draft.request.session.workflow_id == response.saved_workflow_id

    execution_context = _canonical_workflow_execution_context(
        db_session,
        {
            "workflow_id": str(response.saved_workflow_id),
            "execution_id": str(uuid.uuid4()),
        },
    )
    assert execution_context["workflow_id"] == str(response.saved_workflow_id)
    assert execution_context["app_id"] == str(app.id)

    with pytest.raises(
        PermanentDeploymentExecutionError,
        match="workflow execution identity is invalid",
    ):
        _canonical_workflow_execution_context(
            db_session,
            {
                "workflow_id": str(old_workflow.id),
                "execution_id": str(uuid.uuid4()),
            },
        )


def test_new_workflow_apply_blocks_before_insert_when_active_deployment_exists(
    db_session,
):
    actor, _collaborator, organization, app, old_workflow, _team = _app_context(
        db_session
    )
    deployment = WorkflowDeployment(
        app_id=app.id,
        version=1,
        type=DeploymentType.CHATBOT,
        graph_snapshot=old_workflow.graph,
        created_by=actor.id,
        is_active=True,
    )
    db_session.add(deployment)
    db_session.flush()
    app.active_deployment_id = deployment.id
    draft = _ready_new_workflow_draft(
        db_session,
        actor=actor,
        organization=organization,
        app=app,
        expected_primary_workflow_id=old_workflow.id,
    )
    workflow_count_before = db_session.query(Workflow).count()
    service = AgentBuilderService(
        db_session,
        user=actor,
        organization_id=organization.id,
    )

    response = service.apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(draft.preview_graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "APP_ACTIVE_DEPLOYMENT_CONFLICT"
    assert db_session.query(Workflow).count() == workflow_count_before
    db_session.refresh(app)
    assert app.workflow_id == old_workflow.id
    assert app.active_deployment_id == deployment.id


def test_new_workflow_apply_blocks_when_primary_has_active_budget(db_session):
    actor, _collaborator, organization, app, old_workflow, _team = _app_context(
        db_session
    )
    db_session.add(
        WorkflowBudget(
            organization_id=organization.id,
            workflow_id=old_workflow.id,
            monthly_budget_usd=Decimal("100.00"),
            is_enabled=True,
            created_by=actor.id,
        )
    )
    draft = _ready_new_workflow_draft(
        db_session,
        actor=actor,
        organization=organization,
        app=app,
        expected_primary_workflow_id=old_workflow.id,
    )
    workflow_count_before = db_session.query(Workflow).count()

    response = AgentBuilderService(
        db_session,
        user=actor,
        organization_id=organization.id,
    ).apply_draft(
        draft.id,
        AgentBuilderApplyRequest(
            action="apply_and_save",
            client_preview_graph_hash=calculate_graph_hash(draft.preview_graph),
        ),
    )

    assert response.outcome == "blocked"
    assert response.block_reason == "APP_WORKFLOW_BUDGET_CONFLICT"
    assert db_session.query(Workflow).count() == workflow_count_before
    db_session.refresh(app)
    assert app.workflow_id == old_workflow.id


def test_app_lifecycle_lock_refreshes_stale_identity_map(db_session):
    actor, _collaborator, organization, app, old_workflow, _team = _app_context(
        db_session
    )
    actor_id = actor.id
    organization_id = organization.id
    app_id = app.id
    db_session.commit()

    session_factory = sessionmaker(
        bind=db_session.get_bind(),
        expire_on_commit=False,
    )
    stale_db = session_factory()
    try:
        stale_app = stale_db.get(App, app_id)
        assert stale_app.workflow_id == old_workflow.id

        with session_factory() as update_db:
            replacement = Workflow(
                organization_id=organization_id,
                app_id=app_id,
                created_by=actor_id,
                graph={"nodes": [], "edges": []},
            )
            update_db.add(replacement)
            update_db.flush()
            update_db.get(App, app_id).workflow_id = replacement.id
            update_db.commit()
            replacement_id = replacement.id

        locked_app = lock_app_for_lifecycle(
            stale_db,
            app_id,
            organization_id=organization_id,
        )

        assert locked_app is stale_app
        assert locked_app.workflow_id == replacement_id
    finally:
        stale_db.rollback()
        stale_db.close()


def test_budget_upsert_rejects_primary_changed_after_scope_observation(
    db_session,
    monkeypatch,
):
    actor, _collaborator, organization, app, old_workflow, _team = _app_context(
        db_session
    )
    draft = _ready_new_workflow_draft(
        db_session,
        actor=actor,
        organization=organization,
        app=app,
        expected_primary_workflow_id=old_workflow.id,
    )
    actor_id = actor.id
    organization_id = organization.id
    app_id = app.id
    old_workflow_id = old_workflow.id
    draft_id = draft.id
    db_session.commit()

    session_factory = sessionmaker(
        bind=db_session.get_bind(),
        expire_on_commit=False,
    )
    mutation_observed_primary = threading.Event()
    allow_mutation_to_lock = threading.Event()
    mutation_finished = threading.Event()
    mutation_result = {}

    from apps.gateway.services import app_lifecycle_lock as lifecycle_lock_module

    original_lock = lifecycle_lock_module.lock_app_for_lifecycle

    def gated_mutation_lock(db, target_app_id, **kwargs):
        mutation_observed_primary.set()
        if not allow_mutation_to_lock.wait(timeout=10):
            raise RuntimeError("test synchronization timeout")
        return original_lock(db, target_app_id, **kwargs)

    monkeypatch.setattr(
        lifecycle_lock_module,
        "lock_app_for_lifecycle",
        gated_mutation_lock,
    )

    def upsert_budget():
        try:
            with session_factory() as budget_db:
                WorkflowBudgetService.upsert_budget(
                    budget_db,
                    organization_id=organization_id,
                    workflow_id=old_workflow_id,
                    actor_id=actor_id,
                    monthly_budget_usd=Decimal("100.00"),
                    is_enabled=True,
                )
        except Exception as exc:  # pragma: no cover - asserted in main thread
            mutation_result["error"] = exc
        finally:
            mutation_finished.set()

    thread = threading.Thread(target=upsert_budget, daemon=True)
    thread.start()
    try:
        assert mutation_observed_primary.wait(timeout=5)
        with session_factory() as apply_db:
            apply_draft = apply_db.get(AgentBuilderDraft, draft_id)
            apply_actor = apply_db.get(User, actor_id)
            response = AgentBuilderService(
                apply_db,
                user=apply_actor,
                organization_id=organization_id,
            ).apply_draft(
                apply_draft.id,
                AgentBuilderApplyRequest(
                    action="apply_and_save",
                    client_preview_graph_hash=calculate_graph_hash(
                        apply_draft.preview_graph
                    ),
                ),
            )
        assert response.outcome == "saved"
    finally:
        allow_mutation_to_lock.set()

    assert mutation_finished.wait(timeout=10)
    thread.join(timeout=1)
    assert isinstance(
        mutation_result.get("error"),
        AppPrimaryChangedDuringMutationError,
    )

    db_session.expire_all()
    final_app = db_session.get(App, app_id)
    assert final_app.workflow_id == response.saved_workflow_id
    assert (
        db_session.query(WorkflowBudget)
        .filter(WorkflowBudget.workflow_id == old_workflow_id)
        .first()
        is None
    )


def test_budget_commit_precedes_primary_transition_snapshot(db_session, monkeypatch):
    actor, _collaborator, organization, app, old_workflow, _team = _app_context(
        db_session
    )
    draft = _ready_new_workflow_draft(
        db_session,
        actor=actor,
        organization=organization,
        app=app,
        expected_primary_workflow_id=old_workflow.id,
    )
    actor_id = actor.id
    organization_id = organization.id
    app_id = app.id
    workflow_id = old_workflow.id
    draft_id = draft.id
    workflow_count_before = db_session.query(Workflow).count()
    db_session.commit()

    bind = db_session.get_bind()
    session_factory = sessionmaker(bind=bind, expire_on_commit=False)
    budget_lock_attempted = threading.Event()
    apply_finished = threading.Event()
    apply_result = {}

    from apps.gateway.services import workflow_budget_service as budget_module

    original_budget_lock = budget_module.lock_workflow_budget_scope

    def signaled_budget_lock(db, **kwargs):
        budget_lock_attempted.set()
        return original_budget_lock(db, **kwargs)

    monkeypatch.setattr(
        budget_module,
        "lock_workflow_budget_scope",
        signaled_budget_lock,
    )

    budget_db = session_factory()
    lock_workflow_budget_scope(
        budget_db,
        organization_id=organization_id,
        workflow_id=workflow_id,
    )
    budget_db.add(
        WorkflowBudget(
            organization_id=organization_id,
            workflow_id=workflow_id,
            monthly_budget_usd=Decimal("100.00"),
            is_enabled=True,
            created_by=actor_id,
        )
    )
    budget_db.flush()

    def apply_transition():
        try:
            with session_factory() as apply_db:
                apply_draft = apply_db.get(AgentBuilderDraft, draft_id)
                apply_actor = apply_db.get(User, actor_id)
                apply_result["response"] = AgentBuilderService(
                    apply_db,
                    user=apply_actor,
                    organization_id=organization_id,
                ).apply_draft(
                    apply_draft.id,
                    AgentBuilderApplyRequest(
                        action="apply_and_save",
                        client_preview_graph_hash=calculate_graph_hash(
                            apply_draft.preview_graph
                        ),
                    ),
                )
        except Exception as exc:  # pragma: no cover - asserted in main thread
            apply_result["error"] = exc
        finally:
            apply_finished.set()

    thread = threading.Thread(target=apply_transition, daemon=True)
    thread.start()
    try:
        assert budget_lock_attempted.wait(timeout=5)
        assert apply_finished.wait(timeout=0.2) is False
    finally:
        budget_db.commit()
        budget_db.close()

    assert apply_finished.wait(timeout=10)
    thread.join(timeout=1)
    assert "error" not in apply_result
    response = apply_result["response"]
    assert response.outcome == "blocked"
    assert response.block_reason == "APP_WORKFLOW_BUDGET_CONFLICT"

    db_session.expire_all()
    final_app = db_session.get(App, app_id)
    assert final_app.workflow_id == workflow_id
    assert db_session.query(Workflow).count() == workflow_count_before


@pytest.mark.parametrize(
    "explicit_snapshot",
    [False, True],
    ids=["server-resolved", "client-snapshot"],
)
def test_deployment_create_waits_for_primary_transition_app_lock(
    db_session,
    monkeypatch,
    explicit_snapshot,
):
    actor, _collaborator, organization, app, old_workflow, _team = _app_context(
        db_session
    )
    app.auth_secret_verifier = app_auth_secret_verifier(
        "synthetic-agent-builder-deployment-secret"
    )
    app.auth_secret_verifier_version = APP_AUTH_SECRET_VERIFIER_VERSION
    app.auth_secret_generation = 1
    app.auth_secret_rotated_at = datetime.now(timezone.utc)
    draft = _ready_new_workflow_draft(
        db_session,
        actor=actor,
        organization=organization,
        app=app,
        expected_primary_workflow_id=old_workflow.id,
    )
    actor_id = actor.id
    organization_id = organization.id
    app_id = app.id
    draft_id = draft.id
    old_graph_snapshot = old_workflow.graph
    db_session.commit()

    bind = db_session.get_bind()
    session_factory = sessionmaker(bind=bind, expire_on_commit=False)
    apply_lock_acquired = threading.Event()
    allow_apply_to_continue = threading.Event()
    apply_finished = threading.Event()
    deployment_lock_attempted = threading.Event()
    deployment_finished = threading.Event()
    apply_result = {}
    deployment_result = {}

    from apps.gateway.services import agent_builder_service as agent_builder_module
    from apps.gateway.services import deployment_service as deployment_module

    original_apply_lock = agent_builder_module.lock_app_for_lifecycle
    original_deployment_lock = deployment_module.lock_app_for_lifecycle

    def gated_apply_lock(db, target_app_id, **kwargs):
        locked_app = original_apply_lock(db, target_app_id, **kwargs)
        apply_lock_acquired.set()
        if not allow_apply_to_continue.wait(timeout=10):
            raise RuntimeError("test synchronization timeout")
        return locked_app

    def signaled_deployment_lock(db, target_app_id, **kwargs):
        deployment_lock_attempted.set()
        return original_deployment_lock(db, target_app_id, **kwargs)

    monkeypatch.setattr(
        agent_builder_module,
        "lock_app_for_lifecycle",
        gated_apply_lock,
    )
    monkeypatch.setattr(
        deployment_module,
        "lock_app_for_lifecycle",
        signaled_deployment_lock,
    )
    from apps.gateway.services import scheduler_service as scheduler_module

    monkeypatch.setattr(
        scheduler_module,
        "get_scheduler_service",
        lambda: SimpleNamespace(
            add_schedule=lambda *args, **kwargs: None,
            remove_schedule=lambda *args, **kwargs: None,
        ),
    )

    def apply_transition():
        try:
            with session_factory() as apply_db:
                apply_draft = apply_db.get(AgentBuilderDraft, draft_id)
                apply_actor = apply_db.get(User, actor_id)
                apply_result["response"] = AgentBuilderService(
                    apply_db,
                    user=apply_actor,
                    organization_id=organization_id,
                ).apply_draft(
                    apply_draft.id,
                    AgentBuilderApplyRequest(
                        action="apply_and_save",
                        client_preview_graph_hash=calculate_graph_hash(
                            apply_draft.preview_graph
                        ),
                    ),
                )
        except Exception as exc:  # pragma: no cover - asserted in main thread
            apply_result["error"] = exc
        finally:
            apply_finished.set()

    def create_deployment():
        try:
            with session_factory() as deployment_db:
                deployment = DeploymentService.create_deployment(
                    deployment_db,
                    DeploymentCreate(
                        app_id=app_id,
                        type=DeploymentType.API,
                        graph_snapshot=(
                            old_graph_snapshot if explicit_snapshot else None
                        ),
                        is_active=True,
                    ),
                    user_id=actor_id,
                    observed_workflow_id=old_workflow.id,
                    runtime_policy=DEFAULT_DEPLOYMENT_RUNTIME_POLICY,
                )
                deployment_result["deployment_id"] = deployment.id
        except Exception as exc:  # pragma: no cover - asserted in main thread
            deployment_result["error"] = exc
        finally:
            deployment_finished.set()

    apply_thread = threading.Thread(target=apply_transition, daemon=True)
    apply_thread.start()
    assert apply_lock_acquired.wait(timeout=5)

    deployment_thread = threading.Thread(target=create_deployment, daemon=True)
    deployment_thread.start()
    try:
        assert deployment_lock_attempted.wait(timeout=5)
        assert deployment_finished.wait(timeout=0.2) is False
    finally:
        allow_apply_to_continue.set()

    assert apply_finished.wait(timeout=10)
    assert deployment_finished.wait(timeout=10)
    apply_thread.join(timeout=1)
    deployment_thread.join(timeout=1)
    assert "error" not in apply_result
    response = apply_result["response"]
    assert response.outcome == "saved", (
        response.block_reason,
        response.stale_state,
        response.notices,
    )

    db_session.expire_all()
    final_app = db_session.get(App, app_id)
    saved_workflow = db_session.get(Workflow, response.saved_workflow_id)
    assert final_app.workflow_id == response.saved_workflow_id
    if explicit_snapshot:
        error = deployment_result.get("error")
        assert isinstance(error, HTTPException)
        assert error.status_code == 409
        assert error.detail["code"] == "deployment.graph_snapshot_stale"
        assert final_app.active_deployment_id is None
    else:
        assert "error" not in deployment_result
        deployment = db_session.get(
            WorkflowDeployment,
            deployment_result["deployment_id"],
        )
        assert final_app.active_deployment_id == deployment.id
        assert deployment.graph_snapshot == saved_workflow.graph


def test_permission_revoke_commits_before_inheritance_snapshot(
    db_session,
    monkeypatch,
):
    actor, collaborator, organization, app, old_workflow, _team = _app_context(
        db_session
    )
    draft = _ready_new_workflow_draft(
        db_session,
        actor=actor,
        organization=organization,
        app=app,
        expected_primary_workflow_id=old_workflow.id,
    )
    db_session.commit()

    bind = db_session.get_bind()
    session_factory = sessionmaker(bind=bind, expire_on_commit=False)
    inheritance_lock_attempted = threading.Event()
    apply_finished = threading.Event()
    apply_result = {}

    from apps.gateway.services import app_service as app_service_module

    original_permission_lock = app_service_module.lock_workflow_permission_scope

    def signaled_permission_lock(db, **kwargs):
        inheritance_lock_attempted.set()
        return original_permission_lock(db, **kwargs)

    monkeypatch.setattr(
        app_service_module,
        "lock_workflow_permission_scope",
        signaled_permission_lock,
    )

    revoke_db = session_factory()
    lock_workflow_permission_scope(
        revoke_db,
        organization_id=organization.id,
        workflow_id=old_workflow.id,
    )
    revoked = (
        revoke_db.query(UserWorkflowPermission)
        .filter(
            UserWorkflowPermission.grantee_organization_id == organization.id,
            UserWorkflowPermission.workflow_id == old_workflow.id,
            UserWorkflowPermission.user_id == collaborator.id,
        )
        .one()
    )
    revoke_db.delete(revoked)

    def apply_transition():
        try:
            with session_factory() as apply_db:
                apply_draft = apply_db.get(AgentBuilderDraft, draft.id)
                apply_actor = apply_db.get(User, actor.id)
                apply_result["response"] = AgentBuilderService(
                    apply_db,
                    user=apply_actor,
                    organization_id=organization.id,
                ).apply_draft(
                    apply_draft.id,
                    AgentBuilderApplyRequest(
                        action="apply_and_save",
                        client_preview_graph_hash=calculate_graph_hash(
                            apply_draft.preview_graph
                        ),
                    ),
                )
        except Exception as exc:  # pragma: no cover - asserted in main thread
            apply_result["error"] = exc
        finally:
            apply_finished.set()

    thread = threading.Thread(target=apply_transition, daemon=True)
    thread.start()
    assert inheritance_lock_attempted.wait(timeout=5)
    assert apply_finished.wait(timeout=0.2) is False
    revoke_db.commit()
    revoke_db.close()

    assert apply_finished.wait(timeout=10)
    thread.join(timeout=1)
    assert "error" not in apply_result
    response = apply_result["response"]
    assert response.outcome == "saved"

    db_session.expire_all()
    inherited_collaborator = (
        db_session.query(UserWorkflowPermission)
        .filter(
            UserWorkflowPermission.workflow_id == response.saved_workflow_id,
            UserWorkflowPermission.user_id == collaborator.id,
        )
        .first()
    )
    assert inherited_collaborator is None


def test_member_removal_revokes_permission_created_by_inflight_transition(
    db_session,
    monkeypatch,
):
    actor, collaborator, organization, app, old_workflow, _team = _app_context(
        db_session
    )
    removal_manager = _user("removal-manager")
    db_session.add(removal_manager)
    db_session.flush()
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=removal_manager.id,
            membership_state="active",
            organization_auth_state="manager",
            invited_by=actor.id,
        )
    )
    actor_membership = (
        db_session.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == actor.id,
        )
        .one()
    )
    actor_membership.organization_auth_state = "member"
    actor_workflow_permission = (
        db_session.query(UserWorkflowPermission)
        .filter(
            UserWorkflowPermission.grantee_organization_id == organization.id,
            UserWorkflowPermission.workflow_id == old_workflow.id,
            UserWorkflowPermission.user_id == actor.id,
        )
        .one()
    )
    actor_workflow_permission.auth_state = "manager"
    draft = _ready_new_workflow_draft(
        db_session,
        actor=actor,
        organization=organization,
        app=app,
        expected_primary_workflow_id=old_workflow.id,
    )
    actor_id = actor.id
    collaborator_id = collaborator.id
    removal_manager_id = removal_manager.id
    organization_id = organization.id
    old_workflow_id = old_workflow.id
    draft_id = draft.id
    db_session.commit()

    session_factory = sessionmaker(
        bind=db_session.get_bind(),
        expire_on_commit=False,
    )
    inheritance_scope_acquired = threading.Event()
    allow_inheritance_to_continue = threading.Event()
    removal_scope_attempted = threading.Event()
    apply_finished = threading.Event()
    removal_finished = threading.Event()
    apply_result = {}
    removal_result = {}

    from apps.gateway.services import app_service as app_service_module
    from apps.gateway.services import (
        organization_member_service as member_service_module,
    )

    original_inheritance_lock = app_service_module.lock_workflow_permission_scope
    original_removal_lock = member_service_module.lock_workflow_permission_scope

    def gated_inheritance_lock(db, **kwargs):
        result = original_inheritance_lock(db, **kwargs)
        if kwargs["workflow_id"] == old_workflow_id:
            inheritance_scope_acquired.set()
            if not allow_inheritance_to_continue.wait(timeout=10):
                raise RuntimeError("test synchronization timeout")
        return result

    def signaled_removal_lock(db, **kwargs):
        removal_scope_attempted.set()
        return original_removal_lock(db, **kwargs)

    monkeypatch.setattr(
        app_service_module,
        "lock_workflow_permission_scope",
        gated_inheritance_lock,
    )
    monkeypatch.setattr(
        member_service_module,
        "lock_workflow_permission_scope",
        signaled_removal_lock,
    )

    def apply_transition():
        try:
            with session_factory() as apply_db:
                apply_draft = apply_db.get(AgentBuilderDraft, draft_id)
                apply_actor = apply_db.get(User, actor_id)
                apply_result["response"] = AgentBuilderService(
                    apply_db,
                    user=apply_actor,
                    organization_id=organization_id,
                ).apply_draft(
                    apply_draft.id,
                    AgentBuilderApplyRequest(
                        action="apply_and_save",
                        client_preview_graph_hash=calculate_graph_hash(
                            apply_draft.preview_graph
                        ),
                    ),
                )
        except Exception as exc:  # pragma: no cover - asserted in main thread
            apply_result["error"] = exc
        finally:
            apply_finished.set()

    def remove_member():
        try:
            with session_factory() as removal_db:
                removal_actor = removal_db.get(User, removal_manager_id)
                removal_result["response"] = OrganizationMemberService.remove_member(
                    removal_db,
                    removal_actor,
                    organization_id,
                    collaborator_id,
                )
        except Exception as exc:  # pragma: no cover - asserted in main thread
            removal_result["error"] = exc
        finally:
            removal_finished.set()

    apply_thread = threading.Thread(target=apply_transition, daemon=True)
    apply_thread.start()
    assert inheritance_scope_acquired.wait(timeout=5), apply_result

    removal_thread = threading.Thread(target=remove_member, daemon=True)
    removal_thread.start()
    try:
        assert removal_scope_attempted.wait(timeout=5)
        assert removal_finished.wait(timeout=0.2) is False
    finally:
        allow_inheritance_to_continue.set()

    assert apply_finished.wait(timeout=10)
    assert removal_finished.wait(timeout=10)
    apply_thread.join(timeout=1)
    removal_thread.join(timeout=1)
    assert "error" not in apply_result
    assert "error" not in removal_result
    assert apply_result["response"].outcome == "saved"
    assert removal_result["response"].status == "removed"

    db_session.expire_all()
    membership = (
        db_session.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == collaborator_id,
        )
        .one()
    )
    assert membership.membership_state == ORGANIZATION_MEMBERSHIP_REMOVED
    assert (
        db_session.query(UserWorkflowPermission)
        .filter(
            UserWorkflowPermission.grantee_organization_id == organization_id,
            UserWorkflowPermission.user_id == collaborator_id,
        )
        .count()
        == 0
    )


def test_permission_revoke_rejects_primary_changed_after_scope_observation(
    db_session,
    monkeypatch,
):
    actor, collaborator, organization, app, old_workflow, _team = _app_context(
        db_session
    )
    draft = _ready_new_workflow_draft(
        db_session,
        actor=actor,
        organization=organization,
        app=app,
        expected_primary_workflow_id=old_workflow.id,
    )
    actor_id = actor.id
    collaborator_id = collaborator.id
    organization_id = organization.id
    app_id = app.id
    old_workflow_id = old_workflow.id
    draft_id = draft.id
    db_session.commit()

    session_factory = sessionmaker(
        bind=db_session.get_bind(),
        expire_on_commit=False,
    )
    mutation_observed_primary = threading.Event()
    allow_mutation_to_lock = threading.Event()
    mutation_finished = threading.Event()
    mutation_result = {}

    from apps.gateway.services import app_lifecycle_lock as lifecycle_lock_module

    original_lock = lifecycle_lock_module.lock_app_for_lifecycle

    def gated_mutation_lock(db, target_app_id, **kwargs):
        mutation_observed_primary.set()
        if not allow_mutation_to_lock.wait(timeout=10):
            raise RuntimeError("test synchronization timeout")
        return original_lock(db, target_app_id, **kwargs)

    monkeypatch.setattr(
        lifecycle_lock_module,
        "lock_app_for_lifecycle",
        gated_mutation_lock,
    )

    def revoke_permission():
        try:
            with session_factory() as permission_db:
                lock_app_for_workflow_mutation(
                    permission_db,
                    app_id=app_id,
                    workflow_id=old_workflow_id,
                    organization_id=organization_id,
                )
                lock_workflow_permission_scope(
                    permission_db,
                    organization_id=organization_id,
                    workflow_id=old_workflow_id,
                )
                permission = (
                    permission_db.query(UserWorkflowPermission)
                    .filter(
                        UserWorkflowPermission.workflow_id == old_workflow_id,
                        UserWorkflowPermission.user_id == collaborator_id,
                    )
                    .one()
                )
                permission_db.delete(permission)
                permission_db.commit()
        except Exception as exc:  # pragma: no cover - asserted in main thread
            mutation_result["error"] = exc
        finally:
            mutation_finished.set()

    thread = threading.Thread(target=revoke_permission, daemon=True)
    thread.start()
    try:
        assert mutation_observed_primary.wait(timeout=5)
        with session_factory() as apply_db:
            apply_draft = apply_db.get(AgentBuilderDraft, draft_id)
            apply_actor = apply_db.get(User, actor_id)
            response = AgentBuilderService(
                apply_db,
                user=apply_actor,
                organization_id=organization_id,
            ).apply_draft(
                apply_draft.id,
                AgentBuilderApplyRequest(
                    action="apply_and_save",
                    client_preview_graph_hash=calculate_graph_hash(
                        apply_draft.preview_graph
                    ),
                ),
            )
        assert response.outcome == "saved"
    finally:
        allow_mutation_to_lock.set()

    assert mutation_finished.wait(timeout=10)
    thread.join(timeout=1)
    assert isinstance(
        mutation_result.get("error"),
        AppPrimaryChangedDuringMutationError,
    )

    db_session.expire_all()
    rows = (
        db_session.query(UserWorkflowPermission)
        .filter(
            UserWorkflowPermission.user_id == collaborator_id,
            UserWorkflowPermission.workflow_id.in_(
                [old_workflow_id, response.saved_workflow_id]
            ),
        )
        .all()
    )
    assert {row.workflow_id for row in rows} == {
        old_workflow_id,
        response.saved_workflow_id,
    }
