import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from apps.gateway.adapters.audit.resource_permission_mutations import (
    SqlAlchemyResourcePermissionMutationAudit,
)
from apps.gateway.adapters.db.resource_permission_mutations import (
    SqlAlchemyResourcePermissionMutationRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.application.resource_permissions.mutation import (
    PermissionMutationCommand,
    PermissionMutationNotFound,
    PermissionMutationPersistenceFailed,
    ResourcePermissionMutationUseCase,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.llm import LLMCredential, LLMProvider
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.team import Team, TeamWorkflowPermission, UserLLMPermission
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)


ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mba284_permission"


@dataclass(frozen=True)
class _Catalog:
    actor_id: uuid.UUID
    target_user_id: uuid.UUID
    organization_id: uuid.UUID
    team_id: uuid.UUID
    workflow_id: uuid.UUID
    credential_id: uuid.UUID


@dataclass(frozen=True)
class _PostgresHarness:
    sessions: sessionmaker
    engine: Engine

    def __call__(self) -> Session:
        return self.sessions()


class _FailingAudit:
    def record(self, _command, _result):
        raise RuntimeError("simulated audit persistence failure")


class _HoldingAudit:
    def __init__(self, db, actor_id, lock_held, release_first):
        self.delegate = SqlAlchemyResourcePermissionMutationAudit(
            db,
            actor=SimpleNamespace(
                id=actor_id,
                email="mba284-actor@example.invalid",
                name="MBA-284 actor",
            ),
        )
        self.lock_held = lock_held
        self.release_first = release_first

    def record(self, command, result):
        self.delegate.record(command, result)
        self.lock_held.set()
        if not self.release_first.wait(timeout=10):
            raise RuntimeError("concurrent permission mutation release timed out")


def _use_case(db, actor_id, *, audit=None):
    actor = SimpleNamespace(
        id=actor_id,
        email="mba284-actor@example.invalid",
        name="MBA-284 actor",
    )
    return ResourcePermissionMutationUseCase(
        SqlAlchemyResourcePermissionMutationRepository(db),
        audit or SqlAlchemyResourcePermissionMutationAudit(db, actor=actor),
        SqlAlchemyUnitOfWork(db),
    )


def _command(
    catalog,
    *,
    resource_type,
    grantee_type,
    operation="upsert",
    auth_state="builder",
):
    return PermissionMutationCommand(
        actor_id=catalog.actor_id,
        organization_id=catalog.organization_id,
        resource_type=resource_type,
        resource_id=(
            catalog.workflow_id
            if resource_type == "workflow"
            else catalog.credential_id
        ),
        grantee_type=grantee_type,
        grantee_id=(
            catalog.team_id
            if grantee_type == "team"
            else catalog.target_user_id
        ),
        operation=operation,
        auth_state=auth_state if operation == "upsert" else None,
        assigned_at=datetime.now(timezone.utc),
    )


def _run_migrations(database, config):
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


def _enable_vector_extension(database, config):
    engine = create_engine(
        config.database_url(database),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def postgres_session_factory():
    if os.getenv(RUN_ENV) != "1":
        pytest.skip(f"set {RUN_ENV}=1 to run disposable PostgreSQL integration")
    try:
        config = DisposablePostgresConfig.from_environment()
    except DisposablePostgresConfigurationError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL settings are not safely configured",
            pytrace=False,
        ) from None

    database = f"{DB_PREFIX}_{uuid.uuid4().hex[:12]}"
    quoted_database = quote_disposable_database_name(database, prefix=DB_PREFIX)
    admin_engine = create_engine(
        config.database_url(config.maintenance_database),
        isolation_level="AUTOCOMMIT",
    )
    engine = None
    database_created = False
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {quoted_database}"))
        database_created = True
        _enable_vector_extension(database, config)
        _run_migrations(database, config)
        engine = create_engine(config.database_url(database), pool_pre_ping=True)
        yield _PostgresHarness(
            sessions=sessionmaker(bind=engine, expire_on_commit=False),
            engine=engine,
        )
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable; connection details omitted",
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
                    connection.execute(
                        text(f"DROP DATABASE IF EXISTS {quoted_database}")
                    )
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup failed; details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()


def _seed_catalog(session_factory) -> _Catalog:
    catalog = _Catalog(
        actor_id=uuid.uuid4(),
        target_user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
    )
    provider_id = uuid.uuid4()
    app_id = uuid.uuid4()
    suffix = uuid.uuid4().hex
    with session_factory() as db:
        db.add_all(
            [
                User(
                    id=catalog.actor_id,
                    email=f"mba284-actor-{suffix}@example.invalid",
                    name="MBA-284 actor",
                    social_provider="local",
                ),
                User(
                    id=catalog.target_user_id,
                    email=f"mba284-target-{suffix}@example.invalid",
                    name="MBA-284 target",
                    social_provider="local",
                ),
            ]
        )
        db.flush()
        db.add(
            Organization(
                id=catalog.organization_id,
                name=f"MBA-284 {suffix}",
                created_by=catalog.actor_id,
                managed_by=catalog.actor_id,
            )
        )
        db.flush()
        db.add_all(
            [
                Team(
                    id=catalog.team_id,
                    organization_id=catalog.organization_id,
                    name=f"MBA-284 team {suffix}",
                    created_by=catalog.actor_id,
                    managed_by=catalog.actor_id,
                ),
                LLMProvider(
                    id=provider_id,
                    name=f"mba284-provider-{suffix}",
                    type="custom",
                    auth_type="api_key",
                    doc_url="https://example.invalid/provider",
                ),
                App(
                    id=app_id,
                    organization_id=catalog.organization_id,
                    name=f"MBA-284 app {suffix}",
                    url_slug=f"mba284-app-{suffix}",
                    auth_secret=None,
                    created_by=catalog.actor_id,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                Workflow(
                    id=catalog.workflow_id,
                    organization_id=catalog.organization_id,
                    app_id=app_id,
                    graph={},
                    created_by=catalog.actor_id,
                ),
                LLMCredential(
                    id=catalog.credential_id,
                    provider_id=provider_id,
                    user_id=catalog.actor_id,
                    organization_id=catalog.organization_id,
                    credential_name="MBA-284 credential",
                    encrypted_config="***",
                    config_preview=None,
                ),
            ]
        )
        db.commit()
    return catalog


def test_permission_success_noop_and_canonical_audit(postgres_session_factory):
    catalog = _seed_catalog(postgres_session_factory)
    command = _command(
        catalog,
        resource_type="workflow",
        grantee_type="team",
    )
    with postgres_session_factory() as db:
        created = _use_case(db, catalog.actor_id).execute(command)
        assert created.status == "created"
        permission_id = created.permission.permission_id

    with postgres_session_factory() as db:
        unchanged = _use_case(db, catalog.actor_id).execute(command)
        assert unchanged.status == "unchanged"

    with postgres_session_factory() as db:
        assert db.scalar(
            select(func.count()).select_from(TeamWorkflowPermission).where(
                TeamWorkflowPermission.id == permission_id
            )
        ) == 1
        audits = db.scalars(
            select(AuditLog).where(AuditLog.target_id == str(permission_id))
        ).all()
        assert [audit.action for audit in audits] == [
            "team_workflow_permission.created"
        ]
        assert audits[0].audit_metadata["organization_id"] == str(
            catalog.organization_id
        )
        assert set(audits[0].after) == {
            "grantee_organization_id",
            "team_id",
            "workflow_id",
            "auth_state",
        }


def test_audit_failure_rolls_back_create_update_and_delete(postgres_session_factory):
    catalog = _seed_catalog(postgres_session_factory)
    team_create = _command(
        catalog,
        resource_type="workflow",
        grantee_type="team",
        auth_state="viewer",
    )
    with postgres_session_factory() as db:
        with pytest.raises(PermissionMutationPersistenceFailed):
            _use_case(db, catalog.actor_id, audit=_FailingAudit()).execute(team_create)
    with postgres_session_factory() as db:
        assert db.scalar(
            select(func.count()).select_from(TeamWorkflowPermission).where(
                TeamWorkflowPermission.workflow_id == catalog.workflow_id,
                TeamWorkflowPermission.team_id == catalog.team_id,
            )
        ) == 0

    with postgres_session_factory() as db:
        created_team = _use_case(db, catalog.actor_id).execute(team_create)
        team_permission_id = created_team.permission.permission_id
        created_user = _use_case(db, catalog.actor_id).execute(
            _command(
                catalog,
                resource_type="llm_credential",
                grantee_type="user",
                auth_state="operator",
            )
        )
        user_permission_id = created_user.permission.permission_id

    with postgres_session_factory() as db:
        with pytest.raises(PermissionMutationPersistenceFailed):
            _use_case(db, catalog.actor_id, audit=_FailingAudit()).execute(
                _command(
                    catalog,
                    resource_type="workflow",
                    grantee_type="team",
                    auth_state="manager",
                )
            )
    with postgres_session_factory() as db:
        assert db.get(TeamWorkflowPermission, team_permission_id).auth_state == "viewer"

    with postgres_session_factory() as db:
        with pytest.raises(PermissionMutationPersistenceFailed):
            _use_case(db, catalog.actor_id, audit=_FailingAudit()).execute(
                _command(
                    catalog,
                    resource_type="llm_credential",
                    grantee_type="user",
                    operation="delete",
                )
            )
    with postgres_session_factory() as db:
        assert db.get(UserLLMPermission, user_permission_id) is not None
        assert db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.target_id == str(user_permission_id),
                AuditLog.action == "user_llm_permission.deleted",
            )
        ) == 0


