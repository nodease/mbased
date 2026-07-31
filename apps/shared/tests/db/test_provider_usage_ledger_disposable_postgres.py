"""Opt-in PostgreSQL evidence for the provider usage ledger contracts."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier, BrokenBarrierError

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from apps.shared.db.models.app import App
from apps.shared.db.models.audit_log import AuditEventOutbox
from apps.shared.db.models.llm import LLMUsageLog
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.provider_usage import (
    ProviderUsageCorrectionRecord,
    ProviderUsageOperationRecord,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import (
    RunStatus,
    RunTriggerMode,
    WorkflowRun,
)
from apps.shared.domain.provider_execution_capability import (
    CapabilityPurpose,
    ProviderExecutionBinding,
    RuntimeIdentityContext,
    RuntimePrincipal,
)
from apps.shared.domain.provider_usage_ledger import (
    ProviderUsageIntentSnapshot,
    ProviderUsageLedgerError,
    ProviderUsageMeasurement,
    ProviderUsageOperation,
)
from apps.shared.services.provider_usage_ledger import (
    ProviderUsageCorrectionCommand,
    ProviderUsageLedgerService,
)
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_provider_usage"
NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


def _alembic_returncode(
    *args: str,
    database: str,
    config: DisposablePostgresConfig,
) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "apps/shared/alembic.ini", *args],
        cwd=ROOT_DIR,
        env=config.subprocess_environment(database=database, root_dir=ROOT_DIR),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
        check=False,
    )
    returncode = result.returncode
    del result
    return returncode


def _run_alembic(
    *args: str,
    database: str,
    config: DisposablePostgresConfig,
) -> None:
    returncode = _alembic_returncode(*args, database=database, config=config)
    if returncode != 0:
        pytest.fail(
            f"alembic command failed with exit code {returncode}; "
            "stdout/stderr omitted to protect configuration"
        )


def _provider_usage_parent_revision() -> str:
    config = Config("apps/shared/alembic.ini")
    config.set_main_option("script_location", "apps/shared/alembic")
    revisions = [
        revision
        for revision in ScriptDirectory.from_config(config).walk_revisions()
        if "durable provider usage ledger" in (revision.doc or "").lower()
    ]
    assert len(revisions) == 1
    parent = revisions[0].down_revision
    assert isinstance(parent, str)
    return parent


@pytest.fixture(scope="module")
def provider_usage_postgres():
    if os.getenv(RUN_ENV) != "1":
        pytest.skip(f"set {RUN_ENV}=1 to run disposable PostgreSQL contracts")
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

        _run_alembic("upgrade", "head", database=database, config=config)
        engine = create_engine(config.database_url(database))
        yield engine

        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM llm_usage_logs "
                    "WHERE provider_usage_operation_id IS NOT NULL"
                )
            )
            connection.execute(text("DELETE FROM provider_usage_operations"))
        engine.dispose()
        engine = None
        _run_alembic(
            "downgrade",
            _provider_usage_parent_revision(),
            database=database,
            config=config,
        )
        inspection_engine = create_engine(config.database_url(database))
        try:
            with inspection_engine.connect() as connection:
                assert not connection.execute(
                    text("SELECT to_regclass('provider_usage_operations') IS NOT NULL")
                ).scalar_one()
        finally:
            inspection_engine.dispose()
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
                    "disposable PostgreSQL cleanup failed; connection details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()


def test_reconciliation_partial_indexes_are_installed(provider_usage_postgres) -> None:
    with provider_usage_postgres.connect() as connection:
        index_names = set(
            connection.execute(
                text(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = 'provider_usage_operations'
                    """
                )
            ).scalars()
        )

    assert {
        "ix_provider_usage_operation_projection_recovery",
        "ix_provider_usage_operation_started_recovery",
        "ix_provider_usage_operation_workflow_budget_period",
        "ix_provider_usage_operation_subject_cost_period",
    } <= index_names


