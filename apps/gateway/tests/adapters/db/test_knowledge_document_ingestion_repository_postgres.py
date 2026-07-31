"""Opt-in PostgreSQL evidence for durable Knowledge ingestion jobs."""

import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from apps.gateway.adapters.db.knowledge_document_ingestion_repository import (
    SqlAlchemyDocumentIngestionRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.application.knowledge_document_ingestion.worker import (
    ExecuteDocumentIngestionJob,
)
from apps.shared.db.models.knowledge import (
    Document,
    KnowledgeBase,
    KnowledgeDocumentIngestionJob,
    SourceType,
)
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.user import User
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)


ROOT_DIR = Path(__file__).resolve().parents[5]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mba288_ingestion"

pytestmark = pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable Knowledge ingestion tests",
)


def _run_migrations(database: str, config: DisposablePostgresConfig) -> None:
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
        pytest.fail(
            "disposable PostgreSQL migration failed; output omitted to avoid "
            "leaking local configuration"
        )


@pytest.fixture(scope="module")
def postgres_engine():
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
    created = False
    engine = None
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {quoted_database}"))
        created = True
        extension_engine = create_engine(
            config.database_url(database),
            isolation_level="AUTOCOMMIT",
        )
        try:
            with extension_engine.connect() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        finally:
            extension_engine.dispose()
        _run_migrations(database, config)
        engine = create_engine(config.database_url(database), pool_pre_ping=True)
        yield engine
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable or rejected the connection; "
            "connection details omitted",
            pytrace=False,
        ) from None
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            try:
                with admin_engine.connect() as connection:
                    connection.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :database AND pid <> pg_backend_pid()"
                        ),
                        {"database": database},
                    )
                    connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup failed; connection details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()


def _seed_scope(engine):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    document_id = uuid.uuid4()
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        session.add(
            User(
                id=user_id,
                email=f"mba288-{user_id}@example.invalid",
                name="MBA-288 worker",
                social_provider="local",
            )
        )
        session.flush()
        session.add(
            Organization(
                id=organization_id,
                name="MBA-288 organization",
                created_by=user_id,
                managed_by=user_id,
                options={},
                flags=0,
            )
        )
        session.flush()
        session.add(
            KnowledgeBase(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
                name="MBA-288 KB",
                embedding_model="text-embedding-3-small",
                sync_state="manual",
                lifecycle_state="active",
            )
        )
        session.flush()
        session.add(
            Document(
                id=document_id,
                knowledge_base_id=knowledge_base_id,
                filename="fixture.txt",
                source_type=SourceType.FILE,
                status="indexing",
                chunk_size=500,
                chunk_overlap=50,
                meta_info={},
            )
        )
        session.commit()
    finally:
        session.close()
    return user_id, organization_id, knowledge_base_id, document_id


def _job(scope, *, status="pending", expired=False):
    user_id, organization_id, knowledge_base_id, document_id = scope
    now = datetime.now(timezone.utc)
    running = status == "running"
    return KnowledgeDocumentIngestionJob(
        id=uuid.uuid4(),
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        requested_by_user_id=user_id,
        operation_kind="process",
        generation=1,
        input_revision=uuid.uuid4().hex + uuid.uuid4().hex,
        idempotency_key=uuid.uuid4().hex + uuid.uuid4().hex,
        status=status,
        attempt_count=1 if running else 0,
        max_attempts=3,
        retryable=True,
        owner_token="owner" if running else None,
        fencing_token="fence" if running else None,
        heartbeat_at=now - timedelta(minutes=20) if running else None,
        lease_expires_at=(now - timedelta(seconds=1))
        if running and expired
        else (now + timedelta(minutes=10) if running else None),
        next_retry_at=now if status in {"pending", "retry_scheduled"} else None,
        requested_at=now,
        updated_at=now,
        safe_metadata={},
    )


def test_postgres_enforces_one_active_job_per_document(postgres_engine) -> None:
    scope = _seed_scope(postgres_engine)
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    first = session_factory()
    second = session_factory()
    try:
        first.add(_job(scope))
        first.commit()
        second.add(_job(scope, status="retry_scheduled"))
        with pytest.raises(IntegrityError):
            second.commit()
        second.rollback()
    finally:
        first.close()
        second.close()


def test_postgres_concurrent_claim_enters_runner_once(postgres_engine) -> None:
    scope = _seed_scope(postgres_engine)
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    seed = session_factory()
    job = _job(scope)
    seed.add(job)
    seed.commit()
    job_id = job.id
    seed.close()

    runner_started = threading.Event()
    release_runner = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    class Authorization:
        def is_allowed(self, _job_value):
            return True

    class Runner:
        def run(self, _job_value):
            nonlocal calls
            with calls_lock:
                calls += 1
            runner_started.set()
            assert release_runner.wait(timeout=5)
            return None

    runner = Runner()

    def execute():
        db = session_factory()
        try:
            return ExecuteDocumentIngestionJob(
                repository=SqlAlchemyDocumentIngestionRepository(db),
                authorization=Authorization(),
                runner=runner,
                unit_of_work=SqlAlchemyUnitOfWork(db),
            ).execute(job_id, owner_token=str(uuid.uuid4()))
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(execute)
        assert runner_started.wait(timeout=5)
        second = executor.submit(execute)
        second_result = second.result(timeout=5)
        release_runner.set()
        first_result = first.result(timeout=5)

    assert {first_result.status, second_result.status} == {"succeeded", "duplicate"}
    assert calls == 1


