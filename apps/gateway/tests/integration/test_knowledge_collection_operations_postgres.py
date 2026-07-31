import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from apps.gateway.adapters.audit.knowledge_collection_operations import (
    SqlAlchemyKnowledgeCollectionOperationAudit,
)
from apps.gateway.adapters.db.knowledge_collection_operations import (
    SqlAlchemyKnowledgeCollectionOperationRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.application.knowledge_administration.collection_operations import (
    CollectionItemRank,
    CollectionLifecycleAndOrderUseCase,
    CollectionOperationCommand,
    CollectionPersistenceFailed,
    CollectionStateConflict,
    ReorderCollectionItemsCommand,
    compute_order_revision,
)
from apps.gateway.services.knowledge_collection_service import (
    KnowledgeCollectionService,
    KnowledgeCollectionServiceError,
)
from apps.gateway.services.knowledge_candidate_resolver import (
    DEFAULT_MAX_CANDIDATE_KBS,
    KnowledgeCandidateResolver,
)
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.knowledge import (
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
)
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.team import (
    Team,
    UserKnowledgeCollectionPermission,
)
from apps.shared.db.models.user import User
from apps.shared.schemas.knowledge import (
    KnowledgeCollectionPermissionBulkBundleRequest,
)
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)


ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mba264_collection_ops"

pytestmark = pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL collection integration",
)


class _AllowAll:
    def is_organization_manager(self, _actor_id, _organization_id):
        return True

    def has_domain_action(self, _actor_id, _organization_id, _action):
        return False

    def has_collection_action(
        self,
        _actor_id,
        _organization_id,
        _collection_id,
        _action,
    ):
        return False


class _FailingAudit:
    def record(self, **_kwargs):
        raise RuntimeError("simulated audit failure")


class _HoldingAudit:
    def __init__(self, db, lock_held, release_first):
        self.delegate = SqlAlchemyKnowledgeCollectionOperationAudit(db)
        self.lock_held = lock_held
        self.release_first = release_first

    def record(self, **kwargs):
        self.delegate.record(**kwargs)
        self.lock_held.set()
        if not self.release_first.wait(timeout=10):
            raise RuntimeError("concurrent collection operation release timed out")


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


@pytest.fixture(scope="module")
def postgres():
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
        yield engine, sessionmaker(bind=engine, expire_on_commit=False)
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


def _seed_users_and_organizations(db, prefix):
    actor_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()
    db.add_all(
        [
            User(
                id=actor_id,
                email=f"{prefix}-actor-{actor_id}@example.invalid",
                name=f"{prefix} actor",
                social_provider="local",
            ),
            User(
                id=subject_id,
                email=f"{prefix}-subject-{subject_id}@example.invalid",
                name=f"{prefix} subject",
                social_provider="local",
            ),
        ]
    )
    db.flush()
    db.add_all(
        [
            Organization(
                id=organization_a,
                name=f"{prefix} organization A {organization_a}",
                created_by=actor_id,
            ),
            Organization(
                id=organization_b,
                name=f"{prefix} organization B {organization_b}",
                created_by=actor_id,
            ),
        ]
    )
    db.flush()
    return actor_id, subject_id, organization_a, organization_b


def _use_case(db, *, audit=None):
    return CollectionLifecycleAndOrderUseCase(
        _AllowAll(),
        SqlAlchemyKnowledgeCollectionOperationRepository(db),
        audit or SqlAlchemyKnowledgeCollectionOperationAudit(db),
        SqlAlchemyUnitOfWork(db),
    )


def _collection_command(actor_id, organization_id, collection_id):
    return CollectionOperationCommand(
        actor_id=actor_id,
        organization_id=organization_id,
        collection_id=collection_id,
    )