def _seed_tenant(engine) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    with Session(engine) as db:
        db.add(
            User(
                id=user_id,
                email=f"provider-usage-{user_id}@example.invalid",
                name="Provider Usage Test",
                social_provider="local",
            )
        )
        db.flush()
        db.add(
            Organization(
                id=organization_id,
                name=f"Provider Usage {organization_id}",
                created_by=user_id,
                is_active=True,
            )
        )
        db.commit()
    return user_id, organization_id


def _snapshot(
    *,
    organization_id: uuid.UUID,
    credential_principal_id: uuid.UUID,
    provider_attempt_id: uuid.UUID | None = None,
    workflow_id: uuid.UUID | None = None,
) -> ProviderUsageIntentSnapshot:
    return ProviderUsageIntentSnapshot(
        binding=ProviderExecutionBinding(
            organization_id=organization_id,
            workflow_id=workflow_id or uuid.uuid4(),
            deployment_id=uuid.uuid4(),
            deployment_version=1,
            node_id="llm-node",
            node_invocation_id=uuid.uuid4(),
            execution_admission_id=uuid.uuid4(),
            provider_attempt_id=provider_attempt_id or uuid.uuid4(),
            purpose=CapabilityPurpose.MAIN_GENERATION,
            container_path=(("loop", "loop-a"),),
        ),
        capability_id=uuid.uuid4(),
        capability_revision=1,
        policy_id=uuid.uuid4(),
        policy_revision=1,
        provider_id=uuid.uuid4(),
        model_id=uuid.uuid4(),
        model_api_id="provider-usage-test-model",
        credential_id=uuid.uuid4(),
        identities=RuntimeIdentityContext(
            execution_subject=RuntimePrincipal.user(credential_principal_id),
            credential_principal=RuntimePrincipal.user(credential_principal_id),
            billing_principal=RuntimePrincipal.organization(organization_id),
            audit_actor=RuntimePrincipal.user(credential_principal_id),
        ),
        permission_revision="a" * 64,
        relation_revision="b" * 64,
        egress_revision="c" * 64,
        pricing_revision="d" * 64,
        input_price_per_1k="0.100000000",
        output_price_per_1k="0.200000000",
        input_token_cap=1_000,
        output_token_cap=100,
        cost_cap_microusd=100_000,
        admitted_input_tokens=500,
        admitted_output_tokens=50,
        expires_at=NOW + timedelta(minutes=5),
    )


def test_downgrade_refuses_to_drop_a_nonempty_canonical_ledger(
    provider_usage_postgres,
) -> None:
    engine = provider_usage_postgres
    user_id, organization_id = _seed_tenant(engine)
    operation = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=_snapshot(
            organization_id=organization_id,
            credential_principal_id=user_id,
        ),
        now=NOW,
    )
    record = ProviderUsageLedgerService._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=None,
        cost_optimizer_candidate_id=None,
    )
    with Session(engine) as db:
        db.add(record)
        db.commit()

    config = DisposablePostgresConfig.from_environment()
    database = str(engine.url.database)
    returncode = _alembic_returncode(
        "downgrade",
        _provider_usage_parent_revision(),
        database=database,
        config=config,
    )

    assert returncode != 0
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT to_regclass('provider_usage_operations') IS NOT NULL")
        ).scalar_one()
        assert connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM provider_usage_operations)")
        ).scalar_one()


