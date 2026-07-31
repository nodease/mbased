import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from apps.gateway.adapters.audit.knowledge_domain_permissions import (
    SqlAlchemyKnowledgeDomainPermissionAudit,
)
from apps.gateway.adapters.db.knowledge_domain_permissions import (
    SqlAlchemyKnowledgeDomainPermissionRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.application.knowledge_administration.domain_permissions import (
    DomainPermissionCommand,
    DomainPermissionPersistenceFailed,
    KnowledgeDomainPermissionUseCase,
)
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.team import UserKnowledgeDomainPermission
from apps.shared.db.models.user import User
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)


ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mba241_revoke"


class _AllowOrganizationManager:
    def is_organization_manager(self, _actor_id, _organization_id):
        return True

    def effective_actions(self, _actor_id, _organization_id):
        return set()


class _FailingAudit:
    def record(self, **_kwargs):
        raise RuntimeError("simulated audit failure")


class _HoldingAudit:
    def __init__(self, db, lock_held, release_first):
        self.delegate = SqlAlchemyKnowledgeDomainPermissionAudit(db)
        self.lock_held = lock_held
        self.release_first = release_first

    def record(self, **kwargs):
        self.delegate.record(**kwargs)
        self.lock_held.set()
        if not self.release_first.wait(timeout=10):
            raise RuntimeError("concurrent revoke release timed out")


def _use_case(db, *, audit=None):
    return KnowledgeDomainPermissionUseCase(
        _AllowOrganizationManager(),
        SqlAlchemyKnowledgeDomainPermissionRepository(db),
        audit or SqlAlchemyKnowledgeDomainPermissionAudit(db),
        SqlAlchemyUnitOfWork(db),
    )


