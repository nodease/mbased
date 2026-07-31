import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from time import monotonic

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from apps.gateway.services.connection_lifecycle_service import (
    ConnectionLifecycleBusy,
    ConnectionLifecycleConflict,
    ConnectionLifecycleHidden,
    ConnectionLifecycleInUse,
    ConnectionLifecycleService,
)
from apps.gateway.services.knowledge_document_registration_service import (
    KnowledgeDocumentRegistrationHidden,
    KnowledgeDocumentRegistrationService,
    KnowledgeDocumentSlotOccupied,
)
from apps.gateway.services.knowledge_document_lifecycle_service import (
    KnowledgeDocumentLifecycleService,
)
from apps.shared.db.models.connection import Connection
from apps.shared.db.models.knowledge import (
    Document,
    DocumentVersion,
    KnowledgeBase,
    SourceType,
)
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.user import User
from apps.shared.services.connection_runtime_snapshot import (
    ConnectionRuntimeSnapshotProvider,
)
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from apps.shared.utils.encryption import encryption_manager

ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_knowledge_registration"

pytestmark = pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL registration integration",
)


def _run_alembic(database: str, config: DisposablePostgresConfig) -> None:
    env = config.subprocess_environment(database=database, root_dir=ROOT_DIR)
    result = subprocess.run(
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
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"alembic failed with exit code {result.returncode}; "
            "stdout/stderr omitted to avoid leaking local configuration"
        )


@pytest.fixture(scope="module")
def postgres_session_factory():
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
        with admin_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE {quoted_database}"))
        database_created = True

        engine = create_engine(config.database_url(database))
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        engine.dispose()
        engine = None

        _run_alembic(database, config)
        engine = create_engine(config.database_url(database), pool_size=4)
        yield sessionmaker(bind=engine, expire_on_commit=False)
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
                with admin_engine.connect() as conn:
                    conn.execute(
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
                    conn.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup could not connect; "
                    "connection details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()


def _create_empty_knowledge_base(session_factory):
    owner_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    with session_factory() as setup_db:
        setup_db.add(
            User(
                id=owner_id,
                email=f"owner-{owner_id}@example.test",
                name="Owner",
                social_provider="local",
            )
        )
        setup_db.flush()
        setup_db.add(
            Organization(
                id=organization_id,
                name=f"Registration organization {organization_id}",
                created_by=owner_id,
            )
        )
        setup_db.flush()
        setup_db.add(
            KnowledgeBase(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=owner_id,
                name=f"Registration integration KB {knowledge_base_id}",
                sync_state="manual",
                lifecycle_state="active",
            )
        )
        setup_db.commit()
    return organization_id, knowledge_base_id


def _register_document(
    session_factory,
    *,
    organization_id,
    knowledge_base_id,
    filename,
):
    with session_factory() as db:
        return KnowledgeDocumentRegistrationService(db).register_initial_document(
            knowledge_base_id=knowledge_base_id,
            organization_id=organization_id,
            filename=filename,
            file_path=None,
            chunk_size=500,
            chunk_overlap=50,
            source_type=SourceType.FILE,
        )


def test_concurrent_initial_registration_creates_exactly_one_document(
    postgres_session_factory,
):
    organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )
    barrier = Barrier(2)

    def register(index: int) -> str:
        barrier.wait(timeout=10)
        try:
            _register_document(
                postgres_session_factory,
                organization_id=organization_id,
                knowledge_base_id=knowledge_base_id,
                filename=f"policy-{index}.txt",
            )
        except KnowledgeDocumentSlotOccupied:
            return "occupied"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(register, (1, 2)))

    assert sorted(results) == ["created", "occupied"]
    with postgres_session_factory() as verification_db:
        documents = (
            verification_db.query(Document)
            .filter(Document.knowledge_base_id == knowledge_base_id)
            .all()
        )
        assert len(documents) == 1
        assert documents[0].status == "pending"