def test_canonical_attempt_is_unique_without_live_control_resource_fks(
    provider_usage_postgres,
) -> None:
    engine = provider_usage_postgres
    _, organization_id = _seed_tenant(engine)
    attempt_id = uuid.uuid4()
    snapshot = _snapshot(
        organization_id=organization_id,
        credential_principal_id=uuid.uuid4(),
        provider_attempt_id=attempt_id,
    )
    barrier = Barrier(2)

    def insert_once() -> bool:
        operation = ProviderUsageOperation.intent(
            operation_id=uuid.uuid4(),
            snapshot=snapshot,
            now=NOW,
        )
        record = ProviderUsageLedgerService._record_from_operation(  # noqa: SLF001
            operation,
            workflow_run_id=None,
            cost_optimizer_candidate_id=None,
        )
        with Session(engine) as db:
            db.add(record)
            barrier.wait(timeout=10)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: insert_once(), range(2)))

    assert sorted(results) == [False, True]
    with Session(engine) as db:
        records = db.scalars(
            select(ProviderUsageOperationRecord).where(
                ProviderUsageOperationRecord.organization_id == organization_id,
                ProviderUsageOperationRecord.provider_attempt_id == attempt_id,
            )
        ).all()
        assert len(records) == 1
        assert records[0].credential_principal_id not in {
            user_id for user_id in db.scalars(select(User.id))
        }

        records[0].state = "succeeded"
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_concurrent_terminal_classification_has_one_winner(
    provider_usage_postgres,
) -> None:
    engine = provider_usage_postgres
    user_id, organization_id = _seed_tenant(engine)
    service = ProviderUsageLedgerService()
    operation = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=_snapshot(
            organization_id=organization_id,
            credential_principal_id=user_id,
        ),
        now=NOW,
    ).mark_provider_started(now=NOW + timedelta(seconds=1))
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=None,
        cost_optimizer_candidate_id=None,
    )
    with Session(engine) as db:
        db.add(record)
        db.commit()

    barrier = Barrier(2)

    def finish_once(measurement_values: tuple[int, int, int]) -> str:
        prompt_tokens, completion_tokens, total_cost_microusd = measurement_values
        measurement = ProviderUsageMeasurement(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_cost_microusd=total_cost_microusd,
            latency_ms=42,
        )
        with Session(engine) as db:
            barrier.wait(timeout=10)
            try:
                service.record_success(
                    db,
                    operation_id=operation.id,
                    expected_state_version=operation.state_version,
                    measurement=measurement,
                    now=NOW + timedelta(seconds=2),
                )
            except ProviderUsageLedgerError as exc:
                return exc.code
        return "succeeded"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                finish_once,
                ((10, 5, 2_000), (20, 5, 3_000)),
            )
        )

    assert sorted(outcomes) == ["provider_usage.outcome_conflict", "succeeded"]
    with Session(engine) as db:
        stored = db.get(ProviderUsageOperationRecord, operation.id)
        assert stored is not None
        assert stored.state == "succeeded"
        assert stored.total_cost_microusd in {2_000, 3_000}
        assert db.scalar(
            select(func.count()).select_from(AuditEventOutbox).where(
                AuditEventOutbox.id
                == uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"nodease:provider-usage:{operation.id}:llm.call",
                )
            )
        ) == 1


