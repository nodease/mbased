import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import apps.gateway.utils.audit as audit_utils
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app as gateway_app
from apps.gateway.services.app_service import AppService
from apps.gateway.application.app_lifecycle.errors import AppDeleteInProgress
from apps.shared.db.models.agent_builder import AgentBuilderSession
from apps.shared.db.models.app import App
from apps.shared.db.models.cost_optimizer import (
    CostOptimizerCandidate,
    CostOptimizerExperiment,
    CostOptimizerRecommendationVerification,
)
from apps.shared.db.models.llm import (
    LLMCredential,
    LLMModel,
    LLMProvider,
    LLMUsageLog,
)
from apps.shared.db.models.mail_credential import MailCredential
from apps.shared.db.models.mail_processing import MailDraftEffect, MailMessageProcessing
from apps.shared.db.models.model_routing_policy import (
    LLMNodeModelRoutingPolicy,
    LLMNodeModelRoutingPolicyUpdate,
)
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.schedule_dispatch import ScheduleDispatchClaim
from apps.shared.db.models.team import UserWorkflowPermission
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.db.models.workflow_node_effect_attempt import WorkflowNodeEffectAttempt
from apps.shared.db.models.workflow_run import (
    NodeRunStatus,
    RunStatus,
    RunTriggerMode,
    TracePayload,
    TracePayloadAccessEvent,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.shared.db.session import get_db
from apps.shared.schemas.app import AppCreateRequest, AppIcon
from apps.shared.services.app_lifecycle_admission import (
    AppWorkflowAdmissionUnavailable,
    admit_workflow_run,
)
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)


ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mba87_app_delete"


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
def db_runtime():
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
        yield SimpleNamespace(
            engine=engine,
            session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        )
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable or rejected the connection; "
            "connection details omitted",
            pytrace=False,
        ) from None
    finally:
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
                    connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup could not connect; "
                    "connection details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()


@pytest.fixture
def db_session(db_runtime):
    db = db_runtime.session_factory()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _new_user(db_session, prefix: str) -> User:
    user = User(
        email=f"{prefix}-{uuid.uuid4().hex}@example.invalid",
        name=f"MBA-87 {prefix}",
        social_provider="local",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _new_organization(
    db_session,
    actor: User,
    *,
    auth_state: str = "manager",
) -> Organization:
    organization = Organization(
        name=f"MBA-87 Organization {uuid.uuid4().hex}",
        created_by=actor.id,
        managed_by=actor.id if auth_state == "manager" else None,
    )
    db_session.add(organization)
    db_session.flush()
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=actor.id,
            membership_state="active",
            organization_auth_state=auth_state,
            invited_by=actor.id,
        )
    )
    db_session.flush()
    return organization


def _add_membership(
    db_session,
    *,
    user: User,
    organization: Organization,
    invited_by: User,
    auth_state: str = "member",
) -> None:
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            membership_state="active",
            organization_auth_state=auth_state,
            invited_by=invited_by.id,
        )
    )
    db_session.flush()


def _new_app_context(
    db_session,
    *,
    graph: dict | None = None,
    with_creator_permission: bool = False,
):
    actor = _new_user(db_session, "actor")
    organization = _new_organization(db_session, actor)
    app = App(
        organization_id=organization.id,
        name=f"MBA-87 App {uuid.uuid4().hex}",
        icon={"type": "emoji", "content": "test", "background_color": "#000000"},
        url_slug=f"mba87-{uuid.uuid4().hex}",
        auth_secret="test-placeholder",
        created_by=actor.id,
    )
    db_session.add(app)
    db_session.flush()
    workflow = Workflow(
        organization_id=organization.id,
        app_id=app.id,
        created_by=actor.id,
        graph=graph or {"nodes": [], "edges": []},
    )
    db_session.add(workflow)
    db_session.flush()
    app.workflow_id = workflow.id
    if with_creator_permission:
        db_session.add(
            UserWorkflowPermission(
                grantee_organization_id=organization.id,
                workflow_id=workflow.id,
                user_id=actor.id,
                auth_state="manager",
                assigned_by=actor.id,
                options={},
                flags=0,
            )
        )
    db_session.commit()
    return SimpleNamespace(
        actor=actor,
        organization=organization,
        app=app,
        workflow=workflow,
    )