def test_registration_refreshes_preloaded_kb_after_concurrent_policy_change(
    postgres_session_factory,
):
    organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )

    with postgres_session_factory() as stale_db:
        preloaded = stale_db.query(KnowledgeBase).filter_by(id=knowledge_base_id).one()
        assert preloaded.lifecycle_state == "active"

        with postgres_session_factory() as updater_db:
            updater_db.query(KnowledgeBase).filter_by(id=knowledge_base_id).update(
                {KnowledgeBase.lifecycle_state: "archived"},
                synchronize_session=False,
            )
            updater_db.commit()

        with pytest.raises(KnowledgeDocumentRegistrationHidden):
            KnowledgeDocumentRegistrationService(stale_db).register_initial_document(
                knowledge_base_id=knowledge_base_id,
                organization_id=organization_id,
                filename="must-not-register.txt",
                file_path=None,
                chunk_size=500,
                chunk_overlap=50,
                source_type=SourceType.FILE,
            )

    with postgres_session_factory() as verification_db:
        assert (
            verification_db.query(Document)
            .filter(Document.knowledge_base_id == knowledge_base_id)
            .count()
            == 0
        )


def test_registration_hides_knowledge_base_from_another_organization(
    postgres_session_factory,
):
    organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )
    other_organization_id = uuid.uuid4()
    with postgres_session_factory() as setup_db:
        owner_id = (
            setup_db.query(KnowledgeBase.user_id)
            .filter(KnowledgeBase.id == knowledge_base_id)
            .scalar()
        )
        setup_db.add(
            Organization(
                id=other_organization_id,
                name=f"Other registration organization {other_organization_id}",
                created_by=owner_id,
            )
        )
        setup_db.commit()

    assert other_organization_id != organization_id

    with pytest.raises(KnowledgeDocumentRegistrationHidden):
        _register_document(
            postgres_session_factory,
            organization_id=other_organization_id,
            knowledge_base_id=knowledge_base_id,
            filename="cross-organization.txt",
        )

    with postgres_session_factory() as verification_db:
        assert (
            verification_db.query(Document)
            .filter(Document.knowledge_base_id == knowledge_base_id)
            .count()
            == 0
        )


def test_registration_repairs_legacy_stale_active_pointer(
    postgres_session_factory,
):
    organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )
    with postgres_session_factory() as setup_db:
        kb = setup_db.query(KnowledgeBase).filter_by(id=knowledge_base_id).one()
        stale_version = DocumentVersion(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            legacy_document_id=None,
            version_number=1,
            status="ready",
        )
        setup_db.add(stale_version)
        setup_db.flush()
        stale_version_id = stale_version.id
        kb.active_document_version_id = stale_version_id
        setup_db.commit()

    document_id = _register_document(
        postgres_session_factory,
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        filename="replacement.txt",
    )

    with postgres_session_factory() as verification_db:
        kb = verification_db.query(KnowledgeBase).filter_by(id=knowledge_base_id).one()
        stale_version = (
            verification_db.query(DocumentVersion)
            .filter_by(id=stale_version_id)
            .one()
        )
        document = verification_db.query(Document).filter_by(id=document_id).one()
        assert kb.active_document_version_id is None
        assert stale_version.status == "superseded"
        assert stale_version.superseded_at is not None
        assert document.filename == "replacement.txt"


def test_committed_document_delete_reopens_initial_registration_slot(
    postgres_session_factory,
):
    organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )
    first_document_id = _register_document(
        postgres_session_factory,
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        filename="first.txt",
    )

    with postgres_session_factory() as version_db:
        kb = version_db.query(KnowledgeBase).filter_by(id=knowledge_base_id).one()
        active_version = DocumentVersion(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            legacy_document_id=first_document_id,
            version_number=1,
            status="ready",
        )
        version_db.add(active_version)
        version_db.flush()
        active_version_id = active_version.id
        kb.active_document_version_id = active_version_id
        version_db.commit()

    with postgres_session_factory() as delete_db:
        deleted = KnowledgeDocumentLifecycleService(delete_db).delete_document(
            knowledge_base_id=knowledge_base_id,
            organization_id=organization_id,
            document_id=first_document_id,
        )

    assert deleted.file_path is None

    with postgres_session_factory() as released_db:
        kb = released_db.query(KnowledgeBase).filter_by(id=knowledge_base_id).one()
        historical_version = (
            released_db.query(DocumentVersion).filter_by(id=active_version_id).one()
        )
        assert kb.active_document_version_id is None
        assert historical_version.status == "superseded"
        assert historical_version.superseded_at is not None
        assert historical_version.legacy_document_id is None

    replacement_document_id = _register_document(
        postgres_session_factory,
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        filename="replacement.txt",
    )

    assert replacement_document_id != first_document_id
    with postgres_session_factory() as verification_db:
        documents = (
            verification_db.query(Document)
            .filter(Document.knowledge_base_id == knowledge_base_id)
            .all()
        )
        assert [document.filename for document in documents] == ["replacement.txt"]