def test_concurrent_same_upsert_creates_one_row_and_one_audit(
    postgres_session_factory,
):
    catalog = _seed_catalog(postgres_session_factory)
    command = _command(
        catalog,
        resource_type="workflow",
        grantee_type="team",
    )
    lock_held = threading.Event()
    release_first = threading.Event()
    second_lock_started = threading.Event()
    results = []
    errors = []
    result_lock = threading.Lock()

    engine = postgres_session_factory.engine

    def observe_lock(_conn, _cursor, statement, _params, _context, _many):
        if (
            threading.current_thread().name == "mba284-upsert-second"
            and "pg_advisory_xact_lock" in statement.lower()
        ):
            second_lock_started.set()

    event.listen(engine, "before_cursor_execute", observe_lock)

    def run(*, hold):
        db = postgres_session_factory()
        try:
            audit = (
                _HoldingAudit(
                    db,
                    catalog.actor_id,
                    lock_held,
                    release_first,
                )
                if hold
                else None
            )
            result = _use_case(db, catalog.actor_id, audit=audit).execute(command)
            with result_lock:
                results.append(result.status)
        except Exception as exc:
            with result_lock:
                errors.append(type(exc).__name__)
        finally:
            db.close()

    first = threading.Thread(
        target=run,
        kwargs={"hold": True},
        name="mba284-upsert-first",
    )
    second = threading.Thread(
        target=run,
        kwargs={"hold": False},
        name="mba284-upsert-second",
    )
    try:
        first.start()
        assert lock_held.wait(timeout=10)
        second.start()
        assert second_lock_started.wait(timeout=10)
        assert second.is_alive()
        release_first.set()
        first.join(timeout=10)
        second.join(timeout=10)
        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        assert sorted(results) == ["created", "unchanged"]
    finally:
        release_first.set()
        for worker in (first, second):
            if worker.is_alive():
                worker.join(timeout=10)
        event.remove(engine, "before_cursor_execute", observe_lock)

    with postgres_session_factory() as db:
        permissions = db.scalars(
            select(TeamWorkflowPermission).where(
                TeamWorkflowPermission.workflow_id == catalog.workflow_id,
                TeamWorkflowPermission.team_id == catalog.team_id,
            )
        ).all()
        assert len(permissions) == 1
        assert db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.target_id == str(permissions[0].id),
                AuditLog.action == "team_workflow_permission.created",
            )
        ) == 1