def _new_deployment(db_session, context) -> WorkflowDeployment:
    deployment = WorkflowDeployment(
        app_id=context.app.id,
        version=1,
        type=DeploymentType.WORKFLOW_NODE,
        graph_snapshot={"nodes": [], "edges": []},
        created_by=context.actor.id,
        is_active=True,
    )
    db_session.add(deployment)
    db_session.flush()
    context.app.active_deployment_id = deployment.id
    return deployment


def _error_code(response) -> str | None:
    body = response.json()
    return body.get("error", {}).get("code") if isinstance(body, dict) else None


def _client(db_session, current_user, monkeypatch) -> TestClient:
    previous_overrides = dict(gateway_app.dependency_overrides)
    gateway_app.dependency_overrides[get_db] = lambda: db_session
    gateway_app.dependency_overrides[get_current_user] = lambda: current_user
    monkeypatch.setattr(audit_utils, "record_audit", lambda **kwargs: None)
    client = TestClient(gateway_app, raise_server_exceptions=False)
    client._mba87_previous_overrides = previous_overrides
    return client


def _close_client(client: TestClient) -> None:
    client.close()
    gateway_app.dependency_overrides = client._mba87_previous_overrides


def test_delete_default_app_with_creator_permission_succeeds(db_session):
    actor = User(
        email=f"mba87-{uuid.uuid4().hex}@example.invalid",
        name="MBA-87 Actor",
        social_provider="local",
    )
    db_session.add(actor)
    db_session.flush()
    organization = Organization(
        name=f"MBA-87 Organization {uuid.uuid4().hex}",
        created_by=actor.id,
        managed_by=actor.id,
    )
    db_session.add(organization)
    db_session.flush()
    db_session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=actor.id,
            membership_state="active",
            organization_auth_state="manager",
            invited_by=actor.id,
        )
    )
    db_session.flush()

    app = AppService.create_app(
        db_session,
        AppCreateRequest(
            name=f"MBA-87 App {uuid.uuid4().hex}",
            icon=AppIcon(type="emoji", content="test", background_color="#000000"),
        ),
        user_id=actor.id,
        organization_id=organization.id,
    )
    app_id = app.id
    workflow_id = app.workflow_id

    assert (
        db_session.query(UserWorkflowPermission)
        .filter(UserWorkflowPermission.workflow_id == workflow_id)
        .count()
        == 1
    )

    assert AppService.delete_app(db_session, str(app_id), user_id=actor.id) is True
    assert db_session.query(App).filter(App.id == app_id).count() == 0
    assert db_session.query(Workflow).filter(Workflow.id == workflow_id).count() == 0
    assert (
        db_session.query(UserWorkflowPermission)
        .filter(UserWorkflowPermission.workflow_id == workflow_id)
        .count()
        == 0
    )


def test_migration_reaches_repository_heads_and_detaches_history_lifecycle_fks(
    db_session,
):
    config = Config(str(ROOT_DIR / "apps/shared/alembic.ini"))
    repository_heads = set(ScriptDirectory.from_config(config).get_heads())
    applied_heads = {
        row[0]
        for row in db_session.execute(text("SELECT version_num FROM alembic_version"))
    }
    assert applied_heads == repository_heads

    rows = db_session.execute(
        text(
            """
            SELECT
                tc.table_name,
                kcu.column_name,
                ccu.table_name AS foreign_table_name,
                rc.delete_rule
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.constraint_schema = kcu.constraint_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.constraint_schema = ccu.constraint_schema
            JOIN information_schema.referential_constraints AS rc
              ON tc.constraint_name = rc.constraint_name
             AND tc.constraint_schema = rc.constraint_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = current_schema()
            """
        )
    ).all()
    foreign_keys = {
        (table_name, column_name, foreign_table_name): delete_rule
        for table_name, column_name, foreign_table_name, delete_rule in rows
    }

    history_lifecycle_fks = {
        ("workflow_runs", "workflow_id", "workflows"),
        ("workflow_runs", "app_id", "apps"),
        ("workflow_runs", "deployment_id", "workflow_deployments"),
        ("llm_usage_logs", "workflow_id", "workflows"),
        ("cost_optimizer_experiments", "workflow_id", "workflows"),
        ("cost_optimizer_experiments", "app_id", "apps"),
        (
            "cost_optimizer_recommendation_verifications",
            "workflow_id",
            "workflows",
        ),
        ("mail_message_processings", "workflow_id", "workflows"),
        (
            "mail_message_processings",
            "deployment_id",
            "workflow_deployments",
        ),
        (
            "llm_node_model_routing_policy_updates",
            "policy_id",
            "llm_node_model_routing_policies",
        ),
        (
            "llm_node_model_routing_policy_run_events",
            "policy_id",
            "llm_node_model_routing_policies",
        ),
        (
            "llm_node_model_routing_cohorts",
            "policy_id",
            "llm_node_model_routing_policies",
        ),
        (
            "llm_node_model_routing_observations",
            "policy_id",
            "llm_node_model_routing_policies",
        ),
        (
            "llm_node_model_routing_validation_batches",
            "policy_id",
            "llm_node_model_routing_policies",
        ),
        (
            "llm_node_model_routing_validation_budget_months",
            "policy_id",
            "llm_node_model_routing_policies",
        ),
    }
    assert history_lifecycle_fks.isdisjoint(foreign_keys)

    assert foreign_keys[
        ("agent_builder_sessions", "app_id", "apps")
    ] == "SET NULL"
    assert foreign_keys[
        ("agent_builder_sessions", "workflow_id", "workflows")
    ] == "SET NULL"
    assert foreign_keys[("agent_builder_drafts", "app_id", "apps")] == "SET NULL"
    assert foreign_keys[
        ("agent_builder_drafts", "workflow_id", "workflows")
    ] == "SET NULL"