def _command(*, actor_id, organization_id, subject_id, action):
    return DomainPermissionCommand(
        actor_id=actor_id,
        organization_id=organization_id,
        subject_type="user",
        subject_id=subject_id,
        permission_action=action,
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


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL revoke integration",
)
def test_domain_permission_revoke_is_scoped_atomic_and_serialized():
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
    release_first = None
    first = None
    second = None

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {quoted_database}"))
        database_created = True
        _enable_vector_extension(database, config)
        _run_migrations(database, config)

        engine = create_engine(config.database_url(database), pool_pre_ping=True)
        session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
        )
        actor_id = uuid.uuid4()
        subject_id = uuid.uuid4()
        organization_a = uuid.uuid4()
        organization_b = uuid.uuid4()
        catalog_a_id = uuid.uuid4()
        catalog_b_id = uuid.uuid4()
        rollback_permission_id = uuid.uuid4()
        concurrent_permission_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        with session_factory() as db:
            db.add_all(
                [
                    User(
                        id=actor_id,
                        email="mba241-actor@example.invalid",
                        name="MBA-241 actor",
                        social_provider="local",
                    ),
                    User(
                        id=subject_id,
                        email="mba241-subject@example.invalid",
                        name="MBA-241 subject",
                        social_provider="local",
                    ),
                    Organization(
                        id=organization_a,
                        name="MBA-241 organization A",
                        created_by=actor_id,
                    ),
                    Organization(
                        id=organization_b,
                        name="MBA-241 organization B",
                        created_by=actor_id,
                    ),
                ]
            )
            db.flush()
            db.add_all(
                [
                    UserKnowledgeDomainPermission(
                        id=catalog_a_id,
                        organization_id=organization_a,
                        user_id=subject_id,
                        permission_action="catalog_manage",
                        assigned_by=actor_id,
                        assigned_at=now,
                    ),
                    UserKnowledgeDomainPermission(
                        id=catalog_b_id,
                        organization_id=organization_b,
                        user_id=subject_id,
                        permission_action="catalog_manage",
                        assigned_by=actor_id,
                        assigned_at=now,
                    ),
                    UserKnowledgeDomainPermission(
                        id=rollback_permission_id,
                        organization_id=organization_a,
                        user_id=subject_id,
                        permission_action="sync_manage",
                        assigned_by=actor_id,
                        assigned_at=now,
                    ),
                    UserKnowledgeDomainPermission(
                        id=concurrent_permission_id,
                        organization_id=organization_a,
                        user_id=subject_id,
                        permission_action="lifecycle_manage",
                        assigned_by=actor_id,
                        assigned_at=now,
                    ),
                ]
            )
            db.commit()

        with session_factory() as db:
            result = _use_case(db).revoke(
                _command(
                    actor_id=actor_id,
                    organization_id=organization_a,
                    subject_id=subject_id,
                    action="catalog_manage",
                )
            )
            assert result.status == "deleted"

        with session_factory() as db:
            remaining_catalog = db.scalars(
                select(UserKnowledgeDomainPermission).where(
                    UserKnowledgeDomainPermission.permission_action
                    == "catalog_manage"
                )
            ).all()
            assert [row.organization_id for row in remaining_catalog] == [
                organization_b
            ]
            catalog_a_audit_count = db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.target_id == str(catalog_a_id))
            )
            catalog_b_audit_count = db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.target_id == str(catalog_b_id))
            )
            assert catalog_a_audit_count == 1
            assert catalog_b_audit_count == 0

        with session_factory() as db:
            with pytest.raises(DomainPermissionPersistenceFailed):
                _use_case(db, audit=_FailingAudit()).revoke(
                    _command(
                        actor_id=actor_id,
                        organization_id=organization_a,
                        subject_id=subject_id,
                        action="sync_manage",
                    )
                )

        with session_factory() as db:
            assert db.get(
                UserKnowledgeDomainPermission,
                rollback_permission_id,
            ) is not None
            rollback_audit_count = db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.target_id == str(rollback_permission_id))
            )
            assert rollback_audit_count == 0

        first_lock_held = threading.Event()
        release_first = threading.Event()
        second_select_started = threading.Event()
        results = []
        errors = []
        result_lock = threading.Lock()
        concurrent_command = _command(
            actor_id=actor_id,
            organization_id=organization_a,
            subject_id=subject_id,
            action="lifecycle_manage",
        )

        def observe_second_select(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            normalized = statement.lower()
            if (
                threading.current_thread().name == "mba241-revoke-second"
                and "user_knowledge_domain_permissions" in normalized
                and "for update" in normalized
            ):
                second_select_started.set()

        event.listen(engine, "before_cursor_execute", observe_second_select)

        def revoke_in_thread(*, hold_first):
            db = session_factory()
            try:
                audit = (
                    _HoldingAudit(db, first_lock_held, release_first)
                    if hold_first
                    else SqlAlchemyKnowledgeDomainPermissionAudit(db)
                )
                result = _use_case(db, audit=audit).revoke(concurrent_command)
                with result_lock:
                    results.append(result.status)
            except Exception as exc:
                with result_lock:
                    errors.append(type(exc).__name__)
            finally:
                db.close()

        first = threading.Thread(
            target=revoke_in_thread,
            kwargs={"hold_first": True},
            name="mba241-revoke-first",
        )
        second = threading.Thread(
            target=revoke_in_thread,
            kwargs={"hold_first": False},
            name="mba241-revoke-second",
        )
        first.start()
        assert first_lock_held.wait(timeout=10)
        second.start()
        assert second_select_started.wait(timeout=10)
        assert second.is_alive()
        release_first.set()
        first.join(timeout=10)
        second.join(timeout=10)

        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        assert sorted(results) == ["deleted", "unchanged"]

        with session_factory() as db:
            permission_count = db.scalar(
                select(func.count())
                .select_from(UserKnowledgeDomainPermission)
                .where(UserKnowledgeDomainPermission.id == concurrent_permission_id)
            )
            audit_count = db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action
                    == "user_knowledge_domain_permission.deleted",
                    AuditLog.target_id == str(concurrent_permission_id),
                )
            )
            assert permission_count == 0
            assert audit_count == 1
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable or rejected the connection; "
            "connection details omitted",
            pytrace=False,
        ) from None
    finally:
        if release_first is not None:
            release_first.set()
        for worker in (first, second):
            if worker is not None and worker.is_alive():
                worker.join(timeout=10)
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
