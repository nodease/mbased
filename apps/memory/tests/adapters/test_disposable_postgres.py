"""Opt-in PostgreSQL evidence for Conversation Memory persistence invariants."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from apps.memory.adapters.persistence.repository import (
    SqlAlchemyConversationMemoryRepository,
    SqlAlchemyMemoryUnitOfWork,
)
from apps.memory.adapters.persistence.readiness import (
    REQUIRED_MEMORY_SCHEMA,
    check_memory_schema_readiness,
)
from apps.memory.application.lifecycle import (
    CreateSessionCommand,
    CreateSessionUseCase,
    StartTurnCommand,
    StartTurnUseCase,
)
from apps.memory.application.dispatch import (
    ClaimTurnDispatchCommand,
    ClaimTurnDispatchUseCase,
    MarkTurnDispatchPublishedCommand,
    MarkTurnDispatchPublishedUseCase,
)
from apps.memory.domain.conversation import (
    AudienceKind,
    ProtectedContent,
    ProtectedEntryContent,
)
from apps.memory.domain.errors import MemoryDomainError
from apps.memory.domain.public_access import ConversationIdempotency
from apps.shared.db.models.audit_log import AuditEventOutbox
from apps.shared.db.models.conversation_memory import (
    ConversationAccessGrantRecord,
    ConversationIdempotencyRecord,
    ConversationMemoryEntryRecord,
    ConversationSessionRecord,
    ConversationTurnRecord,
    MemoryTurnDispatchJobRecord,
)
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import (
    DeploymentType,
    WorkflowDeployment,
)
from apps.shared.db.models.workflow_run import (
    NodeRunStatus,
    RunStatus,
    RunTriggerMode,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)


ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_memory"
PARENT_REVISION = "aa0b1c2d3e4f"
MEMORY_MERGE_REVISION = "ac2d3e4f5061"
MEMORY_RUNTIME_REVISION = "b18c9d0e1f23"
PUBLIC_CONVERSATION_PARENT_REVISION = "f4a5b6c7d8e9"
PUBLIC_CONVERSATION_REPLAY_REVISION = "ac1d2e3f4a50"
PUBLIC_CONVERSATION_SCOPE_REVISION = "ad2e3f4a5b61"
PUBLIC_CONVERSATION_RESULT_SNAPSHOT_REVISION = "ae3f4a5b6c72"
PUBLIC_CONVERSATION_RESULT_SNAPSHOT_COLUMNS = {
    "result_lifecycle",
    "result_lifecycle_revision",
    "result_memory_contract_version",
    "result_expires_at",
    "result_previous_lifecycle",
    "result_previous_lifecycle_revision",
}
PUBLIC_CONVERSATION_AUTHORIZATION_SCOPE_COLUMNS = {
    "authorization_app_id",
    "authorization_verifier_key_version",
    "authorization_verifier_hash",
}
MEMORY_RUNTIME_COLUMNS = {
    "conversation_turns": {
        "request_fingerprint_key_version",
        "access_grant_id",
    },
    "conversation_memory_entries": {"dependency_proof_version"},
}
MEMORY_RUNTIME_TABLES = {
    "conversation_workflow_execution_admissions",
    "conversation_workflow_execution_events",
}
POST_FOUNDATION_COLUMNS = {
    **MEMORY_RUNTIME_COLUMNS,
    "conversation_purge_jobs": {
        "deployment_id",
        "deployment_version",
        "audience_kind",
        "app_id",
    },
    "conversation_idempotency_records": (
        PUBLIC_CONVERSATION_RESULT_SNAPSHOT_COLUMNS
        | PUBLIC_CONVERSATION_AUTHORIZATION_SCOPE_COLUMNS
    ),
}
FOUNDATION_MEMORY_SCHEMA = {
    table_name: columns - POST_FOUNDATION_COLUMNS.get(table_name, set())
    for table_name, columns in REQUIRED_MEMORY_SCHEMA.items()
    if table_name not in {"conversation_secret_replays", *MEMORY_RUNTIME_TABLES}
}


def _run_alembic(
    revision: str,
    *,
    operation: str,
    database: str,
    config: DisposablePostgresConfig,
) -> None:
    environment = config.subprocess_environment(database=database, root_dir=ROOT_DIR)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "apps/shared/alembic.ini",
            operation,
            revision,
        ],
        cwd=ROOT_DIR,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        del result
        pytest.fail(
            "alembic command failed; stdout/stderr omitted to avoid leaking "
            "local configuration"
        )


def _assert_alembic_fails(
    revision: str,
    *,
    operation: str,
    database: str,
    config: DisposablePostgresConfig,
) -> None:
    environment = config.subprocess_environment(database=database, root_dir=ROOT_DIR)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "apps/shared/alembic.ini",
            operation,
            revision,
        ],
        cwd=ROOT_DIR,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert result.returncode != 0


def _enable_vector_extension(database: str, config: DisposablePostgresConfig) -> None:
    engine = create_engine(config.database_url(database), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    finally:
        engine.dispose()


def _seed_legacy_execution(engine) -> dict[str, uuid.UUID]:
    now = datetime.now(timezone.utc)
    ids = {
        name: uuid.uuid4()
        for name in (
            "user",
            "organization",
            "app",
            "workflow",
            "deployment",
            "run",
            "node_run",
        )
    }
    with Session(engine) as session:
        session.add(
            User(
                id=ids["user"],
                email=f"memory-{ids['user']}@example.invalid",
                name="Memory Migration Test",
                social_provider="local",
            )
        )
        session.flush()
        session.add(
            Organization(
                id=ids["organization"],
                name="Memory Migration Test Organization",
                created_by=ids["user"],
                is_active=True,
            )
        )
        session.flush()
        # This seed intentionally runs against the schema before the Memory
        # migration. Keep App persistence schema-compatible instead of using
        # the current ORM model, which can contain columns added later.
        session.execute(
            text(
                "INSERT INTO apps "
                "(id, organization_id, name, url_slug, auth_secret, "
                "is_api_enabled, api_req_per_minute, api_req_per_hour, "
                "is_market, created_by, created_at, updated_at) "
                "VALUES (:id, :organization_id, :name, :url_slug, "
                ":auth_secret, :is_api_enabled, :api_req_per_minute, "
                ":api_req_per_hour, :is_market, :created_by, :created_at, "
                ":updated_at)"
            ),
            {
                "id": ids["app"],
                "organization_id": ids["organization"],
                "name": "Memory Migration Test App",
                "url_slug": f"memory-{ids['app']}",
                "auth_secret": "redacted-test-placeholder",
                "is_api_enabled": True,
                "api_req_per_minute": 60,
                "api_req_per_hour": 3600,
                "is_market": False,
                "created_by": ids["user"],
                "created_at": now,
                "updated_at": now,
            },
        )
        session.flush()
        session.add(
            Workflow(
                id=ids["workflow"],
                organization_id=ids["organization"],
                app_id=ids["app"],
                graph={"nodes": [], "edges": []},
                created_by=ids["user"],
            )
        )
        session.flush()
        session.execute(
            text("UPDATE apps SET workflow_id = :workflow_id WHERE id = :app_id"),
            {"workflow_id": ids["workflow"], "app_id": ids["app"]},
        )
        deployment = WorkflowDeployment(
            id=ids["deployment"],
            app_id=ids["app"],
            version=1,
            type=DeploymentType.CHATBOT,
            graph_snapshot={"nodes": [], "edges": []},
            created_by=ids["user"],
            is_active=True,
        )
        session.add(deployment)
        session.flush()
        session.execute(
            text(
                "UPDATE apps SET active_deployment_id = :deployment_id "
                "WHERE id = :app_id"
            ),
            {"deployment_id": deployment.id, "app_id": ids["app"]},
        )
        session.add(
            WorkflowRun(
                id=ids["run"],
                workflow_id=ids["workflow"],
                user_id=ids["user"],
                app_id=ids["app"],
                deployment_id=ids["deployment"],
                workflow_version=1,
                status=RunStatus.SUCCESS,
                trigger_mode=RunTriggerMode.MANUAL,
                inputs={},
                outputs={},
            )
        )
        session.flush()
        session.add(
            WorkflowNodeRun(
                id=ids["node_run"],
                workflow_run_id=ids["run"],
                node_id="memory-migration-test-node",
                node_type="answerNode",
                status=NodeRunStatus.SUCCESS,
                inputs={},
                process_data={},
                outputs={},
            )
        )
        session.commit()
    return ids


def _create_session(engine, ids: dict[str, uuid.UUID], session_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        repository = SqlAlchemyConversationMemoryRepository(db)
        uow = SqlAlchemyMemoryUnitOfWork(db)
        CreateSessionUseCase(repository=repository, uow=uow).execute(
            CreateSessionCommand(
                session_id=session_id,
                organization_id=ids["organization"],
                app_id=ids["app"],
                workflow_id=ids["workflow"],
                deployment_id=ids["deployment"],
                deployment_version=1,
                deployment_snapshot_hash=None,
                mapping_version="mapping-v1",
                memory_policy_version="memory-v1",
                memory_contract_version="conversation-memory-v1",
                storage_generation=1,
                audience_kind=AudienceKind.PUBLIC_CHATBOT,
                subject_type=None,
                subject_id=None,
                idle_expires_at=now + timedelta(hours=24),
                absolute_expires_at=now + timedelta(days=7),
                now=now,
            )
        )


def _start_command(
    ids: dict[str, uuid.UUID],
    session_id: uuid.UUID,
    *,
    digest_seed: str,
) -> StartTurnCommand:
    return StartTurnCommand(
        organization_id=ids["organization"],
        session_id=session_id,
        expected_lifecycle_revision=1,
        turn_id=uuid.uuid4(),
        user_entry_id=uuid.uuid4(),
        dispatch_id=uuid.uuid4(),
        idempotency_key_hash=digest_seed * 64,
        request_fingerprint=("f" if digest_seed != "f" else "e") * 64,
        user_content=ProtectedEntryContent(
            display=ProtectedContent(
                ciphertext=b"opaque-test-display-ciphertext",
                key_version="display-key-v1",
                format_version="memory-envelope-v1",
                content_digest="c" * 64,
                plaintext_byte_length=64,
            ),
            model=ProtectedContent(
                ciphertext=b"opaque-test-model-ciphertext",
                key_version="model-key-v1",
                format_version="memory-envelope-v1",
                content_digest="d" * 64,
                plaintext_byte_length=72,
            ),
        ),
        channel="conversation",
        minimum_worker_capability="memory-runtime-v1",
        max_dispatch_attempts=5,
        now=datetime.now(timezone.utc),
    )


class _FailingDispatchRepository(SqlAlchemyConversationMemoryRepository):
    def add_dispatch_job(self, job) -> None:
        raise RuntimeError("safe synthetic dispatch persistence failure")


def _assert_legacy_execution_survives(engine, ids: dict[str, uuid.UUID]) -> None:
    with Session(engine) as session:
        assert session.get(WorkflowRun, ids["run"]) is not None
        assert session.get(WorkflowNodeRun, ids["node_run"]) is not None


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL Memory evidence",
)
def test_memory_migration_uow_and_concurrent_start_turn_contracts():
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

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {quoted_database}"))
        database_created = True
        _enable_vector_extension(database, config)
        _run_alembic(
            PARENT_REVISION,
            operation="upgrade",
            database=database,
            config=config,
        )

        engine = create_engine(config.database_url(database))
        try:
            ids = _seed_legacy_execution(engine)
            _run_alembic(
                MEMORY_MERGE_REVISION,
                operation="upgrade",
                database=database,
                config=config,
            )
            # Memory foundation과 그 parent에서 분기한 runtime revision만 검증한다.
            # 최신 mainline head까지 올린 뒤 rollback하면 의도적으로 비가역인
            # unrelated migration 때문에 이 계약과 무관하게 실패할 수 있다.
            _assert_legacy_execution_survives(engine, ids)
            with Session(engine) as db:
                assert (
                    check_memory_schema_readiness(
                        db,
                        required_schema=FOUNDATION_MEMORY_SCHEMA,
                    ).ready
                    is True
                )

            _run_alembic(
                MEMORY_RUNTIME_REVISION,
                operation="upgrade",
                database=database,
                config=config,
            )
            with Session(engine) as db:
                schema_inspector = inspect(db.get_bind())
                turn_columns = {
                    str(column["name"])
                    for column in schema_inspector.get_columns("conversation_turns")
                }
                entry_columns = {
                    str(column["name"])
                    for column in schema_inspector.get_columns(
                        "conversation_memory_entries"
                    )
                }
                assert {
                    "request_fingerprint_key_version",
                    "access_grant_id",
                } <= turn_columns
                assert "dependency_proof_version" in entry_columns
                assert schema_inspector.has_table(
                    "conversation_workflow_execution_admissions"
                )

            binding_session_id = uuid.uuid4()
            _create_session(engine, ids, binding_session_id)
            binding_now = datetime.now(timezone.utc)
            with Session(engine) as db:
                db.add(
                    ConversationAccessGrantRecord(
                        id=uuid.uuid4(),
                        organization_id=ids["organization"],
                        session_id=binding_session_id,
                        deployment_id=ids["deployment"],
                        deployment_version=1,
                        audience_kind=AudienceKind.PUBLIC_CHATBOT.value,
                        verifier_hash="a" * 64,
                        verifier_key_version="grant-key-v1",
                        state="active",
                        issued_at=binding_now,
                        expires_at=binding_now + timedelta(hours=1),
                    )
                )
                db.commit()

            for deployment_version, audience_kind in (
                (2, AudienceKind.PUBLIC_CHATBOT.value),
                (1, AudienceKind.AUTHENTICATED_INTERNAL_CHATBOT.value),
            ):
                with Session(engine) as db:
                    db.add(
                        ConversationAccessGrantRecord(
                            id=uuid.uuid4(),
                            organization_id=ids["organization"],
                            session_id=binding_session_id,
                            deployment_id=ids["deployment"],
                            deployment_version=deployment_version,
                            audience_kind=audience_kind,
                            verifier_hash=uuid.uuid4().hex * 2,
                            verifier_key_version="grant-key-v1",
                            state="active",
                            issued_at=binding_now,
                            expires_at=binding_now + timedelta(hours=1),
                        )
                    )
                    with pytest.raises(IntegrityError):
                        db.commit()

            concurrent_session_id = uuid.uuid4()
            _create_session(engine, ids, concurrent_session_id)
            barrier = Barrier(2)

            def start_turn(command: StartTurnCommand) -> str:
                with Session(engine) as db:
                    repository = SqlAlchemyConversationMemoryRepository(db)
                    uow = SqlAlchemyMemoryUnitOfWork(db)
                    barrier.wait(timeout=10)
                    try:
                        StartTurnUseCase(repository=repository, uow=uow).execute(
                            command
                        )
                        return "success"
                    except MemoryDomainError as exc:
                        return exc.code

            commands = (
                _start_command(ids, concurrent_session_id, digest_seed="a"),
                _start_command(ids, concurrent_session_id, digest_seed="b"),
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(start_turn, commands))
            assert sorted(outcomes) == ["memory.active_turn_conflict", "success"]

            with Session(engine) as db:
                assert (
                    db.scalar(
                        select(func.count(ConversationTurnRecord.id)).where(
                            ConversationTurnRecord.session_id == concurrent_session_id
                        )
                    )
                    == 1
                )
                dispatch_id = db.scalar(
                    select(MemoryTurnDispatchJobRecord.id).where(
                        MemoryTurnDispatchJobRecord.session_id == concurrent_session_id
                    )
                )
                assert dispatch_id is not None
                assert (
                    db.scalar(
                        select(func.count(ConversationMemoryEntryRecord.id)).where(
                            ConversationMemoryEntryRecord.session_id
                            == concurrent_session_id
                        )
                    )
                    == 1
                )
                assert (
                    db.scalar(
                        select(func.count(MemoryTurnDispatchJobRecord.id)).where(
                            MemoryTurnDispatchJobRecord.session_id
                            == concurrent_session_id
                        )
                    )
                    == 1
                )
                wrong_tenant_repository = SqlAlchemyConversationMemoryRepository(db)
                assert (
                    wrong_tenant_repository.lock_session(
                        organization_id=uuid.uuid4(),
                        session_id=concurrent_session_id,
                    )
                    is None
                )
                db.rollback()

            claim_now = datetime.now(timezone.utc)
            with Session(engine) as db:
                repository = SqlAlchemyConversationMemoryRepository(db)
                uow = SqlAlchemyMemoryUnitOfWork(db)
                claimed = ClaimTurnDispatchUseCase(
                    repository=repository,
                    uow=uow,
                ).execute(
                    ClaimTurnDispatchCommand(
                        organization_id=ids["organization"],
                        dispatch_id=dispatch_id,
                        owner="dispatcher-integration",
                        deadline=claim_now + timedelta(seconds=30),
                        now=claim_now,
                    )
                )
                MarkTurnDispatchPublishedUseCase(
                    repository=repository,
                    uow=uow,
                ).execute(
                    MarkTurnDispatchPublishedCommand(
                        organization_id=ids["organization"],
                        dispatch_id=dispatch_id,
                        owner="dispatcher-integration",
                        claim_generation=claimed.claim_generation,
                        broker_message_id="opaque-integration-message-reference",
                        now=claim_now + timedelta(seconds=1),
                    )
                )
            with Session(engine) as db:
                stored_dispatch = db.get(
                    MemoryTurnDispatchJobRecord,
                    dispatch_id,
                )
                assert stored_dispatch is not None
                assert stored_dispatch.status == "published"
                assert stored_dispatch.claim_owner is None

            rollback_session_id = uuid.uuid4()
            _create_session(engine, ids, rollback_session_id)
            rollback_command = _start_command(
                ids,
                rollback_session_id,
                digest_seed="d",
            )
            with Session(engine) as db:
                repository = _FailingDispatchRepository(db)
                uow = SqlAlchemyMemoryUnitOfWork(db)
                with pytest.raises(RuntimeError, match="synthetic dispatch"):
                    StartTurnUseCase(repository=repository, uow=uow).execute(
                        rollback_command
                    )
            with Session(engine) as db:
                stored_session = db.get(
                    ConversationSessionRecord,
                    rollback_session_id,
                )
                assert stored_session is not None
                assert stored_session.active_turn_id is None
                assert stored_session.next_turn_sequence == 1
                assert (
                    db.scalar(
                        select(func.count(ConversationTurnRecord.id)).where(
                            ConversationTurnRecord.session_id == rollback_session_id
                        )
                    )
                    == 0
                )
                assert (
                    db.scalar(
                        select(func.count(MemoryTurnDispatchJobRecord.id)).where(
                            MemoryTurnDispatchJobRecord.session_id
                            == rollback_session_id
                        )
                    )
                    == 0
                )

            _assert_alembic_fails(
                MEMORY_MERGE_REVISION,
                operation="downgrade",
                database=database,
                config=config,
            )
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE conversation_memory_entries "
                        "SET dependency_proof_version = NULL"
                    )
                )
                connection.execute(
                    text(
                        "UPDATE conversation_turns "
                        "SET request_fingerprint_key_version = NULL, "
                        "access_grant_id = NULL"
                    )
                )
                connection.execute(
                    text(
                        "DELETE FROM conversation_workflow_execution_admissions"
                    )
                )

            _run_alembic(
                PARENT_REVISION,
                operation="downgrade",
                database=database,
                config=config,
            )
            _assert_legacy_execution_survives(engine, ids)

            # Verify the MBA-317 additive revision against its actual current
            # parent without forcing unrelated later revisions through the
            # Memory foundation rollback boundary above.
            _run_alembic(
                PUBLIC_CONVERSATION_PARENT_REVISION,
                operation="upgrade",
                database=database,
                config=config,
            )
            _run_alembic(
                PUBLIC_CONVERSATION_REPLAY_REVISION,
                operation="upgrade",
                database=database,
                config=config,
            )
            _run_alembic(
                PUBLIC_CONVERSATION_SCOPE_REVISION,
                operation="upgrade",
                database=database,
                config=config,
            )
            _assert_legacy_execution_survives(engine, ids)
            with Session(engine) as db:
                readiness = check_memory_schema_readiness(db)
                assert readiness.ready is False
                assert set(
                    readiness.missing_columns.get(
                        "conversation_purge_jobs",
                        (),
                    )
                ) == {"app_id"}
                assert set(
                    readiness.missing_columns.get(
                        "conversation_idempotency_records",
                        (),
                    )
                ) == (
                    PUBLIC_CONVERSATION_RESULT_SNAPSHOT_COLUMNS
                    | PUBLIC_CONVERSATION_AUTHORIZATION_SCOPE_COLUMNS
                )

            _run_alembic(
                PUBLIC_CONVERSATION_RESULT_SNAPSHOT_REVISION,
                operation="upgrade",
                database=database,
                config=config,
            )
            with Session(engine) as db:
                readiness = check_memory_schema_readiness(db)
                assert readiness.ready is False
                assert set(
                    readiness.missing_columns.get(
                        "conversation_purge_jobs",
                        (),
                    )
                ) == {"app_id"}
                assert (
                    set(
                        readiness.missing_columns.get(
                            "conversation_idempotency_records",
                            (),
                        )
                    )
                    == PUBLIC_CONVERSATION_AUTHORIZATION_SCOPE_COLUMNS
                )

            # The authorization-scope migration is the immediate additive
            # successor. Advance relatively so this contract does not pin a
            # repository-specific revision identifier.
            _run_alembic(
                "+1",
                operation="upgrade",
                database=database,
                config=config,
            )
            with Session(engine) as db:
                readiness = check_memory_schema_readiness(db)
                assert readiness.ready is False
                assert {
                    table_name: set(columns)
                    for table_name, columns in readiness.missing_columns.items()
                } == {
                    **MEMORY_RUNTIME_COLUMNS,
                    **{
                        table_name: set(REQUIRED_MEMORY_SCHEMA[table_name])
                        for table_name in MEMORY_RUNTIME_TABLES
                    },
                }
                repository = SqlAlchemyConversationMemoryRepository(db)
                replacement_now = datetime.now(timezone.utc)
                expired_now = replacement_now - timedelta(days=2)
                scope_digest = "9" * 64
                key_hash = "8" * 64
                expired = ConversationIdempotency.pending(
                    record_id=uuid.uuid4(),
                    organization_id=ids["organization"],
                    operation="conversation.create",
                    scope_digest=scope_digest,
                    idempotency_key_hash=key_hash,
                    request_fingerprint="7" * 64,
                    retention_expires_at=expired_now + timedelta(days=1),
                    now=expired_now,
                )
                assert repository.reserve_idempotency(expired).created is True
                db.commit()

                replacement = ConversationIdempotency.pending(
                    record_id=uuid.uuid4(),
                    organization_id=ids["organization"],
                    operation="conversation.create",
                    scope_digest=scope_digest,
                    idempotency_key_hash=key_hash,
                    request_fingerprint="6" * 64,
                    retention_expires_at=replacement_now + timedelta(days=1),
                    now=replacement_now,
                )
                reservation = repository.reserve_idempotency(replacement)
                assert reservation.created is True
                assert reservation.record.id == replacement.id
                db.commit()

                persisted = list(
                    db.scalars(
                        select(ConversationIdempotencyRecord).where(
                            ConversationIdempotencyRecord.organization_id
                            == ids["organization"],
                            ConversationIdempotencyRecord.operation
                            == "conversation.create",
                            ConversationIdempotencyRecord.scope_digest == scope_digest,
                            ConversationIdempotencyRecord.idempotency_key_hash
                            == key_hash,
                        )
                    ).all()
                )
                assert len(persisted) == 1
                assert persisted[0].id == replacement.id
                assert persisted[0].request_fingerprint == "6" * 64

                public_outbox = AuditEventOutbox(
                    payload={
                        "id": str(uuid.uuid4()),
                        "actor_id": None,
                        "actor_type": "public",
                        "action": "memory.session.created",
                    },
                    idempotency_key=f"memory-public-{uuid.uuid4()}",
                )
                db.add(public_outbox)
                db.commit()

            _assert_alembic_fails(
                PUBLIC_CONVERSATION_PARENT_REVISION,
                operation="downgrade",
                database=database,
                config=config,
            )
            with Session(engine) as db:
                db.execute(text("DELETE FROM audit_event_outbox"))
                db.commit()

            _run_alembic(
                PUBLIC_CONVERSATION_PARENT_REVISION,
                operation="downgrade",
                database=database,
                config=config,
            )
            with Session(engine) as db:
                readiness = check_memory_schema_readiness(db)
                assert readiness.ready is False
                assert "conversation_secret_replays" in readiness.missing_tables
        finally:
            engine.dispose()
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable or rejected the connection; "
            "connection details omitted",
            pytrace=False,
        ) from None
    finally:
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