def _observe_second_collection_lock(engine, thread_name, started):
    def observer(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        normalized = statement.lower()
        if (
            threading.current_thread().name == thread_name
            and "knowledge_collections" in normalized
            and "for update" in normalized
        ):
            started.set()

    event.listen(engine, "before_cursor_execute", observer)
    return observer


def test_collection_restore_is_scoped_atomic_and_serialized(postgres):
    engine, session_factory = postgres
    with session_factory() as db:
        actor_id, _, organization_a, organization_b = _seed_users_and_organizations(
            db, "mba264-restore"
        )
        rollback_collection_id = uuid.uuid4()
        concurrent_collection_id = uuid.uuid4()
        db.add_all(
            [
                KnowledgeCollection(
                    id=rollback_collection_id,
                    organization_id=organization_a,
                    name="MBA-264 restore rollback",
                    created_by=actor_id,
                    lifecycle_state="archived",
                ),
                KnowledgeCollection(
                    id=concurrent_collection_id,
                    organization_id=organization_a,
                    name="MBA-264 restore concurrent",
                    created_by=actor_id,
                    lifecycle_state="archived",
                ),
            ]
        )
        db.commit()

    with session_factory() as db:
        repository = SqlAlchemyKnowledgeCollectionOperationRepository(db)
        assert repository.lock_collection(organization_b, rollback_collection_id) is None
        db.rollback()

    with session_factory() as db:
        with pytest.raises(CollectionPersistenceFailed):
            _use_case(db, audit=_FailingAudit()).restore(
                _collection_command(
                    actor_id,
                    organization_a,
                    rollback_collection_id,
                )
            )

    with session_factory() as db:
        assert (
            db.get(KnowledgeCollection, rollback_collection_id).lifecycle_state
            == "archived"
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "knowledge.collection.restored",
                    AuditLog.target_id == str(rollback_collection_id),
                )
            )
            == 0
        )

    first_lock_held = threading.Event()
    release_first = threading.Event()
    second_select_started = threading.Event()
    results = []
    errors = []
    result_lock = threading.Lock()
    command = _collection_command(
        actor_id,
        organization_a,
        concurrent_collection_id,
    )
    observer = _observe_second_collection_lock(
        engine,
        "mba264-restore-second",
        second_select_started,
    )

    def restore_in_thread(*, hold_first):
        db = session_factory()
        try:
            audit = (
                _HoldingAudit(db, first_lock_held, release_first)
                if hold_first
                else SqlAlchemyKnowledgeCollectionOperationAudit(db)
            )
            result = _use_case(db, audit=audit).restore(command)
            with result_lock:
                results.append(result.status)
        except Exception as exc:
            with result_lock:
                errors.append(type(exc).__name__)
        finally:
            db.close()

    first = threading.Thread(
        target=restore_in_thread,
        kwargs={"hold_first": True},
        name="mba264-restore-first",
    )
    second = threading.Thread(
        target=restore_in_thread,
        kwargs={"hold_first": False},
        name="mba264-restore-second",
    )
    try:
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
    finally:
        release_first.set()
        for worker in (first, second):
            if worker.is_alive():
                worker.join(timeout=10)
        event.remove(engine, "before_cursor_execute", observer)

    assert errors == []
    assert sorted(results) == ["changed", "unchanged"]
    with session_factory() as db:
        assert (
            db.get(KnowledgeCollection, concurrent_collection_id).lifecycle_state
            == "active"
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "knowledge.collection.restored",
                    AuditLog.target_id == str(concurrent_collection_id),
                )
            )
            == 1
        )


