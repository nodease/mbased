"""Opt-in PostgreSQL evidence for the external-effect stable slot."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from apps.workflow_engine.adapters.db.external_effect_repository import (
    SQLAlchemyEffectAttemptRepository,
)
from apps.workflow_engine.application.external_effect import (
    AcquireKind,
    EffectAttemptSpec,
)
from apps.workflow_engine.composition.external_effect_readiness import (
    require_external_effect_worker_ready,
)
from apps.workflow_engine.domain.external_effect import (
    EffectOutcome,
    ExternalEffectContext,
    ReplayDecision,
    provider_contract_registry,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_external_effect"
EXTERNAL_EFFECT_REVISION = "fe3f4a5b6c78"
EXTERNAL_EFFECT_MIGRATION_PARENT_REVISION = "b39e0f1a2b43"


def _alembic_python() -> str:
    candidates = (
        ROOT_DIR / "apps" / "gateway" / ".venv" / "Scripts" / "python.exe",
        ROOT_DIR / "apps" / "gateway" / ".venv" / "bin" / "python",
    )
    return str(next((path for path in candidates if path.exists()), sys.executable))


def _run_alembic(
    database: str,
    config: DisposablePostgresConfig,
    *args: str,
) -> None:
    command_args = args or ("upgrade", "heads")
    result = subprocess.run(
        [
            _alembic_python(),
            "-m",
            "alembic",
            "-c",
            "apps/shared/alembic.ini",
            *command_args,
        ],
        cwd=ROOT_DIR,
        env=config.subprocess_environment(database=database, root_dir=ROOT_DIR),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "alembic failed for disposable external-effect database; "
            "stdout/stderr omitted to avoid leaking local configuration"
        )


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL external-effect evidence",
)
def test_concurrent_claims_have_one_database_winner():
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
        _run_alembic(database, config)
        engine = create_engine(config.database_url(database))
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        contracts = provider_contract_registry(include_test_profiles=True)
        repository = SQLAlchemyEffectAttemptRepository(
            session_factory,
            contracts=contracts,
        )
        require_external_effect_worker_ready(engine)
        context = ExternalEffectContext(
            organization_id=uuid.uuid4(),
            app_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            execution_id=uuid.uuid4(),
            node_invocation_id=uuid.uuid4(),
            node_id="http-1",
            workflow_run_id=uuid.uuid4(),
            node_run_id=uuid.uuid4(),
        )
        profile = provider_contract_registry().get(
            "generic_http",
            "generic_http.request",
            "generic_http.request.v1",
        )
        spec = EffectAttemptSpec(
            context=context,
            profile=profile,
            effect_sequence=0,
            effect_input_digest="a" * 64,
            replay_deadline_at=None,
            key_version=None,
            key_format_version=None,
            idempotency_key_fingerprint=None,
        )
        barrier = Barrier(2)

        def acquire(index: int):
            barrier.wait(timeout=10)
            return repository.acquire(
                spec,
                claim_owner=f"worker-{index}",
                claim_ttl=timedelta(seconds=630),
                allow_retry=True,
                now=datetime.now(timezone.utc),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(acquire, range(2)))

        assert sorted(result.kind for result in outcomes) == [
            AcquireKind.CLAIMED,
            AcquireKind.WAIT,
        ]
        winner = next(
            result.record for result in outcomes if result.kind is AcquireKind.CLAIMED
        )
        in_flight = repository.mark_in_flight(
            winner,
            now=datetime.now(timezone.utc),
        )
        repository.finish(
            in_flight,
            outcome=EffectOutcome.FAILED_BEFORE_EFFECT,
            replay_decision=ReplayDecision.RETRY_BEFORE_EFFECT,
            replay_result=None,
            provider_status_code=None,
            error_code="connection_failed",
            now=datetime.now(timezone.utc),
        )
        retry_context = replace(
            context,
            workflow_run_id=uuid.uuid4(),
            node_run_id=uuid.uuid4(),
        )
        reopened = repository.acquire(
            replace(spec, context=retry_context),
            claim_owner="reopened-worker",
            claim_ttl=timedelta(seconds=630),
            allow_retry=True,
            now=datetime.now(timezone.utc),
        )
        assert reopened.kind is AcquireKind.CLAIMED
        assert reopened.record.claim_generation == winner.claim_generation + 1
        assert (
            reopened.record.spec.context.workflow_run_id
            == retry_context.workflow_run_id
        )
        assert reopened.record.spec.context.node_run_id == retry_context.node_run_id
        with pytest.raises(RuntimeError, match="stale effect claim"):
            repository.mark_in_flight(
                in_flight,
                now=datetime.now(timezone.utc),
            )

        lock_context = replace(
            context,
            execution_id=uuid.uuid4(),
            node_invocation_id=uuid.uuid4(),
        )
        lock_spec = replace(
            spec,
            context=lock_context,
            effect_input_digest="f" * 64,
        )
        expiring_claim = repository.acquire(
            lock_spec,
            claim_owner="deadline-worker",
            claim_ttl=timedelta(seconds=0.5),
            allow_retry=True,
            now=datetime.now(timezone.utc),
        ).record
        lock_session = session_factory()
        try:
            lock_session.execute(
                text(
                    "SELECT id FROM workflow_node_effect_attempts "
                    "WHERE id = :attempt_id FOR UPDATE"
                ),
                {"attempt_id": expiring_claim.id},
            ).scalar_one()
            with ThreadPoolExecutor(max_workers=1) as executor:
                blocked = executor.submit(
                    repository.mark_in_flight,
                    expiring_claim,
                    now=datetime.now(timezone.utc),
                )
                time.sleep(0.7)
                lock_session.commit()
                with pytest.raises(RuntimeError, match="stale effect claim"):
                    blocked.result(timeout=10)
        finally:
            lock_session.rollback()
            lock_session.close()

        finish_lock_spec = replace(
            spec,
            context=replace(
                context,
                execution_id=uuid.uuid4(),
                node_invocation_id=uuid.uuid4(),
            ),
            effect_input_digest="0" * 64,
        )
        finish_claim = repository.acquire(
            finish_lock_spec,
            claim_owner="finish-deadline-worker",
            claim_ttl=timedelta(seconds=0.8),
            allow_retry=True,
            now=datetime.now(timezone.utc),
        ).record
        finish_in_flight = repository.mark_in_flight(
            finish_claim,
            now=datetime.now(timezone.utc),
        )
        finish_lock_session = session_factory()
        try:
            finish_lock_session.execute(
                text(
                    "SELECT id FROM workflow_node_effect_attempts "
                    "WHERE id = :attempt_id FOR UPDATE"
                ),
                {"attempt_id": finish_in_flight.id},
            ).scalar_one()
            with ThreadPoolExecutor(max_workers=1) as executor:
                blocked_finish = executor.submit(
                    repository.finish,
                    finish_in_flight,
                    outcome=EffectOutcome.SUCCEEDED,
                    replay_decision=ReplayDecision.RESULT_UNAVAILABLE,
                    replay_result=None,
                    provider_status_code=200,
                    error_code=None,
                    now=datetime.now(timezone.utc),
                )
                time.sleep(1.0)
                finish_lock_session.commit()
                with pytest.raises(RuntimeError, match="stale effect claim"):
                    blocked_finish.result(timeout=10)
        finally:
            finish_lock_session.rollback()
            finish_lock_session.close()

        drift_context = ExternalEffectContext(
            organization_id=uuid.uuid4(),
            app_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            execution_id=uuid.uuid4(),
            node_invocation_id=uuid.uuid4(),
            node_id="effect-1",
        )
        github_profile = contracts.get(
            "github",
            "github.issue_comment.create",
            "github.issue_comment.create.v1",
        )
        drift_specs = (
            EffectAttemptSpec(
                context=drift_context,
                profile=profile,
                effect_sequence=0,
                effect_input_digest="b" * 64,
                replay_deadline_at=None,
                key_version=None,
                key_format_version=None,
                idempotency_key_fingerprint=None,
            ),
            EffectAttemptSpec(
                context=drift_context,
                profile=github_profile,
                effect_sequence=0,
                effect_input_digest="c" * 64,
                replay_deadline_at=None,
                key_version=None,
                key_format_version=None,
                idempotency_key_fingerprint=None,
            ),
        )
        drift_barrier = Barrier(2)

        def acquire_drift(index: int):
            drift_barrier.wait(timeout=10)
            return repository.acquire(
                drift_specs[index],
                claim_owner=f"drift-{index}",
                claim_ttl=timedelta(seconds=630),
                allow_retry=True,
                now=datetime.now(timezone.utc),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            drift_outcomes = list(executor.map(acquire_drift, range(2)))
        assert {result.kind for result in drift_outcomes} == {
            AcquireKind.CLAIMED,
            AcquireKind.IDENTITY_CONFLICT,
        }

        key_context = ExternalEffectContext(
            organization_id=uuid.uuid4(),
            app_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            execution_id=uuid.uuid4(),
            node_invocation_id=uuid.uuid4(),
            node_id="fake-1",
        )
        fake_profile = contracts.get(
            "fake",
            "fake.create_effect",
            "fake.create_effect.v1",
        )
        key_specs = tuple(
            EffectAttemptSpec(
                context=key_context,
                profile=fake_profile,
                effect_sequence=0,
                effect_input_digest="d" * 64,
                replay_deadline_at=None,
                key_version=version,
                key_format_version="hmac-b64url-v1",
                idempotency_key_fingerprint=fingerprint * 64,
            )
            for version, fingerprint in (("v1", "1"), ("v2", "2"))
        )
        key_barrier = Barrier(2)

        def acquire_key(index: int):
            key_barrier.wait(timeout=10)
            return repository.acquire(
                key_specs[index],
                claim_owner=f"key-{index}",
                claim_ttl=timedelta(seconds=630),
                allow_retry=True,
                now=datetime.now(timezone.utc),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            key_outcomes = list(executor.map(acquire_key, range(2)))
        assert sorted(result.kind for result in key_outcomes) == [
            AcquireKind.CLAIMED,
            AcquireKind.WAIT,
        ]
        assert len({result.record.spec.key_version for result in key_outcomes}) == 1
        assert (
            len(
                {
                    result.record.spec.idempotency_key_fingerprint
                    for result in key_outcomes
                }
            )
            == 1
        )

        expired_context = ExternalEffectContext(
            organization_id=uuid.uuid4(),
            app_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            execution_id=uuid.uuid4(),
            node_invocation_id=uuid.uuid4(),
            node_id="expired-prepared",
        )
        expired_spec = EffectAttemptSpec(
            context=expired_context,
            profile=profile,
            effect_sequence=0,
            effect_input_digest="e" * 64,
            replay_deadline_at=None,
            key_version=None,
            key_format_version=None,
            idempotency_key_fingerprint=None,
        )
        expired_initial = repository.acquire(
            expired_spec,
            claim_owner="expired-initial",
            claim_ttl=timedelta(seconds=630),
            allow_retry=True,
            now=datetime.now(timezone.utc),
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE workflow_node_effect_attempts "
                    "SET claim_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                    "WHERE id = CAST(:attempt_id AS uuid)"
                ),
                {"attempt_id": str(expired_initial.record.id)},
            )
        expired_barrier = Barrier(2)

        def reacquire_expired(index: int):
            expired_barrier.wait(timeout=10)
            return repository.acquire(
                expired_spec,
                claim_owner=f"expired-{index}",
                claim_ttl=timedelta(seconds=630),
                allow_retry=True,
                now=datetime.now(timezone.utc),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            expired_outcomes = list(executor.map(reacquire_expired, range(2)))
        assert sorted(result.kind for result in expired_outcomes) == [
            AcquireKind.CLAIMED,
            AcquireKind.WAIT,
        ]
        assert {result.record.claim_generation for result in expired_outcomes} == {
            expired_initial.record.claim_generation + 1
        }

        replay_context = ExternalEffectContext(
            organization_id=uuid.uuid4(),
            app_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            execution_id=uuid.uuid4(),
            node_invocation_id=uuid.uuid4(),
            node_id="replay-window",
        )
        replay_spec = EffectAttemptSpec(
            context=replay_context,
            profile=fake_profile,
            effect_sequence=0,
            effect_input_digest="f" * 64,
            replay_deadline_at=None,
            key_version="v1",
            key_format_version="hmac-b64url-v1",
            idempotency_key_fingerprint="3" * 64,
        )
        replay_claimed = repository.acquire(
            replay_spec,
            claim_owner="replay-initial",
            claim_ttl=timedelta(seconds=630),
            allow_retry=True,
            now=datetime.now(timezone.utc),
        )
        replay_in_flight = repository.mark_in_flight(
            replay_claimed.record,
            now=datetime.now(timezone.utc),
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE workflow_node_effect_attempts "
                    "SET claim_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second', "
                    "replay_deadline_at = CURRENT_TIMESTAMP + INTERVAL '1 hour' "
                    "WHERE id = CAST(:attempt_id AS uuid)"
                ),
                {"attempt_id": str(replay_in_flight.id)},
            )
        recovered = repository.acquire(
            replay_spec,
            claim_owner="replay-recovery",
            claim_ttl=timedelta(seconds=630),
            allow_retry=True,
            now=datetime.now(timezone.utc),
        )
        assert recovered.kind is AcquireKind.TERMINAL
        assert recovered.record.outcome is EffectOutcome.EFFECT_OUTCOME_UNKNOWN
        assert recovered.record.replay_decision is ReplayDecision.REPLAY_SAME_KEY
        replay_reopened = repository.acquire(
            replay_spec,
            claim_owner="replay-next-delivery",
            claim_ttl=timedelta(seconds=630),
            allow_retry=True,
            now=datetime.now(timezone.utc),
        )
        assert replay_reopened.kind is AcquireKind.CLAIMED

        expired_deadline_context = ExternalEffectContext(
            organization_id=uuid.uuid4(),
            app_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            execution_id=uuid.uuid4(),
            node_invocation_id=uuid.uuid4(),
            node_id="expired-replay-window",
        )
        expired_deadline_spec = EffectAttemptSpec(
            context=expired_deadline_context,
            profile=fake_profile,
            effect_sequence=0,
            effect_input_digest="1" * 64,
            replay_deadline_at=None,
            key_version="v1",
            key_format_version="hmac-b64url-v1",
            idempotency_key_fingerprint="4" * 64,
        )
        expired_deadline_claimed = repository.acquire(
            expired_deadline_spec,
            claim_owner="expired-deadline-initial",
            claim_ttl=timedelta(seconds=630),
            allow_retry=True,
            now=datetime.now(timezone.utc),
        )
        expired_deadline_in_flight = repository.mark_in_flight(
            expired_deadline_claimed.record,
            now=datetime.now(timezone.utc),
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE workflow_node_effect_attempts "
                    "SET claim_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second', "
                    "replay_deadline_at = CURRENT_TIMESTAMP "
                    "WHERE id = CAST(:attempt_id AS uuid)"
                ),
                {"attempt_id": str(expired_deadline_in_flight.id)},
            )
        deadline_recovered = repository.acquire(
            expired_deadline_spec,
            claim_owner="expired-deadline-recovery",
            claim_ttl=timedelta(seconds=630),
            allow_retry=True,
            now=datetime.now(timezone.utc),
        )
        assert deadline_recovered.kind is AcquireKind.TERMINAL
        assert deadline_recovered.record.replay_decision is ReplayDecision.STOP

        lock_deadline_spec = replace(
            replay_spec,
            context=replace(
                replay_context,
                execution_id=uuid.uuid4(),
                node_invocation_id=uuid.uuid4(),
                node_id="locked-replay-deadline",
            ),
            effect_input_digest="2" * 64,
            idempotency_key_fingerprint="5" * 64,
        )
        lock_deadline_claimed = repository.acquire(
            lock_deadline_spec,
            claim_owner="locked-deadline-initial",
            claim_ttl=timedelta(seconds=630),
            allow_retry=True,
            now=datetime.now(timezone.utc),
        ).record
        lock_deadline_in_flight = repository.mark_in_flight(
            lock_deadline_claimed,
            now=datetime.now(timezone.utc),
        )
        lock_deadline_terminal = repository.finish(
            lock_deadline_in_flight,
            outcome=EffectOutcome.EFFECT_OUTCOME_UNKNOWN,
            replay_decision=ReplayDecision.REPLAY_SAME_KEY,
            replay_result=None,
            provider_status_code=None,
            error_code="response_lost",
            now=datetime.now(timezone.utc),
        )
        lock_deadline_session = session_factory()
        try:
            lock_deadline_session.execute(
                text(
                    "UPDATE workflow_node_effect_attempts "
                    "SET replay_deadline_at = clock_timestamp() + INTERVAL '0.5 second' "
                    "WHERE id = :attempt_id"
                ),
                {"attempt_id": lock_deadline_terminal.id},
            )
            lock_deadline_session.execute(
                text(
                    "SELECT id FROM workflow_node_effect_attempts "
                    "WHERE id = :attempt_id FOR UPDATE"
                ),
                {"attempt_id": lock_deadline_terminal.id},
            ).scalar_one()
            with ThreadPoolExecutor(max_workers=1) as executor:
                blocked_reopen = executor.submit(
                    repository.acquire,
                    lock_deadline_spec,
                    claim_owner="locked-deadline-reopen",
                    claim_ttl=timedelta(seconds=630),
                    allow_retry=True,
                    now=datetime.now(timezone.utc),
                )
                time.sleep(0.7)
                lock_deadline_session.commit()
                deadline_result = blocked_reopen.result(timeout=10)
        finally:
            lock_deadline_session.rollback()
            lock_deadline_session.close()
        assert deadline_result.kind is AcquireKind.TERMINAL
        assert deadline_result.record.replay_decision is ReplayDecision.STOP

    finally:
        if engine is not None:
            engine.dispose()
        if database_created:
            try:
                with admin_engine.connect() as connection:
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


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL external-effect evidence",
)
def test_external_effect_revision_downgrades_after_attempts_are_removed():
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

        extension_engine = create_engine(
            config.database_url(database),
            isolation_level="AUTOCOMMIT",
        )
        try:
            with extension_engine.connect() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        finally:
            extension_engine.dispose()

        _run_alembic(database, config, "upgrade", EXTERNAL_EFFECT_REVISION)
        _run_alembic(
            database,
            config,
            "downgrade",
            EXTERNAL_EFFECT_MIGRATION_PARENT_REVISION,
        )
        downgraded_engine = create_engine(config.database_url(database))
        try:
            with downgraded_engine.connect() as connection:
                table_exists = connection.execute(
                    text("SELECT to_regclass('workflow_node_effect_attempts')")
                ).scalar_one()
                security_alert_table = connection.execute(
                    text("SELECT to_regclass('security_alerts')")
                ).scalar_one()
                security_alert_audit_table = connection.execute(
                    text("SELECT to_regclass('security_alert_audit_events')")
                ).scalar_one()
            assert table_exists is None
            assert security_alert_table == "security_alerts"
            assert security_alert_audit_table == "security_alert_audit_events"
        finally:
            downgraded_engine.dispose()
    finally:
        if database_created:
            try:
                with admin_engine.connect() as connection:
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