def test_delete_refreshes_preloaded_active_version_after_concurrent_finalization(
    postgres_session_factory,
):
    organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )
    document_id = _register_document(
        postgres_session_factory,
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        filename="finalizing.txt",
    )

    with postgres_session_factory() as stale_db:
        preloaded_kb = stale_db.query(KnowledgeBase).filter_by(id=knowledge_base_id).one()
        stale_db.query(Document).filter_by(id=document_id).one()
        assert preloaded_kb.active_document_version_id is None

        with postgres_session_factory() as finalizer_db:
            finalized_version = DocumentVersion(
                organization_id=organization_id,
                knowledge_base_id=knowledge_base_id,
                legacy_document_id=document_id,
                version_number=1,
                status="ready",
            )
            finalizer_db.add(finalized_version)
            finalizer_db.flush()
            finalized_version_id = finalized_version.id
            finalizer_db.query(KnowledgeBase).filter_by(id=knowledge_base_id).update(
                {KnowledgeBase.active_document_version_id: finalized_version_id},
                synchronize_session=False,
            )
            finalizer_db.commit()

        KnowledgeDocumentLifecycleService(stale_db).delete_document(
            knowledge_base_id=knowledge_base_id,
            organization_id=organization_id,
            document_id=document_id,
        )

    with postgres_session_factory() as verification_db:
        kb = verification_db.query(KnowledgeBase).filter_by(id=knowledge_base_id).one()
        version = (
            verification_db.query(DocumentVersion)
            .filter_by(id=finalized_version_id)
            .one()
        )
        assert kb.active_document_version_id is None
        assert version.status == "superseded"
        assert version.superseded_at is not None
        assert version.legacy_document_id is None


def test_connection_cleanup_rejects_live_document_reference_then_allows_release(
    postgres_session_factory,
):
    organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )
    connection_id = uuid.uuid4()
    with postgres_session_factory() as setup_db:
        owner_id = (
            setup_db.query(KnowledgeBase.user_id)
            .filter(KnowledgeBase.id == knowledge_base_id)
            .scalar()
        )
        setup_db.add(
            Connection(
                id=connection_id,
                user_id=owner_id,
                name="Integration DB",
                type="postgres",
                host="db.invalid",
                port=5432,
                database="integration",
                username="integration",
                encrypted_password="encrypted-placeholder",
                use_ssh=False,
            )
        )
        setup_db.commit()

    with postgres_session_factory() as register_db:
        document_id = KnowledgeDocumentRegistrationService(
            register_db
        ).register_initial_document(
            knowledge_base_id=knowledge_base_id,
            organization_id=organization_id,
            filename="Integration DB",
            file_path=None,
            chunk_size=500,
            chunk_overlap=50,
            source_type=SourceType.DB,
            meta_info={"connection_id": str(connection_id)},
        )

    with postgres_session_factory() as blocked_db:
        with pytest.raises(ConnectionLifecycleHidden):
            ConnectionLifecycleService(blocked_db).delete_unreferenced_connection(
                connection_id=connection_id,
                owner_id=uuid.uuid4(),
            )

    with postgres_session_factory() as blocked_db:
        with pytest.raises(ConnectionLifecycleInUse):
            ConnectionLifecycleService(blocked_db).delete_unreferenced_connection(
                connection_id=connection_id,
                owner_id=owner_id,
            )

    with postgres_session_factory() as nested_reference_db:
        document = nested_reference_db.query(Document).filter_by(id=document_id).one()
        document.meta_info = {
            "db_config": {"connection_id": str(connection_id)}
        }
        nested_reference_db.commit()

    with postgres_session_factory() as blocked_db:
        with pytest.raises(ConnectionLifecycleInUse):
            ConnectionLifecycleService(blocked_db).delete_unreferenced_connection(
                connection_id=connection_id,
                owner_id=owner_id,
            )

    with postgres_session_factory() as serialized_reference_db:
        document = serialized_reference_db.query(Document).filter_by(id=document_id).one()
        document.meta_info = {
            "db_config": json.dumps({"connection_id": str(connection_id)})
        }
        serialized_reference_db.commit()

    with postgres_session_factory() as blocked_db:
        with pytest.raises(ConnectionLifecycleInUse):
            ConnectionLifecycleService(blocked_db).delete_unreferenced_connection(
                connection_id=connection_id,
                owner_id=owner_id,
            )

    with postgres_session_factory() as malformed_reference_db:
        document = malformed_reference_db.query(Document).filter_by(id=document_id).one()
        document.meta_info = {
            "db_config": f'{{"connection_id":"{connection_id}"'
        }
        malformed_reference_db.commit()

    with postgres_session_factory() as blocked_db:
        with pytest.raises(ConnectionLifecycleInUse):
            ConnectionLifecycleService(blocked_db).delete_unreferenced_connection(
                connection_id=connection_id,
                owner_id=owner_id,
            )

    with postgres_session_factory() as delete_document_db:
        KnowledgeDocumentLifecycleService(delete_document_db).delete_document(
            knowledge_base_id=knowledge_base_id,
            organization_id=organization_id,
            document_id=document_id,
        )

    with postgres_session_factory() as cleanup_db:
        ConnectionLifecycleService(cleanup_db).delete_unreferenced_connection(
            connection_id=connection_id,
            owner_id=owner_id,
        )

    with postgres_session_factory() as verification_db:
        assert (
            verification_db.query(Connection).filter_by(id=connection_id).count() == 0
        )