def test_postgres_heartbeat_requires_current_owner_and_fence(postgres_engine) -> None:
    scope = _seed_scope(postgres_engine)
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    seed = session_factory()
    job = _job(scope, status="running")
    seed.add(job)
    seed.commit()
    job_id = job.id
    seed.close()

    db = session_factory()
    try:
        repository = SqlAlchemyDocumentIngestionRepository(db)
        now = repository.database_now()
        assert repository.heartbeat(
            job_id,
            owner_token="wrong",
            fencing_token="fence",
            now=now,
            lease_expires_at=now + timedelta(minutes=15),
        ) is False
        db.rollback()
        assert repository.heartbeat(
            job_id,
            owner_token="owner",
            fencing_token="fence",
            now=now,
            lease_expires_at=now + timedelta(minutes=15),
        ) is True
        db.commit()
    finally:
        db.close()


def test_postgres_recovery_requeues_expired_lease(postgres_engine) -> None:
    scope = _seed_scope(postgres_engine)
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    seed = session_factory()
    job = _job(scope, status="running", expired=True)
    seed.add(job)
    seed.commit()
    job_id = job.id
    seed.close()

    db = session_factory()
    try:
        repository = SqlAlchemyDocumentIngestionRepository(db)
        recovered = repository.recover_due(now=repository.database_now(), limit=10)
        db.commit()
        row = db.get(KnowledgeDocumentIngestionJob, job_id)
        assert job_id in [recovery.job_id for recovery in recovered]
        assert row is not None
        assert row.status == "retry_scheduled"
        assert row.owner_token is None
        assert row.fencing_token is None
        assert row.dispatch_lease_expires_at is not None

        immediate = repository.recover_due(
            now=repository.database_now(),
            limit=10,
        )
        assert immediate == []
    finally:
        db.close()


def test_postgres_expired_lease_cannot_heartbeat_or_finalize(
    postgres_engine,
) -> None:
    scope = _seed_scope(postgres_engine)
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    seed = session_factory()
    job = _job(scope, status="running", expired=True)
    seed.add(job)
    seed.commit()
    job_id = job.id
    seed.close()

    db = session_factory()
    try:
        repository = SqlAlchemyDocumentIngestionRepository(db)
        now = repository.database_now()
        assert repository.heartbeat(
            job_id,
            owner_token="owner",
            fencing_token="fence",
            now=now,
            lease_expires_at=now + timedelta(minutes=15),
        ) is False
        db.rollback()
        assert repository.mark_succeeded(
            job_id,
            owner_token="owner",
            fencing_token="fence",
            result_document_version_id=None,
            now=repository.database_now(),
        ) is False
        db.rollback()
        assert repository.lock_owned_worker_job(
            job_id,
            owner_token="owner",
            fencing_token="fence",
        ) is None
        db.rollback()
    finally:
        db.close()


def test_postgres_long_transaction_uses_wall_clock_for_lease_finalization(
    postgres_engine,
) -> None:
    scope = _seed_scope(postgres_engine)
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    seed = session_factory()
    job = _job(scope, status="running")
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(milliseconds=500)
    seed.add(job)
    seed.commit()
    job_id = job.id
    seed.close()

    db = session_factory()
    try:
        transaction_now = db.query(func.now()).scalar()
        assert transaction_now is not None
        time.sleep(0.75)
        assert db.query(func.now()).scalar() == transaction_now
        wall_clock_now = db.query(func.clock_timestamp()).scalar()
        assert wall_clock_now is not None
        repository = SqlAlchemyDocumentIngestionRepository(db)
        assert (
            repository.is_owned_worker_job_current(
                job_id,
                owner_token="owner",
                fencing_token="fence",
            )
            is False
        )
        assert (
            repository.mark_succeeded(
                job_id,
                owner_token="owner",
                fencing_token="fence",
                result_document_version_id=None,
                now=wall_clock_now,
            )
            is False
        )
        db.rollback()
    finally:
        db.close()


