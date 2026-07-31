"""Opt-in PostgreSQL integration tests for Security Alert persistence."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from apps.log_system import audit_tasks
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.security_alert import SecurityAlert
from apps.shared.services.security_alert_aggregation import (
    aggregate_security_alert_detection,
)
from apps.shared.services.security_alert_evidence import link_security_alert_evidence
from apps.shared.services.security_alert_lifecycle import (
    SecurityAlertStaleStateError,
    acknowledge_security_alert,
    reopen_security_alert,
    resolve_security_alert,
)
from apps.shared.services.security_alert_notification_outbox import (
    SecurityAlertNotificationOutboxService,
)
from apps.shared.services.security_alert_rule_evaluator import (
    build_security_alert_detection_key,
)
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_SECURITY_ALERT_DB_TEST"
DB_PREFIX = "mbased_security_alert"


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_concurrent_notification_outbox_enqueue_is_idempotent_in_postgres():
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        try:
            _enable_vector_extension(database, config)
            _run_alembic(database, config)
            now = datetime.now(timezone.utc)
            user_id = uuid.uuid4()
            organization_id = uuid.uuid4()
            idempotency_key = "audit:concurrent:notifications.changed"
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, name, social_provider, created_at, updated_at) "
                        "VALUES (:id, :email, 'Manager', 'local', :now, :now)"
                    ),
                    {
                        "id": user_id,
                        "email": f"outbox-race-{user_id}@example.invalid",
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO organization "
                        "(id, name, options, flags, created_by, is_active, "
                        "created_at, updated_at) VALUES "
                        "(:id, 'Outbox Race', '{}'::jsonb, 0, :created_by, "
                        "true, :now, :now)"
                    ),
                    {
                        "id": organization_id,
                        "created_by": user_id,
                        "now": now,
                    },
                )

            barrier = Barrier(2)

            class SynchronizedOutboxService(
                SecurityAlertNotificationOutboxService
            ):
                def __init__(self, db):
                    super().__init__(db)
                    self.find_calls = 0

                def _find_existing(self, **kwargs):
                    existing = super()._find_existing(**kwargs)
                    self.find_calls += 1
                    if self.find_calls == 1:
                        barrier.wait(timeout=10)
                    return existing

            def enqueue_once():
                with Session(engine) as session:
                    event = SynchronizedOutboxService(session).enqueue(
                        organization_id=organization_id,
                        idempotency_key=idempotency_key,
                    )
                    session.commit()
                    return event.id

            with ThreadPoolExecutor(max_workers=2) as executor:
                event_ids = list(executor.map(lambda _: enqueue_once(), range(2)))

            assert event_ids[0] == event_ids[1]
            with engine.connect() as connection:
                count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_notification_outbox "
                        "WHERE organization_id = :organization_id "
                        "AND idempotency_key = :idempotency_key"
                    ),
                    {
                        "organization_id": organization_id,
                        "idempotency_key": idempotency_key,
                    },
                ).scalar_one()
            assert count == 1
        finally:
            engine.dispose()


def _run_alembic(
    database: str,
    config: DisposablePostgresConfig,
    *,
    target_revision: str = "heads",
) -> None:
    _run_alembic_command(database, config, "upgrade", target_revision)


def _run_alembic_command(
    database: str,
    config: DisposablePostgresConfig,
    *args: str,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "apps/shared/alembic.ini",
            *args,
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
        pytest.fail("Security Alert migration command failed; output omitted")


def _enable_vector_extension(
    database: str,
    config: DisposablePostgresConfig,
) -> None:
    engine = create_engine(config.database_url(database), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    finally:
        engine.dispose()


def _assert_integrity_error(engine, statement: str, parameters: dict) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(text(statement), parameters)
        finally:
            transaction.rollback()


def _seed_evidence_race_rows(
    engine,
    *,
    manager_id,
    organization_id,
    alert_id,
    audit_events,
    now,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, name, social_provider, created_at, updated_at) "
                "VALUES (:id, :email, 'Manager', 'local', :now, :now)"
            ),
            {
                "id": manager_id,
                "email": f"security-alert-evidence-race-{manager_id}@example.invalid",
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO organization "
                "(id, name, options, flags, created_by, is_active, "
                "created_at, updated_at) VALUES "
                "(:id, 'Evidence Race', '{}'::jsonb, 0, :created_by, "
                "true, :now, :now)"
            ),
            {
                "id": organization_id,
                "created_by": manager_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO security_alerts "
                "(id, organization_id, subject_actor_id, rule_id, "
                "rule_version, severity, status, detection_key, "
                "occurrence_count, first_detected_at, last_detected_at, "
                "lifecycle_version) VALUES "
                "(:id, :organization_id, :subject_actor_id, "
                "'repeated_permission_denied', 'v1', 'medium', 'open', "
                ":detection_key, 0, :now, :now, 1)"
            ),
            {
                "id": alert_id,
                "organization_id": organization_id,
                "subject_actor_id": uuid.uuid4(),
                "detection_key": f"evidence-race:{alert_id}",
                "now": now,
            },
        )
        for audit_log_id, occurred_at in audit_events:
            connection.execute(
                text(
                    "INSERT INTO audit_logs "
                    "(id, occurred_at, actor_id, actor_type, category, action, "
                    "status, audit_metadata) VALUES "
                    "(:id, :occurred_at, :actor_id, 'user', 'action', "
                    "'permission.denied', 'failure', "
                    "jsonb_build_object('organization_id', :organization_id))"
                ),
                {
                    "id": audit_log_id,
                    "occurred_at": occurred_at,
                    "actor_id": manager_id,
                    "organization_id": str(organization_id),
                },
            )


def _seed_aggregation_rows(
    engine,
    *,
    user_id,
    organization_id,
    audit_ids,
    now,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, name, social_provider, created_at, updated_at) "
                "VALUES (:id, :email, 'Aggregation Actor', 'local', :now, :now)"
            ),
            {
                "id": user_id,
                "email": f"security-alert-aggregation-{user_id}@example.invalid",
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO organization "
                "(id, name, options, flags, created_by, is_active, "
                "created_at, updated_at) VALUES "
                "(:id, 'Aggregation Organization', '{}'::jsonb, 0, "
                ":created_by, true, :now, :now)"
            ),
            {
                "id": organization_id,
                "created_by": user_id,
                "now": now,
            },
        )
        for index, audit_id in enumerate(audit_ids):
            connection.execute(
                text(
                    "INSERT INTO audit_logs "
                    "(id, occurred_at, actor_id, actor_type, category, action, "
                    "target_type, target_id, status, audit_metadata) VALUES "
                    "(:id, :occurred_at, :actor_id, 'user', 'action', "
                    "'permission.denied', 'workflow', :target_id, 'failure', "
                    "jsonb_build_object('organization_id', :organization_id))"
                ),
                {
                    "id": audit_id,
                    "occurred_at": now + timedelta(seconds=index),
                    "actor_id": user_id,
                    "target_id": str(uuid.uuid4()),
                    "organization_id": str(organization_id),
                },
            )


def _seed_reconciliation_backlog(
    engine,
    *,
    audit_ids,
    occurred_at,
    organization_id,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_logs "
                "(id, occurred_at, actor_id, actor_type, category, action, "
                "status, audit_metadata) VALUES "
                "(:id, :occurred_at, NULL, 'system', 'action', "
                "'reconciliation.test', 'success', "
                "jsonb_build_object('organization_id', :organization_id))"
            ),
            [
                {
                    "id": audit_id,
                    "occurred_at": occurred_at,
                    "organization_id": str(organization_id),
                }
                for audit_id in audit_ids
            ],
        )
        connection.execute(
            text(
                "UPDATE security_alert_reconciliation_watermarks SET "
                "activation_started_at = :activation_started_at, "
                "cursor_occurred_at = NULL, cursor_audit_log_id = NULL, "
                "reconciliation_generation = 0 "
                "WHERE processor_name = 'security-alert-v1'"
            ),
            {"activation_started_at": occurred_at - timedelta(seconds=1)},
        )


def _aggregation_candidate(*, organization_id, actor_id):
    rule_id = "repeated_permission_denied"
    rule_version = "v1"
    return SimpleNamespace(
        organization_id=organization_id,
        subject_actor_id=actor_id,
        rule_id=rule_id,
        rule_version=rule_version,
        severity="medium",
        policy_reason=None,
        detection_key=build_security_alert_detection_key(
            organization_id=organization_id,
            actor_id=actor_id,
            rule_id=rule_id,
            rule_version=rule_version,
        ),
    )


@contextmanager
def _disposable_database(*, target_revision: str = "heads"):
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
        _run_alembic(database, config, target_revision=target_revision)
        yield database, config
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable; connection details omitted",
            pytrace=False,
        ) from None
    finally:
        if database_created:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database AND pid <> pg_backend_pid()"
                    ),
                    {"database": database},
                )
                connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))
        admin_engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_security_alert_migration_creates_real_postgres_schema():
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
        migration_started_at = datetime.now(timezone.utc)
        _run_alembic(database, config)

        engine = create_engine(config.database_url(database))
        try:
            schema = inspect(engine)
            assert {
                "security_alerts",
                "security_alert_audit_events",
                "security_alert_reconciliation_receipts",
                "security_alert_reconciliation_watermarks",
            } <= set(schema.get_table_names())

            receipt_columns = {
                column["name"]: column
                for column in schema.get_columns(
                    "security_alert_reconciliation_receipts"
                )
            }
            assert set(receipt_columns) == {
                "processor_name",
                "audit_log_id",
                "discovered_generation",
                "evaluated_generation",
                "processed_at",
            }
            assert receipt_columns["processor_name"]["nullable"] is False
            assert receipt_columns["audit_log_id"]["nullable"] is False
            assert receipt_columns["discovered_generation"]["nullable"] is False
            assert receipt_columns["evaluated_generation"]["nullable"] is False
            assert receipt_columns["processed_at"]["nullable"] is False

            watermark_columns = {
                column["name"]: column
                for column in schema.get_columns(
                    "security_alert_reconciliation_watermarks"
                )
            }
            assert watermark_columns["processor_name"]["nullable"] is False
            assert watermark_columns["activation_started_at"]["nullable"] is False
            assert watermark_columns["reconciliation_generation"]["nullable"] is False
            with engine.connect() as connection:
                initial_watermark = connection.execute(
                    text(
                        "SELECT processor_name, activation_started_at, "
                        "cursor_occurred_at, cursor_audit_log_id, "
                        "reconciliation_generation "
                        "FROM security_alert_reconciliation_watermarks"
                    )
                ).one()
            assert initial_watermark.processor_name == "security-alert-v1"
            assert initial_watermark.activation_started_at >= migration_started_at
            assert initial_watermark.cursor_occurred_at is None
            assert initial_watermark.cursor_audit_log_id is None
            assert initial_watermark.reconciliation_generation == 0
            watermark_checks = {
                check["name"]
                for check in schema.get_check_constraints(
                    "security_alert_reconciliation_watermarks"
                )
            }
            assert "ck_security_alert_reconciliation_cursor_pair" in watermark_checks
            assert (
                "ck_security_alert_reconcile_generation_nonnegative"
                in watermark_checks
            )
            receipt_checks = {
                check["name"]
                for check in schema.get_check_constraints(
                    "security_alert_reconciliation_receipts"
                )
            }
            assert {
                "ck_security_alert_receipt_generations_nonnegative",
                "ck_security_alert_receipt_evaluation_order",
            } <= receipt_checks

            audit_indexes = {
                index["name"] for index in schema.get_indexes("audit_logs")
            }
            assert "ix_audit_logs_occurred_at_id" in audit_indexes

            evidence_foreign_keys = {
                tuple(foreign_key["constrained_columns"]): foreign_key
                for foreign_key in schema.get_foreign_keys(
                    "security_alert_audit_events"
                )
            }
            assert evidence_foreign_keys[("security_alert_id",)][
                "referred_table"
            ] == "security_alerts"
            assert evidence_foreign_keys[("security_alert_id",)]["options"].get(
                "ondelete"
            ) == "CASCADE"
            assert evidence_foreign_keys[("audit_log_id",)]["referred_table"] == (
                "audit_logs"
            )
            assert evidence_foreign_keys[("audit_log_id",)]["options"].get(
                "ondelete"
            ) == "CASCADE"

            alert_checks = {
                check["name"] for check in schema.get_check_constraints("security_alerts")
            }
            assert "ck_security_alerts_status_fields" in alert_checks
            assert "ck_security_alerts_timestamp_order" in alert_checks
            assert "ck_security_alerts_episode_count_positive" in alert_checks

            alert_columns = {
                column["name"]: column
                for column in schema.get_columns("security_alerts")
            }
            assert alert_columns["episode_count"]["nullable"] is False
            assert alert_columns["last_episode_started_at"]["nullable"] is False

            active_index = next(
                index
                for index in schema.get_indexes("security_alerts")
                if index["name"] == "uq_security_alerts_active_detection_key"
            )
            assert active_index["unique"] is True

            index_prefixes = {
                tuple(index["column_names"][:2])
                for index in schema.get_indexes("security_alerts")
            }
            assert {
                ("organization_id", "status"),
                ("organization_id", "severity"),
                ("organization_id", "rule_id"),
                ("organization_id", "subject_actor_id"),
            } <= index_prefixes
        finally:
            engine.dispose()
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable; connection details omitted",
            pytrace=False,
        ) from None
    finally:
        if database_created:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database AND pid <> pg_backend_pid()"
                    ),
                    {"database": database},
                )
                connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))
        admin_engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_security_alert_downgrade_removes_only_feature_schema_and_keeps_data():
    with _disposable_database(target_revision="b28d9e0f1a32") as (
        database,
        config,
    ):
        engine = create_engine(config.database_url(database))
        user_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        audit_log_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        try:
            with engine.begin() as connection:
                tables_before = set(inspect(connection).get_table_names())
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, name, social_provider, created_at, updated_at) "
                        "VALUES (:id, :email, 'Manager', 'local', :now, :now)"
                    ),
                    {
                        "id": user_id,
                        "email": f"security-alert-downgrade-{user_id}@example.invalid",
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO organization "
                        "(id, name, options, flags, created_by, is_active, "
                        "created_at, updated_at) VALUES "
                        "(:id, 'Security Alert Downgrade', '{}'::jsonb, 0, "
                        ":created_by, true, :now, :now)"
                    ),
                    {
                        "id": organization_id,
                        "created_by": user_id,
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, occurred_at, actor_id, actor_type, category, action, "
                        "status, audit_metadata) VALUES "
                        "(:id, :occurred_at, :actor_id, 'user', 'action', "
                        "'permission.denied', 'failure', "
                        "jsonb_build_object('organization_id', :organization_id))"
                    ),
                    {
                        "id": audit_log_id,
                        "occurred_at": now,
                        "actor_id": user_id,
                        "organization_id": str(organization_id),
                    },
                )

            _run_alembic_command(
                database,
                config,
                "downgrade",
                "fd2e3f4a5b67",
            )

            with engine.connect() as connection:
                tables_after = set(inspect(connection).get_table_names())
                user_count = connection.execute(
                    text("SELECT count(*) FROM users WHERE id = :id"),
                    {"id": user_id},
                ).scalar_one()
                organization_count = connection.execute(
                    text("SELECT count(*) FROM organization WHERE id = :id"),
                    {"id": organization_id},
                ).scalar_one()
                audit_count = connection.execute(
                    text("SELECT count(*) FROM audit_logs WHERE id = :id"),
                    {"id": audit_log_id},
                ).scalar_one()

            assert tables_after == tables_before - {
                "security_alerts",
                "security_alert_audit_events",
                "security_alert_reconciliation_watermarks",
            }
            assert user_count == 1
            assert organization_count == 1
            assert audit_count == 1
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_aggregation_creates_alert_evidence_and_detected_audit_in_postgres():
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        actor_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        audit_ids = tuple(uuid.uuid4() for _ in range(5))
        now = datetime.now(timezone.utc)
        candidate = _aggregation_candidate(
            organization_id=organization_id,
            actor_id=actor_id,
        )

        try:
            _seed_aggregation_rows(
                engine,
                user_id=actor_id,
                organization_id=organization_id,
                audit_ids=audit_ids,
                now=now,
            )

            with Session(engine, autoflush=False) as session:
                audit_logs = (
                    session.query(AuditLog)
                    .filter(AuditLog.id.in_(audit_ids))
                    .order_by(AuditLog.occurred_at, AuditLog.id)
                    .all()
                )
                alert = aggregate_security_alert_detection(
                    session,
                    candidate=candidate,
                    audit_logs=audit_logs,
                    detected_at=now + timedelta(seconds=4),
                )
                session.commit()
                alert_id = alert.id

            with engine.connect() as connection:
                stored = connection.execute(
                    text(
                        "SELECT occurrence_count FROM security_alerts "
                        "WHERE id = :alert_id"
                    ),
                    {"alert_id": alert_id},
                ).one()
                evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id"
                    ),
                    {"alert_id": alert_id},
                ).scalar_one()
                detected_audit_count = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_logs "
                        "WHERE action = 'security_alert.detected' "
                        "AND target_id = :target_id"
                    ),
                    {"target_id": str(alert_id)},
                ).scalar_one()

            assert stored.occurrence_count == 5
            assert evidence_count == 5
            assert detected_audit_count == 1
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_realtime_worker_creates_alert_on_fifth_denial_in_postgres(monkeypatch):
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        actor_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        audit_ids = tuple(uuid.uuid4() for _ in range(5))
        now = datetime.now(timezone.utc)

        try:
            _seed_aggregation_rows(
                engine,
                user_id=actor_id,
                organization_id=organization_id,
                audit_ids=audit_ids,
                now=now,
            )
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE audit_logs SET target_id = :target_id "
                        "WHERE id = ANY(:audit_ids)"
                    ),
                    {
                        "target_id": str(uuid.uuid4()),
                        "audit_ids": list(audit_ids),
                    },
                )
                connection.execute(
                    text(
                        "UPDATE security_alert_reconciliation_watermarks "
                        "SET activation_started_at = :activation_started_at, "
                        "cursor_occurred_at = NULL, cursor_audit_log_id = NULL "
                        "WHERE processor_name = 'security-alert-v1'"
                    ),
                    {"activation_started_at": now - timedelta(seconds=1)},
                )

            monkeypatch.setattr(
                audit_tasks,
                "SessionLocal",
                lambda: Session(engine, autoflush=False),
            )

            fourth_result = audit_tasks.detect_security_alert.run(str(audit_ids[3]))
            with engine.connect() as connection:
                alert_count_after_four = connection.execute(
                    text("SELECT count(*) FROM security_alerts")
                ).scalar_one()

            fifth_result = audit_tasks.detect_security_alert.run(str(audit_ids[4]))
            with engine.connect() as connection:
                alert = connection.execute(
                    text(
                        "SELECT id, occurrence_count FROM security_alerts "
                        "WHERE rule_id = 'repeated_permission_denied'"
                    )
                ).one()
                evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id"
                    ),
                    {"alert_id": alert.id},
                ).scalar_one()
                detected_audit_count = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_logs "
                        "WHERE action = 'security_alert.detected' "
                        "AND target_id = :target_id"
                    ),
                    {"target_id": str(alert.id)},
                ).scalar_one()

            assert fourth_result["candidate_count"] == 0
            assert alert_count_after_four == 0
            assert fifth_result["candidate_count"] == 1
            assert alert.occurrence_count == 5
            assert evidence_count == 5
            assert detected_audit_count == 1
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_reconciliation_recovers_missed_denials_and_advances_cursor_in_postgres(
    monkeypatch,
):
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        actor_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        audit_ids = tuple(uuid.uuid4() for _ in range(5))
        now = datetime.now(timezone.utc)

        try:
            _seed_aggregation_rows(
                engine,
                user_id=actor_id,
                organization_id=organization_id,
                audit_ids=audit_ids,
                now=now,
            )
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE audit_logs SET target_id = :target_id "
                        "WHERE id = ANY(:audit_ids)"
                    ),
                    {
                        "target_id": str(uuid.uuid4()),
                        "audit_ids": list(audit_ids),
                    },
                )
                connection.execute(
                    text(
                        "UPDATE security_alert_reconciliation_watermarks "
                        "SET activation_started_at = :activation_started_at, "
                        "cursor_occurred_at = NULL, cursor_audit_log_id = NULL "
                        "WHERE processor_name = 'security-alert-v1'"
                    ),
                    {"activation_started_at": now - timedelta(seconds=1)},
                )

            monkeypatch.setattr(
                audit_tasks,
                "SessionLocal",
                lambda: Session(engine, autoflush=False),
            )

            result = audit_tasks.reconcile_security_alerts.run()

            with engine.connect() as connection:
                alert = connection.execute(
                    text(
                        "SELECT id, occurrence_count FROM security_alerts "
                        "WHERE rule_id = 'repeated_permission_denied'"
                    )
                ).one()
                evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id"
                    ),
                    {"alert_id": alert.id},
                ).scalar_one()
                watermark = connection.execute(
                    text(
                        "SELECT cursor_occurred_at, cursor_audit_log_id "
                        "FROM security_alert_reconciliation_watermarks "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).one()
                latest_audit = connection.execute(
                    text(
                        "SELECT occurred_at, id FROM audit_logs "
                        "WHERE id = ANY(:audit_ids) "
                        "ORDER BY occurred_at DESC, id DESC LIMIT 1"
                    ),
                    {"audit_ids": list(audit_ids)},
                ).one()

            assert result == {"status": "processed", "processed_count": 5}
            assert alert.occurrence_count == 5
            assert evidence_count == 5
            assert watermark.cursor_occurred_at == latest_audit.occurred_at
            assert watermark.cursor_audit_log_id == latest_audit.id
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_reconciliation_processes_large_backlog_in_bounded_postgres_batches(
    monkeypatch,
):
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        audit_ids = tuple(uuid.UUID(int=index) for index in range(1, 251))
        occurred_at = datetime.now(timezone.utc)

        try:
            _seed_reconciliation_backlog(
                engine,
                audit_ids=audit_ids,
                occurred_at=occurred_at,
                organization_id=uuid.uuid4(),
            )
            monkeypatch.setattr(
                audit_tasks,
                "SessionLocal",
                lambda: Session(engine, autoflush=False),
            )

            results = [
                audit_tasks.reconcile_security_alerts.run() for _ in range(4)
            ]

            with engine.connect() as connection:
                receipt_count = connection.execute(
                    text(
                        "SELECT count(*) "
                        "FROM security_alert_reconciliation_receipts "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).scalar_one()
                watermark = connection.execute(
                    text(
                        "SELECT cursor_occurred_at, cursor_audit_log_id, "
                        "reconciliation_generation "
                        "FROM security_alert_reconciliation_watermarks "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).one()

            assert [result["processed_count"] for result in results] == [
                100,
                100,
                50,
                0,
            ]
            assert receipt_count == 250
            assert watermark.cursor_occurred_at == occurred_at
            assert watermark.cursor_audit_log_id == audit_ids[-1]
            assert watermark.reconciliation_generation == 3
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_reconciliation_retry_keeps_first_committed_batch_in_postgres(
    monkeypatch,
):
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        audit_ids = tuple(uuid.UUID(int=index) for index in range(1, 151))
        occurred_at = datetime.now(timezone.utc)

        try:
            _seed_reconciliation_backlog(
                engine,
                audit_ids=audit_ids,
                occurred_at=occurred_at,
                organization_id=uuid.uuid4(),
            )
            monkeypatch.setattr(
                audit_tasks,
                "SessionLocal",
                lambda: Session(engine, autoflush=False),
            )

            first_result = audit_tasks.reconcile_security_alerts.run()
            original_process = audit_tasks._process_reconciliation_audit
            process_count = 0
            failure = RuntimeError("synthetic bounded batch failure")

            class _RetryRequested(Exception):
                pass

            def fail_in_second_batch(
                db,
                audit,
                *,
                changed_organization_ids=None,
            ):
                nonlocal process_count
                process_count += 1
                if process_count == 10:
                    raise failure
                original_process(
                    db,
                    audit,
                    changed_organization_ids=changed_organization_ids,
                )

            def request_retry(**kwargs):
                raise _RetryRequested from kwargs["exc"]

            monkeypatch.setattr(
                audit_tasks,
                "_process_reconciliation_audit",
                fail_in_second_batch,
            )
            monkeypatch.setattr(
                audit_tasks.reconcile_security_alerts,
                "retry",
                request_retry,
            )

            with pytest.raises(_RetryRequested):
                audit_tasks.reconcile_security_alerts.run()

            with engine.connect() as connection:
                receipt_count_after_failure = connection.execute(
                    text(
                        "SELECT count(*) "
                        "FROM security_alert_reconciliation_receipts "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).scalar_one()
                watermark_after_failure = connection.execute(
                    text(
                        "SELECT cursor_audit_log_id, reconciliation_generation "
                        "FROM security_alert_reconciliation_watermarks "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).one()

            monkeypatch.setattr(
                audit_tasks,
                "_process_reconciliation_audit",
                original_process,
            )
            retry_result = audit_tasks.reconcile_security_alerts.run()

            with engine.connect() as connection:
                final_receipt_count = connection.execute(
                    text(
                        "SELECT count(*) "
                        "FROM security_alert_reconciliation_receipts "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).scalar_one()
                final_generation = connection.execute(
                    text(
                        "SELECT reconciliation_generation "
                        "FROM security_alert_reconciliation_watermarks "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).scalar_one()

            assert first_result["processed_count"] == 100
            assert receipt_count_after_failure == 100
            assert watermark_after_failure.cursor_audit_log_id == audit_ids[99]
            assert watermark_after_failure.reconciliation_generation == 1
            assert retry_result["processed_count"] == 50
            assert final_receipt_count == 150
            assert final_generation == 2
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_reconciliation_recovers_late_arrival_after_worker_restart_in_postgres(
    monkeypatch,
):
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        actor_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        recent_audit_id = uuid.uuid4()
        late_audit_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        try:
            _seed_aggregation_rows(
                engine,
                user_id=actor_id,
                organization_id=organization_id,
                audit_ids=(recent_audit_id,),
                now=now,
            )
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE security_alert_reconciliation_watermarks "
                        "SET activation_started_at = :activation_started_at, "
                        "cursor_occurred_at = NULL, cursor_audit_log_id = NULL "
                        "WHERE processor_name = 'security-alert-v1'"
                    ),
                    {"activation_started_at": now - timedelta(minutes=20)},
                )

            monkeypatch.setattr(
                audit_tasks,
                "SessionLocal",
                lambda: Session(engine, autoflush=False),
            )
            monkeypatch.setattr(
                audit_tasks,
                "_SECURITY_ALERT_RECONCILIATION_BATCH_SIZE",
                1,
            )

            first_result = audit_tasks.reconcile_security_alerts.run()

            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, occurred_at, actor_id, actor_type, category, action, "
                        "target_type, target_id, status, audit_metadata) VALUES "
                        "(:id, :occurred_at, :actor_id, 'user', 'action', "
                        "'permission.denied', 'workflow', :target_id, 'failure', "
                        "jsonb_build_object('organization_id', :organization_id))"
                    ),
                    {
                        "id": late_audit_id,
                        "occurred_at": now - timedelta(minutes=10),
                        "actor_id": actor_id,
                        "target_id": str(uuid.uuid4()),
                        "organization_id": str(organization_id),
                    },
                )

            second_result = audit_tasks.reconcile_security_alerts.run()
            third_result = audit_tasks.reconcile_security_alerts.run()
            fourth_result = audit_tasks.reconcile_security_alerts.run()

            with engine.connect() as connection:
                receipts = {
                    row.audit_log_id: (
                        row.discovered_generation,
                        row.evaluated_generation,
                    )
                    for row in
                    connection.execute(
                        text(
                            "SELECT audit_log_id, discovered_generation, "
                            "evaluated_generation "
                            "FROM security_alert_reconciliation_receipts "
                            "WHERE processor_name = 'security-alert-v1'"
                        )
                    )
                }

            assert first_result == {"status": "processed", "processed_count": 1}
            assert second_result == {"status": "processed", "processed_count": 1}
            assert third_result == {"status": "processed", "processed_count": 1}
            assert fourth_result == {"status": "processed", "processed_count": 0}
            assert receipts == {
                recent_audit_id: (1, 3),
                late_audit_id: (2, 2),
            }
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_reconciliation_replays_following_event_when_late_audit_reaches_threshold(
    monkeypatch,
):
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        actor_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        initial_audit_ids = tuple(uuid.uuid4() for _ in range(4))
        late_audit_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        try:
            _seed_aggregation_rows(
                engine,
                user_id=actor_id,
                organization_id=organization_id,
                audit_ids=initial_audit_ids,
                now=now,
            )
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE audit_logs SET occurred_at = :occurred_at "
                        "WHERE id = :audit_id"
                    ),
                    {
                        "audit_id": initial_audit_ids[-1],
                        "occurred_at": now + timedelta(seconds=4),
                    },
                )
                connection.execute(
                    text(
                        "UPDATE security_alert_reconciliation_watermarks "
                        "SET activation_started_at = :activation_started_at, "
                        "cursor_occurred_at = NULL, cursor_audit_log_id = NULL "
                        "WHERE processor_name = 'security-alert-v1'"
                    ),
                    {"activation_started_at": now - timedelta(seconds=1)},
                )

            monkeypatch.setattr(
                audit_tasks,
                "SessionLocal",
                lambda: Session(engine, autoflush=False),
            )

            first_result = audit_tasks.reconcile_security_alerts.run()

            with engine.begin() as connection:
                first_alert_count = connection.execute(
                    text("SELECT count(*) FROM security_alerts")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, occurred_at, actor_id, actor_type, category, action, "
                        "target_type, target_id, status, audit_metadata) VALUES "
                        "(:id, :occurred_at, :actor_id, 'user', 'action', "
                        "'permission.denied', 'workflow', :target_id, 'failure', "
                        "jsonb_build_object('organization_id', :organization_id))"
                    ),
                    {
                        "id": late_audit_id,
                        "occurred_at": now + timedelta(seconds=3),
                        "actor_id": actor_id,
                        "target_id": str(uuid.uuid4()),
                        "organization_id": str(organization_id),
                    },
                )

            second_result = audit_tasks.reconcile_security_alerts.run()
            third_result = audit_tasks.reconcile_security_alerts.run()
            fourth_result = audit_tasks.reconcile_security_alerts.run()

            with engine.connect() as connection:
                alert = connection.execute(
                    text(
                        "SELECT id, occurrence_count FROM security_alerts "
                        "WHERE rule_id = 'repeated_permission_denied'"
                    )
                ).one()
                evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id"
                    ),
                    {"alert_id": alert.id},
                ).scalar_one()

            assert first_result == {"status": "processed", "processed_count": 4}
            assert first_alert_count == 0
            assert second_result == {"status": "processed", "processed_count": 2}
            assert third_result == {"status": "processed", "processed_count": 2}
            assert fourth_result == {"status": "processed", "processed_count": 0}
            assert alert.occurrence_count == 5
            assert evidence_count == 5
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_reconciliation_overlap_uuid_cursor_and_activation_boundary_in_postgres(
    monkeypatch,
):
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        actor_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        audit_ids = tuple(uuid.UUID(int=index) for index in range(1, 7))
        activation_started_at = datetime.now(timezone.utc)

        try:
            _seed_aggregation_rows(
                engine,
                user_id=actor_id,
                organization_id=organization_id,
                audit_ids=audit_ids,
                now=activation_started_at,
            )
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE audit_logs SET occurred_at = :before "
                        "WHERE id = :before_id"
                    ),
                    {
                        "before": activation_started_at - timedelta(seconds=1),
                        "before_id": audit_ids[0],
                    },
                )
                connection.execute(
                    text(
                        "UPDATE audit_logs SET occurred_at = :at_activation, "
                        "target_id = :target_id WHERE id = ANY(:audit_ids)"
                    ),
                    {
                        "at_activation": activation_started_at,
                        "target_id": str(uuid.uuid4()),
                        "audit_ids": list(audit_ids[1:]),
                    },
                )
                connection.execute(
                    text(
                        "UPDATE security_alert_reconciliation_watermarks SET "
                        "activation_started_at = :activation_started_at, "
                        "cursor_occurred_at = :cursor_occurred_at, "
                        "cursor_audit_log_id = :cursor_audit_log_id "
                        "WHERE processor_name = 'security-alert-v1'"
                    ),
                    {
                        "activation_started_at": activation_started_at,
                        "cursor_occurred_at": activation_started_at,
                        "cursor_audit_log_id": audit_ids[3],
                    },
                )

            monkeypatch.setattr(
                audit_tasks,
                "SessionLocal",
                lambda: Session(engine, autoflush=False),
            )

            first_result = audit_tasks.reconcile_security_alerts.run()
            with engine.connect() as connection:
                first_alert = connection.execute(
                    text(
                        "SELECT id, occurrence_count FROM security_alerts "
                        "WHERE rule_id = 'repeated_permission_denied'"
                    )
                ).one()
                first_evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id"
                    ),
                    {"alert_id": first_alert.id},
                ).scalar_one()
                first_cursor = connection.execute(
                    text(
                        "SELECT cursor_occurred_at, cursor_audit_log_id "
                        "FROM security_alert_reconciliation_watermarks "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).one()

            second_result = audit_tasks.reconcile_security_alerts.run()
            with engine.connect() as connection:
                second_alert = connection.execute(
                    text(
                        "SELECT occurrence_count FROM security_alerts "
                        "WHERE id = :alert_id"
                    ),
                    {"alert_id": first_alert.id},
                ).one()
                second_evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id"
                    ),
                    {"alert_id": first_alert.id},
                ).scalar_one()
                before_evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE audit_log_id = :before_id"
                    ),
                    {"before_id": audit_ids[0]},
                ).scalar_one()
                processed_receipt_count = connection.execute(
                    text(
                        "SELECT count(*) "
                        "FROM security_alert_reconciliation_receipts "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).scalar_one()

            assert first_result["processed_count"] == 5
            assert first_alert.occurrence_count == 5
            assert first_evidence_count == 5
            assert first_cursor.cursor_occurred_at == activation_started_at
            assert first_cursor.cursor_audit_log_id == audit_ids[-1]
            assert second_result["processed_count"] == 1
            assert second_alert.occurrence_count == 5
            assert second_evidence_count == 5
            assert before_evidence_count == 0
            assert processed_receipt_count == 6
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_reconciliation_failure_rolls_back_alert_cursor_then_retries_once(
    monkeypatch,
):
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        actor_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        audit_ids = tuple(uuid.uuid4() for _ in range(6))
        now = datetime.now(timezone.utc)

        try:
            _seed_aggregation_rows(
                engine,
                user_id=actor_id,
                organization_id=organization_id,
                audit_ids=audit_ids,
                now=now,
            )
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE audit_logs SET target_id = :target_id "
                        "WHERE id = ANY(:audit_ids)"
                    ),
                    {
                        "target_id": str(uuid.uuid4()),
                        "audit_ids": list(audit_ids),
                    },
                )
                connection.execute(
                    text(
                        "UPDATE security_alert_reconciliation_watermarks "
                        "SET activation_started_at = :activation_started_at, "
                        "cursor_occurred_at = NULL, cursor_audit_log_id = NULL "
                        "WHERE processor_name = 'security-alert-v1'"
                    ),
                    {"activation_started_at": now - timedelta(seconds=1)},
                )

            monkeypatch.setattr(
                audit_tasks,
                "SessionLocal",
                lambda: Session(engine, autoflush=False),
            )
            original_process = audit_tasks._process_reconciliation_audit
            process_count = 0
            failure = RuntimeError("synthetic reconciliation failure")
            retries = []

            class _RetryRequested(Exception):
                pass

            def fail_after_alert(db, audit):
                nonlocal process_count
                process_count += 1
                if process_count == 6:
                    raise failure
                original_process(db, audit)

            def request_retry(**kwargs):
                retries.append(kwargs)
                raise _RetryRequested

            monkeypatch.setattr(
                audit_tasks,
                "_process_reconciliation_audit",
                fail_after_alert,
            )
            monkeypatch.setattr(
                audit_tasks.reconcile_security_alerts,
                "retry",
                request_retry,
            )

            with pytest.raises(_RetryRequested):
                audit_tasks.reconcile_security_alerts.run()

            with engine.connect() as connection:
                rolled_back_alerts = connection.execute(
                    text("SELECT count(*) FROM security_alerts")
                ).scalar_one()
                rolled_back_evidence = connection.execute(
                    text("SELECT count(*) FROM security_alert_audit_events")
                ).scalar_one()
                rolled_back_detected_audits = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_logs "
                        "WHERE action = 'security_alert.detected'"
                    )
                ).scalar_one()
                rolled_back_cursor = connection.execute(
                    text(
                        "SELECT cursor_occurred_at, cursor_audit_log_id "
                        "FROM security_alert_reconciliation_watermarks "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).one()
                rolled_back_receipts = connection.execute(
                    text(
                        "SELECT count(*) "
                        "FROM security_alert_reconciliation_receipts "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).scalar_one()

            assert len(retries) == 1
            assert retries[0]["countdown"] == 1
            assert retries[0]["exc"] is not failure
            assert str(failure) not in str(retries[0]["exc"])
            assert rolled_back_alerts == 0
            assert rolled_back_evidence == 0
            assert rolled_back_detected_audits == 0
            assert rolled_back_cursor.cursor_occurred_at is None
            assert rolled_back_cursor.cursor_audit_log_id is None
            assert rolled_back_receipts == 0

            monkeypatch.setattr(
                audit_tasks,
                "_process_reconciliation_audit",
                original_process,
            )
            retry_result = audit_tasks.reconcile_security_alerts.run()

            with engine.connect() as connection:
                stored_alert = connection.execute(
                    text(
                        "SELECT id, occurrence_count FROM security_alerts "
                        "WHERE rule_id = 'repeated_permission_denied'"
                    )
                ).one()
                stored_evidence = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id"
                    ),
                    {"alert_id": stored_alert.id},
                ).scalar_one()
                stored_detected_audits = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_logs "
                        "WHERE action = 'security_alert.detected'"
                    )
                ).scalar_one()
                stored_receipts = connection.execute(
                    text(
                        "SELECT count(*) "
                        "FROM security_alert_reconciliation_receipts "
                        "WHERE processor_name = 'security-alert-v1'"
                    )
                ).scalar_one()

            assert retry_result["processed_count"] == 6
            assert stored_alert.occurrence_count == 6
            assert stored_evidence == 6
            assert stored_detected_audits == 1
            assert stored_receipts == 6
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_concurrent_same_detection_key_reuses_partial_unique_winner():
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        actor_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        audit_ids = tuple(uuid.uuid4() for _ in range(5))
        now = datetime.now(timezone.utc)
        candidate = _aggregation_candidate(
            organization_id=organization_id,
            actor_id=actor_id,
        )
        ready = Barrier(2)

        try:
            _seed_aggregation_rows(
                engine,
                user_id=actor_id,
                organization_id=organization_id,
                audit_ids=audit_ids,
                now=now,
            )

            def aggregate_once():
                with Session(engine, autoflush=False) as session:
                    session.connection(
                        execution_options={"isolation_level": "READ COMMITTED"}
                    )
                    audit_logs = (
                        session.query(AuditLog)
                        .filter(AuditLog.id.in_(audit_ids))
                        .order_by(AuditLog.occurred_at, AuditLog.id)
                        .all()
                    )
                    assert (
                        session.query(SecurityAlert)
                        .filter(
                            SecurityAlert.detection_key
                            == candidate.detection_key,
                            SecurityAlert.status.in_(("open", "acknowledged")),
                        )
                        .count()
                        == 0
                    )
                    ready.wait(timeout=5)
                    try:
                        alert = aggregate_security_alert_detection(
                            session,
                            candidate=candidate,
                            audit_logs=audit_logs,
                            detected_at=now + timedelta(seconds=4),
                        )
                        session.commit()
                        return alert.id
                    except IntegrityError:
                        session.rollback()
                        return "integrity_error"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: aggregate_once(), range(2)))

            with engine.connect() as connection:
                alert_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alerts "
                        "WHERE detection_key = :detection_key "
                        "AND status IN ('open', 'acknowledged')"
                    ),
                    {"detection_key": candidate.detection_key},
                ).scalar_one()
                evidence_count = connection.execute(
                    text("SELECT count(*) FROM security_alert_audit_events")
                ).scalar_one()
                detected_audit_count = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_logs "
                        "WHERE action = 'security_alert.detected'"
                    )
                ).scalar_one()

            assert "integrity_error" not in results
            assert len(set(results)) == 1
            assert alert_count == 1
            assert evidence_count == 5
            assert detected_audit_count == 1
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_concurrent_acknowledge_has_one_database_winner_and_one_audit():
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        user_ids = (uuid.uuid4(), uuid.uuid4())
        organization_id = uuid.uuid4()
        alert_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        try:
            with engine.begin() as connection:
                for index, user_id in enumerate(user_ids):
                    connection.execute(
                        text(
                            "INSERT INTO users "
                            "(id, email, name, social_provider, created_at, updated_at) "
                            "VALUES (:id, :email, :name, 'local', :now, :now)"
                        ),
                        {
                            "id": user_id,
                            "email": f"security-alert-{index}-{user_id}@example.invalid",
                            "name": f"Manager {index}",
                            "now": now,
                        },
                    )
                connection.execute(
                    text(
                        "INSERT INTO organization "
                        "(id, name, options, flags, created_by, is_active, "
                        "created_at, updated_at) "
                        "VALUES (:id, 'Security Alert Test', '{}'::jsonb, 0, "
                        ":created_by, true, :now, :now)"
                    ),
                    {
                        "id": organization_id,
                        "created_by": user_ids[0],
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO security_alerts "
                        "(id, organization_id, subject_actor_id, rule_id, "
                        "rule_version, severity, status, detection_key, "
                        "occurrence_count, first_detected_at, last_detected_at, "
                        "lifecycle_version) VALUES "
                        "(:id, :organization_id, :subject_actor_id, "
                        "'repeated_permission_denied', 'v1', 'medium', 'open', "
                        ":detection_key, 1, :now, :now, 1)"
                    ),
                    {
                        "id": alert_id,
                        "organization_id": organization_id,
                        "subject_actor_id": uuid.uuid4(),
                        "detection_key": f"test:{alert_id}",
                        "now": now,
                    },
                )

            ready = Barrier(2)

            def acknowledge(manager_id):
                with Session(engine) as session:
                    alert = session.get(SecurityAlert, alert_id)
                    ready.wait(timeout=5)
                    try:
                        acknowledge_security_alert(
                            session,
                            alert=alert,
                            manager_id=manager_id,
                            acknowledged_at=now,
                            expected_version=1,
                        )
                        session.commit()
                        return "success"
                    except SecurityAlertStaleStateError:
                        session.rollback()
                        return "stale"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(acknowledge, user_ids))

            with engine.connect() as connection:
                stored = connection.execute(
                    text(
                        "SELECT status, lifecycle_version FROM security_alerts "
                        "WHERE id = :alert_id"
                    ),
                    {"alert_id": alert_id},
                ).one()
                audit_count = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_logs "
                        "WHERE action = 'security_alert.acknowledged' "
                        "AND target_id = :target_id"
                    ),
                    {"target_id": str(alert_id)},
                ).scalar_one()

            assert sorted(results) == ["stale", "success"]
            assert stored.status == "acknowledged"
            assert stored.lifecycle_version == 2
            assert audit_count == 1
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_occurrence_and_acknowledge_race_preserves_both_database_updates():
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        manager_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        alert_id = uuid.uuid4()
        audit_log_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        detected_at = now + timedelta(seconds=1)

        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, name, social_provider, created_at, updated_at) "
                        "VALUES (:id, :email, 'Manager', 'local', :now, :now)"
                    ),
                    {
                        "id": manager_id,
                        "email": f"security-alert-race-{manager_id}@example.invalid",
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO organization "
                        "(id, name, options, flags, created_by, is_active, "
                        "created_at, updated_at) VALUES "
                        "(:id, 'Security Alert Race', '{}'::jsonb, 0, "
                        ":created_by, true, :now, :now)"
                    ),
                    {
                        "id": organization_id,
                        "created_by": manager_id,
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO security_alerts "
                        "(id, organization_id, subject_actor_id, rule_id, "
                        "rule_version, severity, status, detection_key, "
                        "occurrence_count, first_detected_at, last_detected_at, "
                        "lifecycle_version) VALUES "
                        "(:id, :organization_id, :subject_actor_id, "
                        "'repeated_permission_denied', 'v1', 'medium', 'open', "
                        ":detection_key, 4, :now, :now, 1)"
                    ),
                    {
                        "id": alert_id,
                        "organization_id": organization_id,
                        "subject_actor_id": uuid.uuid4(),
                        "detection_key": f"race:{alert_id}",
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, occurred_at, actor_id, actor_type, category, action, "
                        "status, audit_metadata) VALUES "
                        "(:id, :occurred_at, :actor_id, 'user', 'action', "
                        "'permission.denied', 'failure', "
                        "jsonb_build_object('organization_id', :organization_id))"
                    ),
                    {
                        "id": audit_log_id,
                        "occurred_at": detected_at,
                        "actor_id": manager_id,
                        "organization_id": str(organization_id),
                    },
                )

            ready = Barrier(2)

            def link_occurrence():
                with Session(engine) as session:
                    alert = session.get(SecurityAlert, alert_id)
                    ready.wait(timeout=5)
                    linked = link_security_alert_evidence(
                        session,
                        alert=alert,
                        audit_log_id=audit_log_id,
                        detected_at=detected_at,
                    )
                    session.commit()
                    return linked

            def acknowledge():
                with Session(engine) as session:
                    alert = session.get(SecurityAlert, alert_id)
                    ready.wait(timeout=5)
                    acknowledge_security_alert(
                        session,
                        alert=alert,
                        manager_id=manager_id,
                        acknowledged_at=detected_at,
                        expected_version=1,
                    )
                    session.commit()
                    return True

            with ThreadPoolExecutor(max_workers=2) as executor:
                occurrence_future = executor.submit(link_occurrence)
                acknowledge_future = executor.submit(acknowledge)
                assert occurrence_future.result(timeout=10) is True
                assert acknowledge_future.result(timeout=10) is True

            with engine.connect() as connection:
                stored = connection.execute(
                    text(
                        "SELECT status, lifecycle_version, occurrence_count, "
                        "last_detected_at FROM security_alerts WHERE id = :alert_id"
                    ),
                    {"alert_id": alert_id},
                ).one()
                evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id "
                        "AND audit_log_id = :audit_log_id"
                    ),
                    {"alert_id": alert_id, "audit_log_id": audit_log_id},
                ).scalar_one()
                lifecycle_audit_count = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_logs "
                        "WHERE action = 'security_alert.acknowledged' "
                        "AND target_id = :target_id"
                    ),
                    {"target_id": str(alert_id)},
                ).scalar_one()

            assert stored.status == "acknowledged"
            assert stored.lifecycle_version == 2
            assert stored.occurrence_count == 5
            assert stored.last_detected_at == detected_at
            assert evidence_count == 1
            assert lifecycle_audit_count == 1
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_evidence_is_idempotent_under_race_and_rejects_cross_organization():
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        user_id = uuid.uuid4()
        organization_ids = (uuid.uuid4(), uuid.uuid4())
        alert_id = uuid.uuid4()
        audit_ids = (uuid.uuid4(), uuid.uuid4())
        now = datetime.now(timezone.utc)

        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, name, social_provider, created_at, updated_at) "
                        "VALUES (:id, :email, 'Manager', 'local', :now, :now)"
                    ),
                    {
                        "id": user_id,
                        "email": f"security-alert-evidence-{user_id}@example.invalid",
                        "now": now,
                    },
                )
                for index, organization_id in enumerate(organization_ids):
                    connection.execute(
                        text(
                            "INSERT INTO organization "
                            "(id, name, options, flags, created_by, is_active, "
                            "created_at, updated_at) VALUES "
                            "(:id, :name, '{}'::jsonb, 0, :created_by, true, "
                            ":now, :now)"
                        ),
                        {
                            "id": organization_id,
                            "name": f"Evidence Organization {index}",
                            "created_by": user_id,
                            "now": now,
                        },
                    )
                connection.execute(
                    text(
                        "INSERT INTO security_alerts "
                        "(id, organization_id, subject_actor_id, rule_id, "
                        "rule_version, severity, status, detection_key, "
                        "occurrence_count, first_detected_at, last_detected_at, "
                        "lifecycle_version) VALUES "
                        "(:id, :organization_id, :subject_actor_id, "
                        "'repeated_permission_denied', 'v1', 'medium', 'open', "
                        ":detection_key, 0, :now, :now, 1)"
                    ),
                    {
                        "id": alert_id,
                        "organization_id": organization_ids[0],
                        "subject_actor_id": uuid.uuid4(),
                        "detection_key": f"evidence:{alert_id}",
                        "now": now,
                    },
                )
                for audit_id, organization_id in zip(audit_ids, organization_ids):
                    connection.execute(
                        text(
                            "INSERT INTO audit_logs "
                            "(id, occurred_at, actor_id, actor_type, category, "
                            "action, status, audit_metadata) VALUES "
                            "(:id, :occurred_at, :actor_id, 'user', 'action', "
                            "'permission.denied', 'failure', "
                            "jsonb_build_object('organization_id', "
                            ":organization_id))"
                        ),
                        {
                            "id": audit_id,
                            "occurred_at": now,
                            "actor_id": user_id,
                            "organization_id": str(organization_id),
                        },
                    )

            ready = Barrier(2)

            def link_same_audit():
                with Session(engine) as session:
                    alert = session.get(SecurityAlert, alert_id)
                    ready.wait(timeout=5)
                    linked = link_security_alert_evidence(
                        session,
                        alert=alert,
                        audit_log_id=audit_ids[0],
                        detected_at=now,
                    )
                    session.commit()
                    return linked

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: link_same_audit(), range(2)))

            with Session(engine) as session:
                alert = session.get(SecurityAlert, alert_id)
                cross_organization_audit = session.get(AuditLog, audit_ids[1])
                cross_organization_linked = link_security_alert_evidence(
                    session,
                    alert=alert,
                    audit_log=cross_organization_audit,
                    detected_at=now,
                )
                session.commit()

            with Session(engine) as session:
                alert = session.get(SecurityAlert, alert_id)
                cross_organization_id_linked = link_security_alert_evidence(
                    session,
                    alert=alert,
                    audit_log_id=audit_ids[1],
                    detected_at=now,
                )
                session.commit()

            with engine.connect() as connection:
                occurrence_count = connection.execute(
                    text(
                        "SELECT occurrence_count FROM security_alerts "
                        "WHERE id = :alert_id"
                    ),
                    {"alert_id": alert_id},
                ).scalar_one()
                evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id"
                    ),
                    {"alert_id": alert_id},
                ).scalar_one()

            assert sorted(results) == [False, True]
            assert cross_organization_linked is False
            assert cross_organization_id_linked is False
            assert occurrence_count == 1
            assert evidence_count == 1
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_distinct_concurrent_evidence_keeps_exact_count_and_latest_timestamp():
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        manager_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        alert_id = uuid.uuid4()
        audit_ids = (uuid.uuid4(), uuid.uuid4())
        now = datetime.now(timezone.utc)
        detected_times = (now + timedelta(seconds=1), now + timedelta(seconds=2))

        try:
            _seed_evidence_race_rows(
                engine,
                manager_id=manager_id,
                organization_id=organization_id,
                alert_id=alert_id,
                audit_events=tuple(zip(audit_ids, detected_times)),
                now=now,
            )
            ready = Barrier(2)

            def link_once(audit_log_id, detected_at):
                with Session(engine) as session:
                    alert = session.get(SecurityAlert, alert_id)
                    audit_log = session.get(AuditLog, audit_log_id)
                    ready.wait(timeout=5)
                    linked = link_security_alert_evidence(
                        session,
                        alert=alert,
                        audit_log=audit_log,
                        detected_at=detected_at,
                    )
                    session.commit()
                    return linked

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(link_once, audit_log_id, detected_at)
                    for audit_log_id, detected_at in zip(audit_ids, detected_times)
                ]
                results = [future.result(timeout=10) for future in futures]

            with engine.connect() as connection:
                stored = connection.execute(
                    text(
                        "SELECT occurrence_count, last_detected_at "
                        "FROM security_alerts WHERE id = :alert_id"
                    ),
                    {"alert_id": alert_id},
                ).one()
                evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id"
                    ),
                    {"alert_id": alert_id},
                ).scalar_one()

            assert results == [True, True]
            assert evidence_count == 2
            assert stored.occurrence_count == 2
            assert stored.last_detected_at == max(detected_times)
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_stale_open_alert_cannot_link_evidence_after_resolve_commits():
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        manager_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        alert_id = uuid.uuid4()
        audit_log_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        detected_at = now + timedelta(seconds=1)

        try:
            _seed_evidence_race_rows(
                engine,
                manager_id=manager_id,
                organization_id=organization_id,
                alert_id=alert_id,
                audit_events=((audit_log_id, detected_at),),
                now=now,
            )

            with Session(engine) as stale_session:
                stale_alert = stale_session.get(SecurityAlert, alert_id)
                audit_log = stale_session.get(AuditLog, audit_log_id)

                with Session(engine) as resolve_session:
                    current_alert = resolve_session.get(SecurityAlert, alert_id)
                    resolve_security_alert(
                        resolve_session,
                        alert=current_alert,
                        manager_id=manager_id,
                        resolved_at=detected_at,
                        expected_version=1,
                        resolution_type="mitigated",
                        reason="대응 완료",
                        reason_sanitizer=_IdentityReasonSanitizer(),
                    )
                    resolve_session.commit()

                linked = link_security_alert_evidence(
                    stale_session,
                    alert=stale_alert,
                    audit_log=audit_log,
                    detected_at=detected_at,
                )
                stale_session.commit()

            with engine.connect() as connection:
                stored = connection.execute(
                    text(
                        "SELECT status, occurrence_count "
                        "FROM security_alerts WHERE id = :alert_id"
                    ),
                    {"alert_id": alert_id},
                ).one()
                evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE security_alert_id = :alert_id"
                    ),
                    {"alert_id": alert_id},
                ).scalar_one()

            assert linked is False
            assert stored.status == "resolved"
            assert stored.occurrence_count == 0
            assert evidence_count == 0
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_alert_constraints_defaults_and_delete_policies_in_postgres():
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        creator_id = uuid.uuid4()
        handler_id = uuid.uuid4()
        resolved_handler_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        alert_id = uuid.uuid4()
        acknowledged_alert_id = uuid.uuid4()
        resolved_alert_id = uuid.uuid4()
        audit_log_id = uuid.uuid4()
        parent_delete_audit_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        base_insert = (
            "INSERT INTO security_alerts "
            "(id, organization_id, subject_actor_id, rule_id, rule_version, "
            "severity, detection_key, first_detected_at, last_detected_at) "
            "VALUES (:id, :organization_id, :subject_actor_id, "
            "'repeated_permission_denied', 'v1', :severity, :detection_key, "
            ":first_detected_at, :last_detected_at)"
        )

        try:
            with engine.begin() as connection:
                for index, user_id in enumerate(
                    (creator_id, handler_id, resolved_handler_id)
                ):
                    connection.execute(
                        text(
                            "INSERT INTO users "
                            "(id, email, name, social_provider, created_at, updated_at) "
                            "VALUES (:id, :email, :name, 'local', :now, :now)"
                        ),
                        {
                            "id": user_id,
                            "email": f"security-alert-policy-{user_id}@example.invalid",
                            "name": f"User {index}",
                            "now": now,
                        },
                    )
                connection.execute(
                    text(
                        "INSERT INTO organization "
                        "(id, name, options, flags, created_by, is_active, "
                        "created_at, updated_at) VALUES "
                        "(:id, 'Security Alert Policies', '{}'::jsonb, 0, "
                        ":created_by, true, :now, :now)"
                    ),
                    {
                        "id": organization_id,
                        "created_by": creator_id,
                        "now": now,
                    },
                )
                connection.execute(
                    text(base_insert),
                    {
                        "id": alert_id,
                        "organization_id": organization_id,
                        "subject_actor_id": uuid.uuid4(),
                        "severity": "medium",
                        "detection_key": "policy:shared-key",
                        "first_detected_at": now,
                        "last_detected_at": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO security_alerts "
                        "(id, organization_id, subject_actor_id, rule_id, "
                        "rule_version, severity, status, detection_key, "
                        "occurrence_count, first_detected_at, last_detected_at, "
                        "lifecycle_version, acknowledged_by, acknowledged_at) "
                        "VALUES (:id, :organization_id, :subject_actor_id, "
                        "'repeated_permission_denied', 'v1', 'medium', "
                        "'acknowledged', :detection_key, 1, :now, :now, 1, "
                        ":acknowledged_by, :now)"
                    ),
                    {
                        "id": acknowledged_alert_id,
                        "organization_id": organization_id,
                        "subject_actor_id": uuid.uuid4(),
                        "detection_key": f"ack:{acknowledged_alert_id}",
                        "acknowledged_by": handler_id,
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, occurred_at, actor_id, actor_type, category, action, "
                        "status, audit_metadata) VALUES "
                        "(:id, :now, :actor_id, 'user', 'action', "
                        "'permission.denied', 'failure', '{}'::jsonb)"
                    ),
                    {"id": audit_log_id, "now": now, "actor_id": creator_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO security_alerts "
                        "(id, organization_id, subject_actor_id, rule_id, "
                        "rule_version, severity, status, detection_key, "
                        "occurrence_count, first_detected_at, last_detected_at, "
                        "lifecycle_version, resolution_type, resolution_reason, "
                        "resolved_by, resolved_at) VALUES "
                        "(:id, :organization_id, :subject_actor_id, "
                        "'repeated_permission_denied', 'v1', 'medium', "
                        "'resolved', :detection_key, 1, :now, :now, 2, "
                        "'mitigated', 'sanitized reason', :resolved_by, :now)"
                    ),
                    {
                        "id": resolved_alert_id,
                        "organization_id": organization_id,
                        "subject_actor_id": uuid.uuid4(),
                        "detection_key": f"resolved:{resolved_alert_id}",
                        "resolved_by": resolved_handler_id,
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, occurred_at, actor_id, actor_type, category, action, "
                        "status, audit_metadata) VALUES "
                        "(:id, :now, :actor_id, 'user', 'action', "
                        "'permission.denied', 'failure', '{}'::jsonb)"
                    ),
                    {
                        "id": parent_delete_audit_id,
                        "now": now,
                        "actor_id": creator_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO security_alert_audit_events "
                        "(id, security_alert_id, audit_log_id) "
                        "VALUES (:id, :alert_id, :audit_log_id)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "alert_id": alert_id,
                        "audit_log_id": audit_log_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO security_alert_audit_events "
                        "(id, security_alert_id, audit_log_id) "
                        "VALUES (:id, :alert_id, :audit_log_id)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "alert_id": acknowledged_alert_id,
                        "audit_log_id": parent_delete_audit_id,
                    },
                )

            with engine.connect() as connection:
                defaults = connection.execute(
                    text(
                        "SELECT status, occurrence_count, episode_count, "
                        "last_episode_started_at, lifecycle_version, "
                        "created_at, updated_at FROM security_alerts WHERE id = :id"
                    ),
                    {"id": alert_id},
                ).one()
            assert defaults.status == "open"
            assert defaults.occurrence_count == 0
            assert defaults.episode_count == 1
            assert defaults.last_episode_started_at is not None
            assert defaults.lifecycle_version == 1
            assert defaults.created_at.tzinfo is not None
            assert defaults.updated_at.tzinfo is not None

            invalid_cases = (
                {"severity": "critical", "first": now, "last": now},
                {"severity": "medium", "first": now, "last": now - timedelta(1)},
            )
            for invalid in invalid_cases:
                _assert_integrity_error(
                    engine,
                    base_insert,
                    {
                        "id": uuid.uuid4(),
                        "organization_id": organization_id,
                        "subject_actor_id": uuid.uuid4(),
                        "severity": invalid["severity"],
                        "detection_key": f"invalid:{uuid.uuid4()}",
                        "first_detected_at": invalid["first"],
                        "last_detected_at": invalid["last"],
                    },
                )

            for invalid_update in (
                "occurrence_count = -1",
                "episode_count = 0",
                "lifecycle_version = 0",
                "status = 'unknown'",
                "resolution_type = 'unknown'",
                "status = 'acknowledged', acknowledged_at = NULL",
            ):
                _assert_integrity_error(
                    engine,
                    f"UPDATE security_alerts SET {invalid_update} WHERE id = :id",
                    {"id": alert_id},
                )

            _assert_integrity_error(
                engine,
                base_insert,
                {
                    "id": uuid.uuid4(),
                    "organization_id": organization_id,
                    "subject_actor_id": uuid.uuid4(),
                    "severity": "medium",
                    "detection_key": "policy:shared-key",
                    "first_detected_at": now,
                    "last_detected_at": now,
                },
            )

            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE security_alerts SET status = 'resolved', "
                        "resolution_type = 'mitigated', "
                        "resolution_reason = 'done', resolved_at = :now "
                        "WHERE id = :id"
                    ),
                    {"id": alert_id, "now": now},
                )
                connection.execute(
                    text(base_insert),
                    {
                        "id": uuid.uuid4(),
                        "organization_id": organization_id,
                        "subject_actor_id": uuid.uuid4(),
                        "severity": "medium",
                        "detection_key": "policy:shared-key",
                        "first_detected_at": now,
                        "last_detected_at": now,
                    },
                )
                connection.execute(
                    text("DELETE FROM users WHERE id = :id"),
                    {"id": handler_id},
                )
                connection.execute(
                    text("DELETE FROM users WHERE id = :id"),
                    {"id": resolved_handler_id},
                )
                connection.execute(
                    text("DELETE FROM audit_logs WHERE id = :id"),
                    {"id": audit_log_id},
                )
                connection.execute(
                    text("DELETE FROM security_alerts WHERE id = :id"),
                    {"id": acknowledged_alert_id},
                )

            _assert_integrity_error(
                engine,
                "DELETE FROM organization WHERE id = :id",
                {"id": organization_id},
            )

            with engine.connect() as connection:
                resolved_history = connection.execute(
                    text(
                        "SELECT resolved_by, resolved_at, resolution_type, "
                        "resolution_reason FROM security_alerts WHERE id = :id"
                    ),
                    {"id": resolved_alert_id},
                ).one()
                evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE audit_log_id = :audit_log_id"
                    ),
                    {"audit_log_id": audit_log_id},
                ).scalar_one()
                parent_evidence_count = connection.execute(
                    text(
                        "SELECT count(*) FROM security_alert_audit_events "
                        "WHERE audit_log_id = :audit_log_id"
                    ),
                    {"audit_log_id": parent_delete_audit_id},
                ).scalar_one()
                parent_audit_count = connection.execute(
                    text("SELECT count(*) FROM audit_logs WHERE id = :id"),
                    {"id": parent_delete_audit_id},
                ).scalar_one()

            assert resolved_history.resolved_by is None
            assert resolved_history.resolved_at == now
            assert resolved_history.resolution_type == "mitigated"
            assert resolved_history.resolution_reason == "sanitized reason"
            assert evidence_count == 0
            assert parent_evidence_count == 0
            assert parent_audit_count == 1
        finally:
            engine.dispose()


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_concurrent_resolve_and_reopen_each_have_one_database_winner():
    with _disposable_database() as (database, config):
        engine = create_engine(config.database_url(database))
        manager_ids = (uuid.uuid4(), uuid.uuid4())
        organization_id = uuid.uuid4()
        resolve_alert_id = uuid.uuid4()
        reopen_alert_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        try:
            with engine.begin() as connection:
                for index, manager_id in enumerate(manager_ids):
                    connection.execute(
                        text(
                            "INSERT INTO users "
                            "(id, email, name, social_provider, created_at, updated_at) "
                            "VALUES (:id, :email, :name, 'local', :now, :now)"
                        ),
                        {
                            "id": manager_id,
                            "email": f"security-alert-lifecycle-{manager_id}@example.invalid",
                            "name": f"Manager {index}",
                            "now": now,
                        },
                    )
                connection.execute(
                    text(
                        "INSERT INTO organization "
                        "(id, name, options, flags, created_by, is_active, "
                        "created_at, updated_at) VALUES "
                        "(:id, 'Lifecycle Race', '{}'::jsonb, 0, :created_by, "
                        "true, :now, :now)"
                    ),
                    {
                        "id": organization_id,
                        "created_by": manager_ids[0],
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO security_alerts "
                        "(id, organization_id, subject_actor_id, rule_id, "
                        "rule_version, severity, status, detection_key, "
                        "occurrence_count, first_detected_at, last_detected_at, "
                        "lifecycle_version) VALUES "
                        "(:id, :organization_id, :subject_actor_id, "
                        "'repeated_permission_denied', 'v1', 'medium', 'open', "
                        ":detection_key, 1, :now, :now, 1)"
                    ),
                    {
                        "id": resolve_alert_id,
                        "organization_id": organization_id,
                        "subject_actor_id": uuid.uuid4(),
                        "detection_key": f"resolve-race:{resolve_alert_id}",
                        "now": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO security_alerts "
                        "(id, organization_id, subject_actor_id, rule_id, "
                        "rule_version, severity, status, detection_key, "
                        "occurrence_count, first_detected_at, last_detected_at, "
                        "lifecycle_version, acknowledged_by, acknowledged_at) "
                        "VALUES (:id, :organization_id, :subject_actor_id, "
                        "'repeated_permission_denied', 'v1', 'medium', "
                        "'acknowledged', :detection_key, 1, :now, :now, 2, "
                        ":acknowledged_by, :now)"
                    ),
                    {
                        "id": reopen_alert_id,
                        "organization_id": organization_id,
                        "subject_actor_id": uuid.uuid4(),
                        "detection_key": f"reopen-race:{reopen_alert_id}",
                        "acknowledged_by": manager_ids[0],
                        "now": now,
                    },
                )

            resolve_ready = Barrier(2)

            def resolve(manager_id):
                with Session(engine) as session:
                    alert = session.get(SecurityAlert, resolve_alert_id)
                    resolve_ready.wait(timeout=5)
                    try:
                        resolve_security_alert(
                            session,
                            alert=alert,
                            manager_id=manager_id,
                            resolved_at=now,
                            expected_version=1,
                            resolution_type="mitigated",
                            reason="대응 완료",
                            reason_sanitizer=_IdentityReasonSanitizer(),
                        )
                        session.commit()
                        return "success"
                    except SecurityAlertStaleStateError:
                        session.rollback()
                        return "stale"

            with ThreadPoolExecutor(max_workers=2) as executor:
                resolve_results = list(executor.map(resolve, manager_ids))

            reopen_ready = Barrier(2)

            def reopen(manager_id):
                with Session(engine) as session:
                    alert = session.get(SecurityAlert, reopen_alert_id)
                    reopen_ready.wait(timeout=5)
                    try:
                        reopen_security_alert(
                            session,
                            alert=alert,
                            manager_id=manager_id,
                            reopened_at=now,
                            expected_version=2,
                        )
                        session.commit()
                        return "success"
                    except SecurityAlertStaleStateError:
                        session.rollback()
                        return "stale"

            with ThreadPoolExecutor(max_workers=2) as executor:
                reopen_results = list(executor.map(reopen, manager_ids))

            with engine.connect() as connection:
                resolve_audit_count = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_logs "
                        "WHERE action = 'security_alert.resolved' "
                        "AND target_id = :target_id"
                    ),
                    {"target_id": str(resolve_alert_id)},
                ).scalar_one()
                reopen_audit_count = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_logs "
                        "WHERE action = 'security_alert.reopened' "
                        "AND target_id = :target_id"
                    ),
                    {"target_id": str(reopen_alert_id)},
                ).scalar_one()

            assert sorted(resolve_results) == ["stale", "success"]
            assert sorted(reopen_results) == ["stale", "success"]
            assert resolve_audit_count == 1
            assert reopen_audit_count == 1
        finally:
            engine.dispose()


class _IdentityReasonSanitizer:
    def sanitize(self, reason):
        return reason