def test_collection_reorder_is_atomic_and_stale_safe(postgres):
    engine, session_factory = postgres
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        actor_id, _, organization_a, _ = _seed_users_and_organizations(
            db, "mba264-reorder"
        )
        collection_id = uuid.uuid4()
        kb_ids = [uuid.uuid4(), uuid.uuid4()]
        item_ids = [uuid.uuid4(), uuid.uuid4()]
        db.add(
            KnowledgeCollection(
                id=collection_id,
                organization_id=organization_a,
                name="MBA-264 reorder",
                created_by=actor_id,
            )
        )
        db.add_all(
            [
                KnowledgeBase(
                    id=kb_id,
                    organization_id=organization_a,
                    user_id=actor_id,
                    name=f"MBA-264 reorder KB {index}",
                )
                for index, kb_id in enumerate(kb_ids)
            ]
        )
        db.flush()
        db.add_all(
            [
                KnowledgeCollectionItem(
                    id=item_ids[index],
                    organization_id=organization_a,
                    collection_id=collection_id,
                    knowledge_base_id=kb_ids[index],
                    rank=index,
                    created_at=now + timedelta(seconds=index),
                )
                for index in range(2)
            ]
        )
        db.commit()

    with session_factory() as db:
        repository = SqlAlchemyKnowledgeCollectionOperationRepository(db)
        assert repository.lock_collection(organization_a, collection_id) is not None
        current = repository.lock_item_order(organization_a, collection_id)
        revision = compute_order_revision(collection_id, current)
        db.rollback()

    command = ReorderCollectionItemsCommand(
        actor_id=actor_id,
        organization_id=organization_a,
        collection_id=collection_id,
        expected_order_revision=revision,
        items=(
            CollectionItemRank(item_ids[1], 0),
            CollectionItemRank(item_ids[0], 1),
        ),
    )
    with session_factory() as db:
        with pytest.raises(CollectionPersistenceFailed):
            _use_case(db, audit=_FailingAudit()).reorder(command)

    with session_factory() as db:
        rows = db.scalars(
            select(KnowledgeCollectionItem)
            .where(KnowledgeCollectionItem.collection_id == collection_id)
            .order_by(KnowledgeCollectionItem.rank.asc())
        ).all()
        assert [(row.id, row.rank) for row in rows] == [
            (item_ids[0], 0),
            (item_ids[1], 1),
        ]
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "knowledge.collection.items.reordered",
                    AuditLog.target_id == str(collection_id),
                )
            )
            == 0
        )

    first_lock_held = threading.Event()
    release_first = threading.Event()
    second_select_started = threading.Event()
    results = []
    result_lock = threading.Lock()
    observer = _observe_second_collection_lock(
        engine,
        "mba264-reorder-second",
        second_select_started,
    )

    def reorder_in_thread(*, hold_first):
        db = session_factory()
        try:
            audit = (
                _HoldingAudit(db, first_lock_held, release_first)
                if hold_first
                else SqlAlchemyKnowledgeCollectionOperationAudit(db)
            )
            result = _use_case(db, audit=audit).reorder(command)
            outcome = result.status
        except CollectionStateConflict as exc:
            outcome = exc.reason_code
        except Exception as exc:
            outcome = type(exc).__name__
        finally:
            db.close()
        with result_lock:
            results.append(outcome)

    first = threading.Thread(
        target=reorder_in_thread,
        kwargs={"hold_first": True},
        name="mba264-reorder-first",
    )
    second = threading.Thread(
        target=reorder_in_thread,
        kwargs={"hold_first": False},
        name="mba264-reorder-second",
    )
    try:
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
    finally:
        release_first.set()
        for worker in (first, second):
            if worker.is_alive():
                worker.join(timeout=10)
        event.remove(engine, "before_cursor_execute", observer)

    assert sorted(results) == ["changed", "collection_order_stale"]
    with session_factory() as db:
        rows = db.scalars(
            select(KnowledgeCollectionItem)
            .where(KnowledgeCollectionItem.collection_id == collection_id)
            .order_by(KnowledgeCollectionItem.rank.asc())
        ).all()
        assert [(row.id, row.rank) for row in rows] == [
            (item_ids[1], 0),
            (item_ids[0], 1),
        ]
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "knowledge.collection.items.reordered",
                    AuditLog.target_id == str(collection_id),
                )
            )
            == 1
        )