def test_terminal_replay_projection_and_correction_converge_once(
    provider_usage_postgres,
) -> None:
    engine = provider_usage_postgres
    user_id, organization_id = _seed_tenant(engine)
    service = ProviderUsageLedgerService()
    operation = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=_snapshot(
            organization_id=organization_id,
            credential_principal_id=user_id,
        ),
        now=NOW,
    ).mark_provider_started(now=NOW + timedelta(seconds=1))
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=None,
        cost_optimizer_candidate_id=uuid.uuid4(),
    )
    with Session(engine) as db:
        db.add(record)
        db.commit()

    measurement = ProviderUsageMeasurement(
        prompt_tokens=10,
        completion_tokens=5,
        total_cost_microusd=2_000,
        latency_ms=42,
    )
    for _ in range(2):
        with Session(engine) as db:
            result = service.record_success(
                db,
                operation_id=operation.id,
                expected_state_version=operation.state_version,
                measurement=measurement,
                now=NOW + timedelta(seconds=2),
            )
            assert result.usage_revision == 1

    with Session(engine) as db:
        assert db.scalar(
            select(func.count()).select_from(AuditEventOutbox).where(
                AuditEventOutbox.id
                == uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"nodease:provider-usage:{operation.id}:llm.call",
                )
            )
        ) == 1

    with Session(engine) as db:
        service.project_compatibility_usage(db, operation_id=operation.id, now=NOW)
    with Session(engine) as db:
        projected = db.scalar(
            select(LLMUsageLog).where(
                LLMUsageLog.provider_usage_operation_id == operation.id
            )
        )
        assert projected is not None
        assert projected.workflow_id is None
        assert projected.credential_id is None
        assert projected.model_id is None
        assert projected.cost_optimizer_candidate_id is None
        ledger = db.get(ProviderUsageOperationRecord, operation.id)
        assert ledger is not None
        ledger.projection_status = "pending"
        ledger.projected_usage_log_id = None
        ledger.projected_usage_revision = None
        ledger.projected_at = None
        db.commit()

    with Session(engine) as db:
        service.project_compatibility_usage(db, operation_id=operation.id, now=NOW)
    with Session(engine) as db:
        assert db.scalar(
            select(func.count()).select_from(LLMUsageLog).where(
                LLMUsageLog.provider_usage_operation_id == operation.id
            )
        ) == 1

    same_value_correction = ProviderUsageCorrectionCommand(
        operation_id=operation.id,
        expected_usage_revision=1,
        correction_key="provider-report-same-value",
        measurement=measurement,
        source="provider_reconciliation",
        reason_code="provider_reported_usage",
    )
    for _ in range(2):
        with Session(engine) as db:
            result = service.apply_correction(
                db,
                command=same_value_correction,
                now=NOW + timedelta(hours=12),
            )
            assert result.usage_revision == 2
    with Session(engine) as db:
        with pytest.raises(ProviderUsageLedgerError) as exc_info:
            service.apply_correction(
                db,
                command=ProviderUsageCorrectionCommand(
                    operation_id=operation.id,
                    expected_usage_revision=1,
                    correction_key="provider-report-same-value",
                    measurement=ProviderUsageMeasurement(
                        prompt_tokens=10,
                        completion_tokens=5,
                        total_cost_microusd=2_001,
                        latency_ms=42,
                    ),
                    source="provider_reconciliation",
                    reason_code="provider_reported_usage",
                ),
                now=NOW + timedelta(hours=12),
            )
        assert exc_info.value.code == "provider_usage.correction_conflict"

    correction = ProviderUsageCorrectionCommand(
        operation_id=operation.id,
        expected_usage_revision=2,
        correction_key="provider-report-2",
        measurement=ProviderUsageMeasurement(
            prompt_tokens=20,
            completion_tokens=5,
            total_cost_microusd=3_000,
            latency_ms=40,
        ),
        source="provider_reconciliation",
        reason_code="provider_reported_usage",
    )
    for _ in range(2):
        with Session(engine) as db:
            result = service.apply_correction(
                db,
                command=correction,
                now=NOW + timedelta(days=1),
            )
            assert result.usage_revision == 3
    with Session(engine) as db:
        assert db.scalar(
            select(func.count())
            .select_from(ProviderUsageCorrectionRecord)
            .where(ProviderUsageCorrectionRecord.operation_id == operation.id)
        ) == 2
        with pytest.raises(ProviderUsageLedgerError) as exc_info:
            service.apply_correction(
                db,
                command=ProviderUsageCorrectionCommand(
                    operation_id=operation.id,
                    expected_usage_revision=2,
                    correction_key="provider-report-2",
                    measurement=ProviderUsageMeasurement(
                        prompt_tokens=20,
                        completion_tokens=5,
                        total_cost_microusd=4_000,
                        latency_ms=40,
                    ),
                    source="provider_reconciliation",
                    reason_code="provider_reported_usage",
                ),
                now=NOW + timedelta(days=1),
            )
        assert exc_info.value.code == "provider_usage.correction_conflict"
    with Session(engine) as db:
        service.project_compatibility_usage(db, operation_id=operation.id, now=NOW)
    with Session(engine) as db:
        projected = db.scalar(
            select(LLMUsageLog).where(
                LLMUsageLog.provider_usage_operation_id == operation.id
            )
        )
        assert projected is not None
        assert projected.provider_usage_revision == 3
        assert Decimal(projected.total_cost) == Decimal("0.003000")
        assert db.scalar(
            select(func.count()).select_from(LLMUsageLog).where(
                LLMUsageLog.provider_usage_operation_id == operation.id
            )
        ) == 1