def test_connection_reference_lock_serializes_settings_save_with_delete(
    postgres_session_factory,
):
    organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )
    connection_id = uuid.uuid4()
    with postgres_session_factory() as setup_db:
        owner_id = (
            setup_db.query(KnowledgeBase.user_id)
            .filter(KnowledgeBase.id == knowledge_base_id)
            .scalar()
        )
        setup_db.add(
            Connection(
                id=connection_id,
                user_id=owner_id,
                name="Settings DB",
                type="postgres",
                host="db.invalid",
                port=5432,
                database="settings",
                username="settings",
                encrypted_password="encrypted-placeholder",
                use_ssh=False,
            )
        )
        setup_db.commit()

    with postgres_session_factory() as register_db:
        document_id = KnowledgeDocumentRegistrationService(
            register_db
        ).register_initial_document(
            knowledge_base_id=knowledge_base_id,
            organization_id=organization_id,
            filename="Settings DB",
            file_path=None,
            chunk_size=500,
            chunk_overlap=50,
            source_type=SourceType.DB,
            meta_info={},
        )

    with postgres_session_factory() as writer_db:
        ConnectionLifecycleService(writer_db).lock_owned_connection_for_reference(
            connection_id=connection_id,
            owner_id=owner_id,
        )
        document = writer_db.query(Document).filter_by(id=document_id).one()

        with postgres_session_factory() as contender_db:
            started_at = monotonic()
            with pytest.raises(ConnectionLifecycleBusy):
                ConnectionLifecycleService(
                    contender_db
                ).delete_unreferenced_connection(
                    connection_id=connection_id,
                    owner_id=owner_id,
                )
            assert monotonic() - started_at < 5
            assert contender_db.in_transaction() is False
            assert contender_db.execute(text("SELECT 1")).scalar_one() == 1
            contender_db.rollback()

        document.meta_info = {
            "db_config": {"connection_id": str(connection_id)}
        }
        writer_db.commit()

    with postgres_session_factory() as blocked_db:
        with pytest.raises(ConnectionLifecycleInUse):
            ConnectionLifecycleService(blocked_db).delete_unreferenced_connection(
                connection_id=connection_id,
                owner_id=owner_id,
            )


def test_runtime_snapshot_releases_its_transaction_before_connection_mutation(
    postgres_session_factory,
    monkeypatch,
):
    _organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )
    connection_id = uuid.uuid4()
    with postgres_session_factory() as setup_db:
        owner_id = (
            setup_db.query(KnowledgeBase.user_id)
            .filter(KnowledgeBase.id == knowledge_base_id)
            .scalar()
        )
        setup_db.add(
            Connection(
                id=connection_id,
                user_id=owner_id,
                name="Runtime snapshot DB",
                type="postgres",
                host="db.invalid",
                port=5432,
                database="runtime",
                username="runtime",
                encrypted_password="encrypted-placeholder",
                use_ssh=False,
            )
        )
        setup_db.commit()

    monkeypatch.setattr(encryption_manager, "decrypt", lambda _value: "test-value")
    snapshot = ConnectionRuntimeSnapshotProvider(postgres_session_factory).load(
        connection_id,
        execution_subject_user_id=owner_id,
    )

    assert snapshot.adapter_type == "postgres"
    with postgres_session_factory() as mutation_db:
        locked = ConnectionLifecycleService(
            mutation_db
        ).lock_owned_connection_for_reference(
            connection_id=connection_id,
            owner_id=owner_id,
        )
        assert locked.id == connection_id
        mutation_db.rollback()