def test_concurrent_delete_records_one_audit(postgres_session_factory):
    catalog = _seed_catalog(postgres_session_factory)
    with postgres_session_factory() as db:
        created = _use_case(db, catalog.actor_id).execute(
            _command(
                catalog,
                resource_type="llm_credential",
                grantee_type="user",
                auth_state="operator",
            )
        )
        permission_id = created.permission.permission_id

    command = _command(
        catalog,
        resource_type="llm_credential",
        grantee_type="user",
        operation="delete",
    )
    lock_held = threading.Event()
    release_first = threading.Event()
    second_lock_started = threading.Event()
    results = []
    errors = []
    result_lock = threading.Lock()

    def observe_lock(_conn, _cursor, statement, _params, _context, _many):
        if (
            threading.current_thread().name == "mba284-delete-second"
            and "pg_advisory_xact_lock" in statement.lower()
        ):
            second_lock_started.set()

    event.listen(
        postgres_session_factory.engine,
        "before_cursor_execute",
        observe_lock,
    )

    def run(*, hold):
        db = postgres_session_factory()
        try:
            audit = (
                _HoldingAudit(
                    db,
                    catalog.actor_id,
                    lock_held,
                    release_first,
                )
                if hold
                else None
            )
            result = _use_case(db, catalog.actor_id, audit=audit).execute(command)
            status = result.status
        except PermissionMutationNotFound:
            status = "not_found"
        except Exception as exc:
            with result_lock:
                errors.append(type(exc).__name__)
            return
        finally:
            db.close()
        with result_lock:
            results.append(status)

    first = threading.Thread(
        target=run,
        kwargs={"hold": True},
        name="mba284-delete-first",
    )
    second = threading.Thread(
        target=run,
        kwargs={"hold": False},
        name="mba284-delete-second",
    )
    try:
        first.start()
        assert lock_held.wait(timeout=10)
        second.start()
        assert second_lock_started.wait(timeout=10)
        assert second.is_alive()
        release_first.set()
        first.join(timeout=10)
        second.join(timeout=10)
        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        assert sorted(results) == ["deleted", "not_found"]
    finally:
        release_first.set()
        for worker in (first, second):
            if worker.is_alive():
                worker.join(timeout=10)
        event.remove(
            postgres_session_factory.engine,
            "before_cursor_execute",
            observe_lock,
        )

    with postgres_session_factory() as db:
        assert db.get(UserLLMPermission, permission_id) is None
        assert db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.target_id == str(permission_id),
                AuditLog.action == "user_llm_permission.deleted",
            )
        ) == 1