def test_postgres_recovery_skips_locked_scope_and_processes_later_job(
    postgres_engine,
) -> None:
    locked_scope = _seed_scope(postgres_engine)
    ready_scope = _seed_scope(postgres_engine)
    _, _, locked_knowledge_base_id, locked_document_id = locked_scope
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    seed = session_factory()
    locked_job = _job(locked_scope, status="running", expired=True)
    ready_job = _job(ready_scope)
    locked_job.requested_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    seed.add_all([locked_job, ready_job])
    seed.commit()
    locked_job_id = locked_job.id
    ready_job_id = ready_job.id
    seed.close()

    locker = session_factory()
    locked_knowledge_base = locker.query(KnowledgeBase).filter(
        KnowledgeBase.id == locked_knowledge_base_id
    ).with_for_update().one()
    locked_document = locker.query(Document).filter(
        Document.id == locked_document_id
    ).with_for_update().one()
    assert locked_knowledge_base.id == locked_knowledge_base_id
    assert locked_document.id == locked_document_id

    probe = session_factory()
    try:
        with pytest.raises(OperationalError):
            probe.query(KnowledgeBase).filter(
                KnowledgeBase.id == locked_knowledge_base_id
            ).with_for_update(nowait=True).one()
        probe.rollback()
    finally:
        probe.close()

    def recover_due_jobs():
        db = session_factory()
        try:
            repository = SqlAlchemyDocumentIngestionRepository(db)
            recovered = repository.recover_due(
                now=repository.database_now(),
                limit=10,
            )
            db.commit()
            return recovered
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            recovered = executor.submit(recover_due_jobs).result(timeout=3)
        recovered_ids = [item.job_id for item in recovered]
        assert ready_job_id in recovered_ids
        assert locked_job_id not in recovered_ids
    finally:
        locker.rollback()
        locker.close()

    recovered_after_unlock = recover_due_jobs()
    assert locked_job_id in [item.job_id for item in recovered_after_unlock]


def test_postgres_failure_transition_uses_scope_before_job_lock_order(
    postgres_engine,
) -> None:
    scope = _seed_scope(postgres_engine)
    _, organization_id, knowledge_base_id, document_id = scope
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    seed = session_factory()
    job = _job(scope, status="running")
    seed.add(job)
    seed.commit()
    job_id = job.id
    seed.close()

    scope_locked = threading.Event()
    allow_admission_job_lock = threading.Event()

    def admission_lock_then_job() -> None:
        db = session_factory()
        try:
            repository = SqlAlchemyDocumentIngestionRepository(db)
            assert repository.lock_document_scope(
                organization_id,
                knowledge_base_id,
                document_id,
            ) is not None
            scope_locked.set()
            assert allow_admission_job_lock.wait(timeout=5)
            assert repository.find_active_job(document_id) is not None
            db.commit()
        finally:
            db.close()

    def transition_failure() -> None:
        assert scope_locked.wait(timeout=5)
        db = session_factory()
        try:
            repository = SqlAlchemyDocumentIngestionRepository(db)
            locked = repository.lock_owned_worker_job(
                job_id,
                owner_token="owner",
                fencing_token="fence",
            )
            assert locked is not None
            now = repository.database_now()
            repository.mark_retry_scheduled(
                locked,
                now=now,
                next_retry_at=now + timedelta(minutes=1),
                reason_code="ingestion.source_temporarily_unavailable",
            )
            db.commit()
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        admission = executor.submit(admission_lock_then_job)
        assert scope_locked.wait(timeout=5)
        failure = executor.submit(transition_failure)
        threading.Event().wait(0.2)
        allow_admission_job_lock.set()
        admission.result(timeout=5)
        failure.result(timeout=5)

    verify = session_factory()
    try:
        persisted = verify.get(KnowledgeDocumentIngestionJob, job_id)
        assert persisted is not None
        assert persisted.status == "retry_scheduled"
    finally:
        verify.close()


def test_postgres_worker_claim_uses_scope_before_job_lock_order(
    postgres_engine,
) -> None:
    scope = _seed_scope(postgres_engine)
    _, organization_id, knowledge_base_id, document_id = scope
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    seed = session_factory()
    job = _job(scope)
    seed.add(job)
    seed.commit()
    job_id = job.id
    seed.close()

    scope_locked = threading.Event()
    allow_admission_job_lock = threading.Event()

    def admission_lock_then_job() -> None:
        db = session_factory()
        try:
            repository = SqlAlchemyDocumentIngestionRepository(db)
            assert repository.lock_document_scope(
                organization_id,
                knowledge_base_id,
                document_id,
            ) is not None
            scope_locked.set()
            assert allow_admission_job_lock.wait(timeout=5)
            assert repository.find_active_job(document_id) is not None
            db.commit()
        finally:
            db.close()

    def claim_worker_job() -> None:
        assert scope_locked.wait(timeout=5)
        db = session_factory()
        try:
            repository = SqlAlchemyDocumentIngestionRepository(db)
            assert repository.lock_worker_job(job_id) is not None
            db.commit()
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        admission = executor.submit(admission_lock_then_job)
        assert scope_locked.wait(timeout=5)
        claim = executor.submit(claim_worker_job)
        threading.Event().wait(0.2)
        allow_admission_job_lock.set()
        admission.result(timeout=5)
        claim.result(timeout=5)