def test_late_workflow_run_is_claimed_and_attached_once(
    provider_usage_postgres,
) -> None:
    engine = provider_usage_postgres
    user_id, organization_id = _seed_tenant(engine)
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    workflow_run_id = uuid.uuid4()
    with Session(engine) as db:
        app = App(
            id=app_id,
            organization_id=organization_id,
            name="Provider Usage Late Run",
            description=None,
            icon=None,
            url_slug=f"provider-usage-{app_id.hex}",
            auth_secret=None,
            created_by=user_id,
        )
        db.add(app)
        db.flush()
        workflow = Workflow(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={},
            features={},
            env_variables=[],
            runtime_variables=[],
            created_by=user_id,
        )
        db.add(workflow)
        db.flush()
        app.workflow_id = workflow_id
        db.commit()

    service = ProviderUsageLedgerService()
    operation = ProviderUsageOperation.intent(
        operation_id=uuid.uuid4(),
        snapshot=_snapshot(
            organization_id=organization_id,
            credential_principal_id=user_id,
            workflow_id=workflow_id,
        ),
        now=NOW,
    ).mark_provider_started(now=NOW + timedelta(seconds=1))
    record = service._record_from_operation(  # noqa: SLF001
        operation,
        workflow_run_id=workflow_run_id,
        cost_optimizer_candidate_id=None,
    )
    with Session(engine) as db:
        db.add(record)
        db.commit()
    with Session(engine) as db:
        service.record_success(
            db,
            operation_id=operation.id,
            expected_state_version=operation.state_version,
            measurement=ProviderUsageMeasurement(
                prompt_tokens=10,
                completion_tokens=5,
                total_cost_microusd=2_000,
                latency_ms=42,
            ),
            now=NOW + timedelta(seconds=2),
        )
    with Session(engine) as db:
        service.project_compatibility_usage(db, operation_id=operation.id, now=NOW)
    with Session(engine) as db:
        projected = db.scalar(
            select(LLMUsageLog).where(
                LLMUsageLog.provider_usage_operation_id == operation.id
            )
        )
        assert projected is not None
        assert projected.workflow_id == workflow_id
        assert projected.workflow_run_id is None
        ledger = db.get(ProviderUsageOperationRecord, operation.id)
        assert ledger is not None
        assert ledger.projection_status == "awaiting_workflow_run"
        db.add(
            WorkflowRun(
                id=workflow_run_id,
                workflow_id=workflow_id,
                user_id=user_id,
                app_id=app_id,
                status=RunStatus.RUNNING,
                trigger_mode=RunTriggerMode.MANUAL,
                inputs={},
                started_at=NOW,
            )
        )
        ledger = db.get(ProviderUsageOperationRecord, operation.id)
        assert ledger is not None
        ledger.projection_status = "retryable_failure"
        ledger.projection_next_attempt_at = NOW
        ledger.projection_reason_code = "projection_commit_failed"
        db.commit()

    with Session(engine) as db:
        claimed = service.claim_pending_projection_ids(
            db,
            limit=10,
            now=NOW + timedelta(minutes=1),
        )
        assert operation.id in claimed
        assert len(claimed) == len(set(claimed))
    with Session(engine) as db:
        service.project_compatibility_usage(
            db,
            operation_id=operation.id,
            now=NOW + timedelta(minutes=1),
        )
    with Session(engine) as db:
        projected = db.scalar(
            select(LLMUsageLog).where(
                LLMUsageLog.provider_usage_operation_id == operation.id
            )
        )
        run = db.get(WorkflowRun, workflow_run_id)
        assert projected is not None
        assert run is not None
        assert projected.workflow_run_id == workflow_run_id
        assert run.total_tokens == 15
        assert Decimal(run.total_cost) == Decimal("0.002000")
        ledger = db.get(ProviderUsageOperationRecord, operation.id)
        assert ledger is not None
        assert ledger.projection_status == "projected"
        assert service.claim_pending_projection_ids(
            db,
            limit=10,
            now=NOW + timedelta(minutes=2),
        ) == ()

    with Session(engine) as db:
        app = db.get(App, app_id)
        workflow = db.get(Workflow, workflow_id)
        assert app is not None
        assert workflow is not None
        app.workflow_id = None
        db.flush()
        db.delete(workflow)
        db.commit()
    with Session(engine) as db:
        projected = db.scalar(
            select(LLMUsageLog).where(
                LLMUsageLog.provider_usage_operation_id == operation.id
            )
        )
        ledger = db.get(ProviderUsageOperationRecord, operation.id)
        assert projected is not None
        assert ledger is not None
        assert projected.workflow_id is None
        assert projected.workflow_run_id is None
        assert Decimal(projected.total_cost) == Decimal("0.002000")
        assert ledger.total_cost_microusd == 2_000