def test_collection_bulk_bundle_is_all_or_nothing_and_deadlock_safe(postgres):
    engine, session_factory = postgres
    with session_factory() as db:
        actor_id, subject_id, organization_a, organization_b = (
            _seed_users_and_organizations(db, "mba264-bulk")
        )
        collection_a_ids = [uuid.uuid4(), uuid.uuid4()]
        collection_b_id = uuid.uuid4()
        db.add_all(
            [
                OrganizationMembership(
                    organization_id=organization_a,
                    user_id=actor_id,
                    membership_state="active",
                    organization_auth_state="member",
                ),
                OrganizationMembership(
                    organization_id=organization_a,
                    user_id=subject_id,
                    membership_state="active",
                    organization_auth_state="member",
                ),
                KnowledgeCollection(
                    id=collection_a_ids[0],
                    organization_id=organization_a,
                    name="MBA-264 bulk A1",
                    created_by=actor_id,
                ),
                KnowledgeCollection(
                    id=collection_a_ids[1],
                    organization_id=organization_a,
                    name="MBA-264 bulk A2",
                    created_by=actor_id,
                ),
                KnowledgeCollection(
                    id=collection_b_id,
                    organization_id=organization_b,
                    name="MBA-264 bulk B",
                    created_by=actor_id,
                ),
            ]
        )
        db.flush()
        actor_manage_permission_id = uuid.uuid4()
        db.add(
            UserKnowledgeCollectionPermission(
                id=actor_manage_permission_id,
                grantee_organization_id=organization_a,
                user_id=actor_id,
                assigned_by=actor_id,
                knowledge_collection_id=collection_a_ids[0],
                permission_action="manage",
            )
        )
        db.commit()

    def service(db, *, manager=False):
        instance = KnowledgeCollectionService(
            db,
            user_id=actor_id,
            organization_id=organization_a,
        )
        if manager:
            instance._is_org_manager = lambda: True
        return instance

    cross_org_request = KnowledgeCollectionPermissionBulkBundleRequest(
        collection_ids=[collection_a_ids[0], collection_b_id],
        operation="grant",
        subject_type="user",
        subject_id=subject_id,
        role_bundle="viewer",
    )
    with session_factory() as db:
        with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
            service(db, manager=True).mutate_permission_bundle_bulk(
                cross_org_request
            )
        assert exc_info.value.status_code == 404

    unauthorized_request = KnowledgeCollectionPermissionBulkBundleRequest(
        collection_ids=collection_a_ids,
        operation="grant",
        subject_type="user",
        subject_id=subject_id,
        role_bundle="viewer",
    )
    with session_factory() as db:
        with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
            service(db).mutate_permission_bundle_bulk(unauthorized_request)
        assert exc_info.value.status_code == 403

    self_revoke_request = KnowledgeCollectionPermissionBulkBundleRequest(
        collection_ids=[collection_a_ids[0]],
        operation="revoke",
        subject_type="user",
        subject_id=actor_id,
        role_bundle="maintainer",
    )
    with session_factory() as db:
        with pytest.raises(KnowledgeCollectionServiceError) as exc_info:
            service(db).mutate_permission_bundle_bulk(self_revoke_request)
        assert exc_info.value.status_code == 403

    rollback_request = KnowledgeCollectionPermissionBulkBundleRequest(
        collection_ids=[collection_a_ids[0]],
        operation="grant",
        subject_type="user",
        subject_id=subject_id,
        role_bundle="viewer",
    )
    with session_factory() as db:
        instance = service(db, manager=True)
        instance._record_collection_audit = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated audit failure")
        )
        with pytest.raises(RuntimeError, match="^simulated audit failure$"):
            instance.mutate_permission_bundle_bulk(rollback_request)

    with session_factory() as db:
        assert db.get(UserKnowledgeCollectionPermission, actor_manage_permission_id)
        assert (
            db.scalar(
                select(func.count())
                .select_from(UserKnowledgeCollectionPermission)
                .where(
                    UserKnowledgeCollectionPermission.user_id == subject_id,
                    UserKnowledgeCollectionPermission.grantee_organization_id
                    == organization_a,
                )
            )
            == 0
        )

    first_lock_held = threading.Event()
    release_first = threading.Event()
    second_select_started = threading.Event()
    results = []
    errors = []
    result_lock = threading.Lock()
    observer = _observe_second_collection_lock(
        engine,
        "mba264-bulk-second",
        second_select_started,
    )

    def bulk_in_thread(collection_ids, *, hold_first):
        db = session_factory()
        try:
            instance = service(db, manager=True)
            if hold_first:
                original_record = instance._record_collection_audit
                held = False

                def holding_record(*args, **kwargs):
                    nonlocal held
                    original_record(*args, **kwargs)
                    if not held:
                        held = True
                        first_lock_held.set()
                        if not release_first.wait(timeout=10):
                            raise RuntimeError("concurrent bulk release timed out")

                instance._record_collection_audit = holding_record
            response = instance.mutate_permission_bundle_bulk(
                KnowledgeCollectionPermissionBulkBundleRequest(
                    collection_ids=collection_ids,
                    operation="grant",
                    subject_type="user",
                    subject_id=subject_id,
                    role_bundle="workflow_router",
                )
            )
            with result_lock:
                results.append(
                    (response.changed_count_bucket, response.unchanged_count_bucket)
                )
        except Exception as exc:
            with result_lock:
                errors.append(type(exc).__name__)
        finally:
            db.close()

    first = threading.Thread(
        target=bulk_in_thread,
        args=(list(reversed(collection_a_ids)),),
        kwargs={"hold_first": True},
        name="mba264-bulk-first",
    )
    second = threading.Thread(
        target=bulk_in_thread,
        args=(collection_a_ids,),
        kwargs={"hold_first": False},
        name="mba264-bulk-second",
    )
    try:
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
    finally:
        release_first.set()
        for worker in (first, second):
            if worker.is_alive():
                worker.join(timeout=10)
        event.remove(engine, "before_cursor_execute", observer)

    assert errors == []
    assert sorted(results) == [("0", "2-10"), ("2-10", "0")]
    with session_factory() as db:
        permission_rows = db.scalars(
            select(UserKnowledgeCollectionPermission).where(
                UserKnowledgeCollectionPermission.user_id == subject_id,
                UserKnowledgeCollectionPermission.grantee_organization_id
                == organization_a,
            )
        ).all()
        assert {
            (row.knowledge_collection_id, row.permission_action)
            for row in permission_rows
        } == {
            (collection_id, action)
            for collection_id in collection_a_ids
            for action in ("read", "route")
        }
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action
                    == "knowledge.collection.permission_bundle.granted",
                    AuditLog.target_id.in_(
                        [str(collection_id) for collection_id in collection_a_ids]
                    ),
                )
            )
            == 2
        )