def test_delete_preserves_completed_run_trace_and_usage_history(db_session):
    context = _new_app_context(db_session)
    deployment = _new_deployment(db_session, context)
    run = WorkflowRun(
        workflow_id=context.workflow.id,
        user_id=context.actor.id,
        app_id=context.app.id,
        deployment_id=deployment.id,
        status=RunStatus.SUCCESS,
        trigger_mode=RunTriggerMode.MANUAL,
        inputs={},
        outputs={"result": "safe"},
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="node-1",
        node_type="answerNode",
        status=NodeRunStatus.SUCCESS,
        inputs={},
        process_data={},
        outputs={"result": "safe"},
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(node_run)
    db_session.flush()
    trace = TracePayload(
        workflow_run_id=run.id,
        workflow_node_run_id=node_run.id,
        scope="node",
        payload_kind="finish",
        redacted_payload={"status": "success"},
    )
    db_session.add(trace)
    db_session.flush()
    trace_access = TracePayloadAccessEvent(
        payload_id=trace.id,
        workflow_run_id=run.id,
        actor_user_id=context.actor.id,
        view_level="metadata",
        allowed=True,
        reason_code="owner_allowed",
    )
    provider = LLMProvider(
        name=f"provider-{uuid.uuid4().hex}",
        type="custom",
        auth_type="api_key",
        doc_url="https://example.invalid/docs",
    )
    db_session.add(provider)
    db_session.flush()
    model = LLMModel(
        provider_id=provider.id,
        model_id_for_api_call=f"model-{uuid.uuid4().hex}",
        name="MBA-87 Model",
        type="chat",
        context_window=1024,
        is_active=True,
    )
    credential = LLMCredential(
        provider_id=provider.id,
        user_id=context.actor.id,
        organization_id=context.organization.id,
        credential_name="MBA-87 Credential",
        encrypted_config="encrypted-test-placeholder",
    )
    db_session.add_all([model, credential])
    db_session.flush()
    usage = LLMUsageLog(
        user_id=context.actor.id,
        organization_id=context.organization.id,
        credential_id=credential.id,
        model_id=model.id,
        workflow_id=context.workflow.id,
        workflow_run_id=run.id,
        node_id="node-1",
        prompt_tokens=1,
        completion_tokens=1,
        total_cost=0.01,
        latency_ms=1,
        status="success",
    )
    db_session.add_all([trace_access, usage])
    db_session.commit()

    original_ids = {
        "app_id": context.app.id,
        "workflow_id": context.workflow.id,
        "deployment_id": deployment.id,
    }
    assert (
        AppService.delete_app(db_session, str(context.app.id), context.actor.id)
        is True
    )

    db_session.expunge_all()
    retained_run = db_session.get(WorkflowRun, run.id)
    assert retained_run is not None
    assert retained_run.app_id == original_ids["app_id"]
    assert retained_run.workflow_id == original_ids["workflow_id"]
    assert retained_run.deployment_id == original_ids["deployment_id"]
    assert db_session.get(WorkflowNodeRun, node_run.id) is not None
    assert db_session.get(TracePayload, trace.id) is not None
    assert db_session.get(TracePayloadAccessEvent, trace_access.id) is not None
    retained_usage = db_session.get(LLMUsageLog, usage.id)
    assert retained_usage is not None
    assert retained_usage.workflow_id == original_ids["workflow_id"]


def test_delete_preserves_cost_mail_routing_and_external_effect_history(db_session):
    context = _new_app_context(db_session)
    deployment = _new_deployment(db_session, context)
    experiment = CostOptimizerExperiment(
        organization_id=context.organization.id,
        workflow_id=context.workflow.id,
        app_id=context.app.id,
        node_id="llm-1",
        created_by=context.actor.id,
    )
    db_session.add(experiment)
    db_session.flush()
    candidate = CostOptimizerCandidate(
        experiment_id=experiment.id,
        name="candidate",
        model_id="test-model",
        candidate_settings={},
    )
    verification = CostOptimizerRecommendationVerification(
        workflow_id=context.workflow.id,
        node_id="llm-1",
        created_by=context.actor.id,
        idempotency_key=f"mba87-{uuid.uuid4().hex}",
        request_fingerprint="a" * 64,
        status="completed",
        experiment_id=experiment.id,
        response_summary={},
    )
    db_session.add_all([candidate, verification])

    routing_policy = LLMNodeModelRoutingPolicy(
        organization_id=context.organization.id,
        workflow_id=context.workflow.id,
        deployment_id=deployment.id,
        node_id="llm-1",
        enabled=True,
        status="active",
        active_policy={},
    )
    db_session.add(routing_policy)
    db_session.flush()
    routing_update = LLMNodeModelRoutingPolicyUpdate(
        policy_id=routing_policy.id,
        trigger="manual",
        status="applied",
    )

    mail_credential = MailCredential(
        organization_id=context.organization.id,
        credential_name="MBA-87 Mail",
        provider="gmail",
        email_address="mba87@example.invalid",
        auth_type="password",
        imap_host="imap.example.invalid",
        imap_port=993,
        use_ssl=True,
        encrypted_secret="encrypted-test-placeholder",
        encryption_key_version="v1",
        encryption_algorithm="test",
        status="active",
        created_by=context.actor.id,
    )
    db_session.add(mail_credential)
    db_session.flush()
    completed_at = datetime.now(timezone.utc)
    processing = MailMessageProcessing(
        organization_id=context.organization.id,
        workflow_id=context.workflow.id,
        deployment_id=deployment.id,
        source_node_id="mail-source",
        credential_id=mail_credential.id,
        provider="gmail",
        message_identity_hash="b" * 64,
        encrypted_source_reference="encrypted-test-placeholder",
        source_key_version="v1",
        source_algorithm="test",
        status="succeeded",
        completed_at=completed_at,
    )
    db_session.add(processing)
    db_session.flush()
    draft_effect = MailDraftEffect(
        processing_id=processing.id,
        node_id="mail-draft",
        operation_key_hash="c" * 64,
        input_digest="d" * 64,
        status="succeeded",
        encrypted_draft_reference="encrypted-test-placeholder",
        draft_key_version="v1",
        draft_algorithm="test",
        completed_at=completed_at,
    )
    effect_attempt = WorkflowNodeEffectAttempt(
        organization_id=context.organization.id,
        app_id=context.app.id,
        workflow_id=context.workflow.id,
        execution_id=uuid.uuid4(),
        node_invocation_id=uuid.uuid4(),
        node_id="http-1",
        operation="http.post",
        effect_sequence=0,
        provider="http",
        provider_contract_version="v1",
        provider_replay_capability="unknown",
        result_reuse_capability="unavailable",
        effect_input_digest="e" * 64,
        status="terminal",
        outcome="succeeded",
        replay_decision="result_unavailable",
        claim_owner=None,
        claim_expires_at=None,
        claim_generation=1,
        provider_started_at=completed_at,
        terminal_at=completed_at,
    )
    schedule_claim = ScheduleDispatchClaim(
        schedule_id=uuid.uuid4(),
        organization_id=context.organization.id,
        deployment_id=deployment.id,
        scheduled_for=completed_at,
        idempotency_key=f"schedule:{uuid.uuid4()}",
        status="succeeded",
        celery_task_id=f"schedule:{uuid.uuid4()}",
        workflow_run_id=uuid.uuid4(),
        claimed_at=completed_at,
        enqueued_at=completed_at,
        started_at=completed_at,
        completed_at=completed_at,
    )
    schedule_claim.celery_task_id = schedule_claim.idempotency_key
    db_session.add_all(
        [routing_update, draft_effect, effect_attempt, schedule_claim]
    )
    db_session.commit()

    assert (
        AppService.delete_app(db_session, str(context.app.id), context.actor.id)
        is True
    )

    db_session.expunge_all()
    assert db_session.get(CostOptimizerExperiment, experiment.id) is not None
    assert db_session.get(CostOptimizerCandidate, candidate.id) is not None
    assert (
        db_session.get(CostOptimizerRecommendationVerification, verification.id)
        is not None
    )
    assert db_session.get(LLMNodeModelRoutingPolicy, routing_policy.id) is None
    assert (
        db_session.get(LLMNodeModelRoutingPolicyUpdate, routing_update.id) is not None
    )
    assert db_session.get(MailMessageProcessing, processing.id) is not None
    assert db_session.get(MailDraftEffect, draft_effect.id) is not None
    assert db_session.get(WorkflowNodeEffectAttempt, effect_attempt.id) is not None
    assert db_session.get(ScheduleDispatchClaim, schedule_claim.id) is not None


def _add_delete_blocker(db_session, context, blocker: str):
    now = datetime.now(timezone.utc)
    if blocker == "running_run":
        row = WorkflowRun(
            workflow_id=context.workflow.id,
            user_id=context.actor.id,
            app_id=context.app.id,
            status=RunStatus.RUNNING,
            trigger_mode=RunTriggerMode.MANUAL,
            inputs={},
        )
    elif blocker == "pending_schedule":
        deployment = _new_deployment(db_session, context)
        row = ScheduleDispatchClaim(
            schedule_id=uuid.uuid4(),
            organization_id=context.organization.id,
            deployment_id=deployment.id,
            scheduled_for=now,
            idempotency_key=f"schedule:{uuid.uuid4()}",
            status="pending",
            claimed_at=now,
        )
    elif blocker == "prepared_effect":
        row = WorkflowNodeEffectAttempt(
            organization_id=context.organization.id,
            app_id=context.app.id,
            workflow_id=context.workflow.id,
            execution_id=uuid.uuid4(),
            node_invocation_id=uuid.uuid4(),
            node_id="http-1",
            operation="http.post",
            effect_sequence=0,
            provider="http",
            provider_contract_version="v1",
            provider_replay_capability="unknown",
            result_reuse_capability="unavailable",
            effect_input_digest="f" * 64,
            status="prepared",
            claim_owner="worker",
            claim_expires_at=now + timedelta(minutes=5),
            claim_generation=1,
        )
    elif blocker == "pending_mail":
        credential = MailCredential(
            organization_id=context.organization.id,
            credential_name="MBA-87 Mail Blocker",
            provider="gmail",
            email_address="blocker@example.invalid",
            auth_type="password",
            imap_host="imap.example.invalid",
            imap_port=993,
            use_ssl=True,
            encrypted_secret="encrypted-test-placeholder",
            encryption_key_version="v1",
            encryption_algorithm="test",
            status="active",
            created_by=context.actor.id,
        )
        db_session.add(credential)
        db_session.flush()
        row = MailMessageProcessing(
            organization_id=context.organization.id,
            workflow_id=context.workflow.id,
            source_node_id="mail-source",
            credential_id=credential.id,
            provider="gmail",
            message_identity_hash="1" * 64,
            encrypted_source_reference="encrypted-test-placeholder",
            source_key_version="v1",
            source_algorithm="test",
            status="pending",
        )
    else:
        raise AssertionError(f"unknown blocker: {blocker}")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.mark.parametrize(
    "blocker",
    ["running_run", "pending_schedule", "prepared_effect", "pending_mail"],
)
def test_delete_returns_409_without_mutation_for_active_operations(
    db_session,
    monkeypatch,
    blocker,
):
    context = _new_app_context(db_session)
    blocker_row = _add_delete_blocker(db_session, context, blocker)
    client = _client(db_session, context.actor, monkeypatch)
    try:
        response = client.delete(
            f"/api/v1/apps/{context.app.id}",
            headers={"X-Organization-Id": str(context.organization.id)},
        )
    finally:
        _close_client(client)

    assert response.status_code == 409
    assert _error_code(response) == "app.delete_in_progress"
    assert db_session.get(App, context.app.id) is not None
    assert db_session.get(Workflow, context.workflow.id) is not None
    assert db_session.get(type(blocker_row), blocker_row.id) is not None


def test_delete_requires_active_organization_header(db_session, monkeypatch):
    context = _new_app_context(db_session)
    client = _client(db_session, context.actor, monkeypatch)
    try:
        response = client.delete(f"/api/v1/apps/{context.app.id}")
    finally:
        _close_client(client)

    assert response.status_code == 400
    assert _error_code(response) == "organization.required"
    assert db_session.get(App, context.app.id) is not None


def test_delete_returns_safe_403_and_hidden_404(db_session, monkeypatch):
    context = _new_app_context(db_session)
    same_org_member = _new_user(db_session, "same-org-member")
    _add_membership(
        db_session,
        user=same_org_member,
        organization=context.organization,
        invited_by=context.actor,
    )
    other_org_member = _new_user(db_session, "other-org-member")
    other_organization = _new_organization(
        db_session,
        other_org_member,
        auth_state="member",
    )
    db_session.commit()

    same_org_client = _client(db_session, same_org_member, monkeypatch)
    try:
        forbidden = same_org_client.delete(
            f"/api/v1/apps/{context.app.id}",
            headers={"X-Organization-Id": str(context.organization.id)},
        )
    finally:
        _close_client(same_org_client)
    assert forbidden.status_code == 403
    assert _error_code(forbidden) == "permission.denied"

    other_org_client = _client(db_session, other_org_member, monkeypatch)
    try:
        hidden = other_org_client.delete(
            f"/api/v1/apps/{context.app.id}",
            headers={"X-Organization-Id": str(other_organization.id)},
        )
        missing = other_org_client.delete(
            f"/api/v1/apps/{uuid.uuid4()}",
            headers={"X-Organization-Id": str(other_organization.id)},
        )
    finally:
        _close_client(other_org_client)
    assert hidden.status_code == 404
    assert _error_code(hidden) == "resource.not_found"
    assert missing.status_code == 404
    assert _error_code(missing) == "resource.not_found"


def test_repeated_delete_returns_safe_404(db_session, monkeypatch):
    context = _new_app_context(db_session)
    client = _client(db_session, context.actor, monkeypatch)
    headers = {"X-Organization-Id": str(context.organization.id)}
    try:
        first = client.delete(f"/api/v1/apps/{context.app.id}", headers=headers)
        second = client.delete(f"/api/v1/apps/{context.app.id}", headers=headers)
    finally:
        _close_client(client)

    assert first.status_code == 200
    assert first.json() == {"message": "App deleted successfully"}
    assert second.status_code == 404
    assert _error_code(second) == "resource.not_found"


def test_delete_rolls_back_every_change_when_final_app_delete_fails(
    db_session,
    db_runtime,
):
    context = _new_app_context(db_session)
    app_id = context.app.id
    workflow_id = context.workflow.id
    actor_id = context.actor.id

    def fail_app_delete(conn, cursor, statement, parameters, execution_context, many):
        normalized = statement.lstrip().upper()
        if normalized.startswith("DELETE FROM APPS"):
            raise RuntimeError("injected app delete failure")

    event.listen(db_runtime.engine, "before_cursor_execute", fail_app_delete)
    try:
        with pytest.raises(RuntimeError, match="injected app delete failure"):
            AppService.delete_app(
                db_session,
                str(app_id),
                actor_id,
            )
    finally:
        event.remove(db_runtime.engine, "before_cursor_execute", fail_app_delete)

    observer = db_runtime.session_factory()
    try:
        assert observer.get(App, app_id) is not None
        assert observer.get(Workflow, workflow_id) is not None
    finally:
        observer.close()
    assert db_session.in_transaction() is False


def test_concurrent_delete_has_one_success_and_one_not_found(db_session, db_runtime):
    context = _new_app_context(db_session)
    function_name = f"mba87_pause_delete_{uuid.uuid4().hex}"
    trigger_name = f"mba87_pause_delete_{uuid.uuid4().hex}"
    db_session.execute(
        text(
            f"""
            CREATE FUNCTION {function_name}() RETURNS trigger AS $$
            BEGIN
              PERFORM pg_sleep(0.4);
              RETURN OLD;
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER {trigger_name}
            BEFORE DELETE ON workflows
            FOR EACH ROW
            WHEN (OLD.id = '{context.workflow.id}'::uuid)
            EXECUTE FUNCTION {function_name}();
            """
        )
    )
    db_session.commit()
    barrier = threading.Barrier(2)

    def delete_once():
        session = db_runtime.session_factory()
        try:
            barrier.wait(timeout=5)
            return ("ok", AppService.delete_app(session, str(context.app.id), context.actor.id))
        except Exception as exc:  # failure is asserted below without leaking DB detail
            return ("error", type(exc).__name__)
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: delete_once(), range(2)))
    finally:
        cleanup = db_runtime.session_factory()
        try:
            cleanup.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name} ON workflows"))
            cleanup.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))
            cleanup.commit()
        finally:
            cleanup.close()

    errors = [value for kind, value in results if kind == "error"]
    assert errors == [], results
    values = [value for kind, value in results if kind == "ok"]
    assert values.count(True) == 1
    assert values.count(None) == 1