def test_connection_reference_update_rejects_stale_document_revision(
    postgres_session_factory,
):
    organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )
    connection_id = uuid.uuid4()
    with postgres_session_factory() as setup_db:
        owner_id = (
            setup_db.query(KnowledgeBase.user_id)
            .filter(KnowledgeBase.id == knowledge_base_id)
            .scalar()
        )
        setup_db.add(
            Connection(
                id=connection_id,
                user_id=owner_id,
                name="Stale reference DB",
                type="postgres",
                host="db.invalid",
                port=5432,
                database="stale-reference",
                username="stale-reference",
                encrypted_password="encrypted-placeholder",
                use_ssh=False,
            )
        )
        setup_db.commit()

    with postgres_session_factory() as register_db:
        document_id = KnowledgeDocumentRegistrationService(
            register_db
        ).register_initial_document(
            knowledge_base_id=knowledge_base_id,
            organization_id=organization_id,
            filename="Stale reference DB",
            file_path=None,
            chunk_size=500,
            chunk_overlap=50,
            source_type=SourceType.DB,
            meta_info={},
        )

    with postgres_session_factory() as stale_db:
        stale_document = stale_db.query(Document).filter_by(id=document_id).one()
        expected_updated_at = stale_document.updated_at
        assert expected_updated_at is not None

        with postgres_session_factory() as concurrent_db:
            concurrent_document = (
                concurrent_db.query(Document).filter_by(id=document_id).one()
            )
            concurrent_document.updated_at = expected_updated_at + timedelta(seconds=1)
            concurrent_document.chunk_size = 640
            concurrent_db.commit()

        with pytest.raises(ConnectionLifecycleConflict):
            ConnectionLifecycleService(
                stale_db
            ).lock_owned_connection_and_document_for_reference(
                connection_id=connection_id,
                owner_id=owner_id,
                document_id=document_id,
                expected_document_updated_at=expected_updated_at,
            )
        assert stale_db.in_transaction() is False

    with postgres_session_factory() as verification_db:
        document = verification_db.query(Document).filter_by(id=document_id).one()
        assert document.chunk_size == 640


def test_connection_deadlock_is_normalized_and_new_session_remains_usable(
    postgres_session_factory,
):
    _organization_id, knowledge_base_id = _create_empty_knowledge_base(
        postgres_session_factory
    )
    connection_ids = (uuid.uuid4(), uuid.uuid4())
    with postgres_session_factory() as setup_db:
        owner_id = (
            setup_db.query(KnowledgeBase.user_id)
            .filter(KnowledgeBase.id == knowledge_base_id)
            .scalar()
        )
        for index, connection_id in enumerate(connection_ids):
            setup_db.add(
                Connection(
                    id=connection_id,
                    user_id=owner_id,
                    name=f"Deadlock DB {index}",
                    type="postgres",
                    host="db.invalid",
                    port=5432,
                    database="deadlock",
                    username="deadlock",
                    encrypted_password="encrypted-placeholder",
                    use_ssh=False,
                )
            )
        setup_db.commit()

    barrier = Barrier(2)

    def lock_in_order(first_id, second_id) -> str:
        with postgres_session_factory() as db:
            service = ConnectionLifecycleService(db)
            service.lock_owned_connection_for_reference(
                connection_id=first_id,
                owner_id=owner_id,
            )
            barrier.wait(timeout=10)
            try:
                service.lock_owned_connection_for_reference(
                    connection_id=second_id,
                    owner_id=owner_id,
                )
                db.commit()
                return "acquired"
            except ConnectionLifecycleBusy:
                assert db.in_transaction() is False
                return "busy"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            lock_in_order,
            connection_ids[0],
            connection_ids[1],
        )
        second = executor.submit(
            lock_in_order,
            connection_ids[1],
            connection_ids[0],
        )
        outcomes = sorted((first.result(timeout=15), second.result(timeout=15)))

    assert outcomes == ["acquired", "busy"]
    with postgres_session_factory() as verification_db:
        assert verification_db.execute(text("SELECT 1")).scalar_one() == 1
