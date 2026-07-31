import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from apps.gateway.services.knowledge_lifecycle_service import KnowledgeLifecycleService
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.knowledge import Document, DocumentChunk, KnowledgeBase
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.team import (
    Team,
    TeamKnowledgePermission,
    UserKnowledgePermission,
)
from apps.shared.db.models.user import User
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)

ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_lifecycle"


def _run_alembic(database: str, config: DisposablePostgresConfig) -> None:
    env = config.subprocess_environment(
        database=database,
        root_dir=ROOT_DIR,
    )
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


class _Storage:
    def __init__(self) -> None:
        self.deleted_paths: list[str] = []

    def delete(self, file_path: str) -> None:
        self.deleted_paths.append(file_path)


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL lifecycle integration",
)
def test_hard_delete_knowledge_base_is_atomic_and_cleans_actual_fk_rows():
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
        with admin_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE {quoted_database}"))
        database_created = True

        engine = create_engine(config.database_url(database))
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        engine.dispose()
        engine = None

        _run_alembic(database, config)
        engine = create_engine(config.database_url(database))
        db = sessionmaker(bind=engine, expire_on_commit=False)()

        owner_id = uuid.uuid4()
        grantee_id = uuid.uuid4()
        owner = User(
            id=owner_id,
            email=f"owner-{owner_id}@example.test",
            name="Owner",
            social_provider="local",
        )
        grantee = User(
            id=grantee_id,
            email=f"grantee-{grantee_id}@example.test",
            name="Grantee",
            social_provider="local",
        )
        db.add_all([owner, grantee])
        db.flush()

        owner_org_id = uuid.uuid4()
        legacy_org_id = uuid.uuid4()
        db.add_all(
            [
                Organization(
                    id=owner_org_id,
                    name="Owner organization",
                    created_by=owner_id,
                ),
                Organization(
                    id=legacy_org_id,
                    name="Legacy grantee organization",
                    created_by=owner_id,
                ),
            ]
        )
        db.flush()

        legacy_team_id = uuid.uuid4()
        db.add(
            Team(
                id=legacy_team_id,
                organization_id=legacy_org_id,
                name="Legacy team",
                created_by=owner_id,
            )
        )
        db.flush()

        kb_id = uuid.uuid4()
        document_id = uuid.uuid4()
        chunk_id = uuid.uuid4()
        db.add(
            KnowledgeBase(
                id=kb_id,
                organization_id=owner_org_id,
                user_id=owner_id,
                name="Lifecycle integration KB",
            )
        )
        db.flush()
        db.add(
            Document(
                id=document_id,
                knowledge_base_id=kb_id,
                filename="policy.txt",
                file_path="private/policy.txt",
                status="completed",
            )
        )
        db.flush()
        db.add(
            DocumentChunk(
                id=chunk_id,
                document_id=document_id,
                knowledge_base_id=kb_id,
                content="Policy evidence",
                embedding=[0.1, 0.2, 0.3],
                chunk_index=0,
                token_count=2,
            )
        )
        db.add_all(
            [
                UserKnowledgePermission(
                    id=uuid.uuid4(),
                    grantee_organization_id=owner_org_id,
                    user_id=grantee_id,
                    knowledge_base_id=kb_id,
                    assigned_by=owner_id,
                    auth_state="viewer",
                ),
                TeamKnowledgePermission(
                    id=uuid.uuid4(),
                    grantee_organization_id=legacy_org_id,
                    team_id=legacy_team_id,
                    knowledge_base_id=kb_id,
                    assigned_by=owner_id,
                    auth_state="viewer",
                ),
            ]
        )
        db.commit()

        storage = _Storage()
        service = KnowledgeLifecycleService(
            db,
            storage_service_factory=lambda: storage,
            hard_delete_policy_checker=lambda _kb: True,
        )

        def fail_before_commit(_session) -> None:
            raise RuntimeError("injected commit failure")

        kb = db.get(KnowledgeBase, kb_id)
        assert kb is not None
        event.listen(db, "before_commit", fail_before_commit, once=True)
        with pytest.raises(RuntimeError, match="^injected commit failure$"):
            service.hard_delete_knowledge_base(kb, actor_id=owner_id)
        db.rollback()

        assert db.query(KnowledgeBase).filter_by(id=kb_id).count() == 1
        assert db.query(Document).filter_by(id=document_id).count() == 1
        assert db.query(DocumentChunk).filter_by(id=chunk_id).count() == 1
        assert (
            db.query(UserKnowledgePermission).filter_by(knowledge_base_id=kb_id).count()
            == 1
        )
        assert (
            db.query(TeamKnowledgePermission).filter_by(knowledge_base_id=kb_id).count()
            == 1
        )
        assert db.query(AuditLog).filter_by(action="knowledge.hard_deleted").count() == 0
        # Physical cleanup-before-commit is the documented transitional baseline;
        # durable cleanup moves to the MBA-184 outbox/reconciler boundary.
        assert storage.deleted_paths == ["private/policy.txt"]
        storage.deleted_paths.clear()

        kb = db.get(KnowledgeBase, kb_id)
        assert kb is not None
        service.hard_delete_knowledge_base(kb, actor_id=owner_id)

        assert db.query(KnowledgeBase).filter_by(id=kb_id).count() == 0
        assert db.query(Document).filter_by(id=document_id).count() == 0
        assert db.query(DocumentChunk).filter_by(id=chunk_id).count() == 0
        assert (
            db.query(UserKnowledgePermission).filter_by(knowledge_base_id=kb_id).count()
            == 0
        )
        assert (
            db.query(TeamKnowledgePermission).filter_by(knowledge_base_id=kb_id).count()
            == 0
        )
        assert db.query(Team).filter_by(id=legacy_team_id).count() == 1
        assert db.query(AuditLog).filter_by(action="knowledge.hard_deleted").count() == 1
        assert storage.deleted_paths == ["private/policy.txt"]
        db.close()
        db = None
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