def test_concurrent_projections_accumulate_one_workflow_run_total(
    provider_usage_postgres,
) -> None:
    engine = provider_usage_postgres
    user_id, organization_id = _seed_tenant(engine)
    app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    workflow_run_id = uuid.uuid4()
    with Session(engine) as db:
        app = App(
            id=app_id,
            organization_id=organization_id,
            name="Provider Usage Concurrent Projection",
            description=None,
            icon=None,
            url_slug=f"provider-usage-{app_id.hex}",
            auth_secret=None,
            created_by=user_id,
        )
        db.add(app)
        db.flush()
        workflow = Workflow(
            id=workflow_id,
            organization_id=organization_id,
            app_id=app_id,
            graph={},
            features={},
            env_variables=[],
            runtime_variables=[],
            created_by=user_id,
        )
        db.add(workflow)
        db.flush()
        app.workflow_id = workflow_id
        db.add(
            WorkflowRun(
                id=workflow_run_id,
                workflow_id=workflow_id,
                user_id=user_id,
                app_id=app_id,
                status=RunStatus.RUNNING,
                trigger_mode=RunTriggerMode.MANUAL,
                inputs={},
                started_at=NOW,
            )
        )
        db.commit()

    operation_ids = []
    terminal_service = ProviderUsageLedgerService()
    for prompt_tokens, completion_tokens, total_cost_microusd in (
        (10, 5, 2_000),
        (7, 3, 1_300),
    ):
        operation = ProviderUsageOperation.intent(
            operation_id=uuid.uuid4(),
            snapshot=_snapshot(
                organization_id=organization_id,
                credential_principal_id=user_id,
                workflow_id=workflow_id,
            ),
            now=NOW,
        ).mark_provider_started(now=NOW + timedelta(seconds=1))
        measurement = ProviderUsageMeasurement(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_cost_microusd=total_cost_microusd,
            latency_ms=42,
        )
        operation_ids.append(operation.id)
        with Session(engine) as db:
            db.add(
                ProviderUsageLedgerService._record_from_operation(  # noqa: SLF001
                    operation,
                    workflow_run_id=workflow_run_id,
                    cost_optimizer_candidate_id=None,
                )
            )
            db.commit()
        with Session(engine) as db:
            terminal_service.record_success(
                db,
                operation_id=operation.id,
                expected_state_version=operation.state_version,
                measurement=measurement,
                now=NOW + timedelta(seconds=2),
            )

    barrier = Barrier(2)

    class CoordinatedProjectionService(ProviderUsageLedgerService):
        @staticmethod
        def _apply_run_usage_delta(run, **kwargs) -> None:
            try:
                barrier.wait(timeout=2)
            except BrokenBarrierError:
                pass
            ProviderUsageLedgerService._apply_run_usage_delta(run, **kwargs)  # noqa: SLF001

    service = CoordinatedProjectionService()

    def project(operation_id: uuid.UUID) -> None:
        with Session(engine) as db:
            service.project_compatibility_usage(
                db,
                operation_id=operation_id,
                now=NOW + timedelta(minutes=1),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(project, operation_ids))

    with Session(engine) as db:
        run = db.get(WorkflowRun, workflow_run_id)
        assert run is not None
        assert run.total_tokens == 25
        assert Decimal(run.total_cost) == Decimal("0.003300")
        assert db.scalar(
            select(func.count()).select_from(LLMUsageLog).where(
                LLMUsageLog.provider_usage_operation_id.in_(operation_ids)
            )
        ) == 2