def test_delete_and_run_admission_race_is_atomic(db_session, db_runtime):
    context = _new_app_context(db_session)
    barrier = threading.Barrier(2)

    def admit_once():
        session = db_runtime.session_factory()
        try:
            barrier.wait(timeout=5)
            run = admit_workflow_run(
                session,
                run_id=uuid.uuid4(),
                app_id=context.app.id,
                workflow_id=context.workflow.id,
                organization_id=context.organization.id,
                user_id=context.actor.id,
                trigger_mode=RunTriggerMode.MANUAL,
                inputs={},
            )
            return ("admitted", run.id)
        except AppWorkflowAdmissionUnavailable:
            return ("unavailable", None)
        finally:
            session.close()

    def delete_once():
        session = db_runtime.session_factory()
        try:
            barrier.wait(timeout=5)
            try:
                return ("deleted", AppService.delete_app(
                    session,
                    str(context.app.id),
                    context.actor.id,
                ))
            except AppDeleteInProgress:
                return ("blocked", None)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        admission_future = executor.submit(admit_once)
        deletion_future = executor.submit(delete_once)
        admission_result = admission_future.result(timeout=10)
        deletion_result = deletion_future.result(timeout=10)

    observer = db_runtime.session_factory()
    try:
        if admission_result[0] == "admitted":
            assert deletion_result[0] == "blocked"
            assert observer.get(App, context.app.id) is not None
            assert observer.get(WorkflowRun, admission_result[1]) is not None
        else:
            assert admission_result == ("unavailable", None)
            assert deletion_result == ("deleted", True)
            assert observer.get(App, context.app.id) is None
    finally:
        observer.close()


def test_delete_does_not_rewrite_other_workflow_graph_references(db_session):
    target = _new_app_context(db_session)
    original_graph = {
        "nodes": [
            {
                "id": "workflow-node",
                "type": "workflowNode",
                "data": {"appId": str(target.app.id)},
            }
        ],
        "edges": [],
    }
    source = _new_app_context(db_session, graph=original_graph)

    assert AppService.delete_app(db_session, str(target.app.id), target.actor.id) is True
    db_session.refresh(source.workflow)
    assert source.workflow.graph == original_graph


def test_delete_detaches_agent_builder_session_instead_of_deleting_it(db_session):
    context = _new_app_context(db_session)
    builder_session = AgentBuilderSession(
        organization_id=context.organization.id,
        user_id=context.actor.id,
        workflow_id=context.workflow.id,
        app_id=context.app.id,
        status="active",
    )
    db_session.add(builder_session)
    db_session.commit()

    assert (
        AppService.delete_app(db_session, str(context.app.id), context.actor.id)
        is True
    )
    db_session.refresh(builder_session)
    assert builder_session.app_id is None
    assert builder_session.workflow_id is None