def test_delegation_subject_page_is_bounded_and_organization_scoped(postgres):
    _, session_factory = postgres
    with session_factory() as db:
        actor_id, subject_id, organization_a, organization_b = (
            _seed_users_and_organizations(db, "mba264-subjects")
        )
        db.get(User, subject_id).name = "Alpha first user"
        second_subject_id = uuid.uuid4()
        removed_subject_id = uuid.uuid4()
        cross_subject_id = uuid.uuid4()
        db.add_all(
            [
                User(
                    id=second_subject_id,
                    email=f"mba264-second-{second_subject_id}@example.invalid",
                    name="Alpha second user",
                    social_provider="local",
                ),
                User(
                    id=removed_subject_id,
                    email=f"mba264-removed-{removed_subject_id}@example.invalid",
                    name="Alpha removed user",
                    social_provider="local",
                ),
                User(
                    id=cross_subject_id,
                    email=f"mba264-cross-{cross_subject_id}@example.invalid",
                    name="Alpha cross user",
                    social_provider="local",
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                OrganizationMembership(
                    organization_id=organization_a,
                    user_id=actor_id,
                    membership_state="active",
                    organization_auth_state="manager",
                ),
                OrganizationMembership(
                    organization_id=organization_a,
                    user_id=subject_id,
                    membership_state="active",
                    organization_auth_state="member",
                ),
                OrganizationMembership(
                    organization_id=organization_a,
                    user_id=second_subject_id,
                    membership_state="active",
                    organization_auth_state="member",
                ),
                OrganizationMembership(
                    organization_id=organization_a,
                    user_id=removed_subject_id,
                    membership_state="removed",
                    organization_auth_state="member",
                ),
                OrganizationMembership(
                    organization_id=organization_b,
                    user_id=cross_subject_id,
                    membership_state="active",
                    organization_auth_state="member",
                ),
                Team(
                    organization_id=organization_a,
                    name="A% literal team",
                    created_by=actor_id,
                    is_active=True,
                ),
                Team(
                    organization_id=organization_a,
                    name="Ax wildcard decoy",
                    created_by=actor_id,
                    is_active=True,
                ),
                Team(
                    organization_id=organization_a,
                    name="A% inactive team",
                    created_by=actor_id,
                    is_active=False,
                ),
                Team(
                    organization_id=organization_b,
                    name="A% cross team",
                    created_by=actor_id,
                    is_active=True,
                ),
            ]
        )
        db.commit()

    with session_factory() as db:
        service = KnowledgeCollectionService(
            db,
            user_id=actor_id,
            organization_id=organization_a,
        )
        team_page = service.list_domain_delegation_subjects(
            subject_type="team",
            query="A%",
            limit=1,
        )
        assert [row.subject_safe_label for row in team_page.subjects] == [
            "A% literal team"
        ]
        assert team_page.next_cursor is None

        first_user_page = service.list_domain_delegation_subjects(
            subject_type="user",
            query="Alpha",
            limit=1,
        )
        assert len(first_user_page.subjects) == 1
        assert first_user_page.next_cursor is not None
        second_user_page = service.list_domain_delegation_subjects(
            subject_type="user",
            query="Alpha",
            cursor=first_user_page.next_cursor,
            limit=1,
        )
        user_ids = {
            first_user_page.subjects[0].subject_id,
            second_user_page.subjects[0].subject_id,
        }
        assert user_ids == {subject_id, second_subject_id}
        assert second_user_page.next_cursor is None
        assert removed_subject_id not in user_ids
        assert cross_subject_id not in user_ids
        assert "@" not in str(first_user_page.model_dump())


def test_candidate_cap_preserves_caller_collection_order(postgres):
    _, session_factory = postgres
    with session_factory() as db:
        actor_id, _, organization_id, _ = _seed_users_and_organizations(
            db, "candidate-cap-order"
        )
        first_collection_id = uuid.uuid4()
        second_collection_id = uuid.uuid4()
        first_collection_kb_ids = [
            uuid.uuid4() for _ in range(DEFAULT_MAX_CANDIDATE_KBS)
        ]
        second_collection_kb_id = uuid.uuid4()
        all_kb_ids = [*first_collection_kb_ids, second_collection_kb_id]
        db.add_all(
            [
                KnowledgeCollection(
                    id=first_collection_id,
                    organization_id=organization_id,
                    name="Caller order first",
                    created_by=actor_id,
                ),
                KnowledgeCollection(
                    id=second_collection_id,
                    organization_id=organization_id,
                    name="Caller order second",
                    created_by=actor_id,
                ),
            ]
        )
        db.add_all(
            [
                KnowledgeBase(
                    id=kb_id,
                    organization_id=organization_id,
                    user_id=actor_id,
                    name=f"Candidate cap KB {index}",
                )
                for index, kb_id in enumerate(all_kb_ids)
            ]
        )
        db.flush()
        db.add_all(
            [
                KnowledgeCollectionItem(
                    organization_id=organization_id,
                    collection_id=first_collection_id,
                    knowledge_base_id=kb_id,
                    rank=index,
                )
                for index, kb_id in enumerate(first_collection_kb_ids)
            ]
            + [
                KnowledgeCollectionItem(
                    organization_id=organization_id,
                    collection_id=second_collection_id,
                    knowledge_base_id=second_collection_kb_id,
                    rank=0,
                )
            ]
        )
        db.commit()

        resolver = KnowledgeCandidateResolver(
            db,
            user_id=actor_id,
            organization_id=organization_id,
        )
        rows = resolver._collection_items(
            [first_collection_id, second_collection_id],
            DEFAULT_MAX_CANDIDATE_KBS,
        )

        assert len(rows) == DEFAULT_MAX_CANDIDATE_KBS
        assert {row.collection_id for row in rows} == {first_collection_id}
        assert second_collection_kb_id not in {
            row.knowledge_base_id for row in rows
        }
