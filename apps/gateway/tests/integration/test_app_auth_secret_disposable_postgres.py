"""Opt-in PostgreSQL evidence for the App auth secret staged rollout."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Lock
from unittest.mock import patch

import pytest
from apps.gateway.services.app_auth_secret_service import (
    AppAuthSecretService,
    AppAuthSecretVersionConflictError,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.audit_log import AuditEventOutbox
from apps.shared.domain.app_auth_secret import app_auth_secret_verifier
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_app_auth"
BASE_REVISION = "a9b0c1d2e3f4"
APP_AUTH_REVISION = "b0c1d2e3f4a5"


def _run_alembic(
    *args: str,
    database: str,
    config: DisposablePostgresConfig,
) -> None:
    environment = config.subprocess_environment(database=database, root_dir=ROOT_DIR)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "apps/shared/alembic.ini", *args],
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
        returncode = result.returncode
        del result
        pytest.fail(
            f"alembic command failed with exit code {returncode}; "
            "stdout/stderr omitted to avoid leaking local configuration"
        )


def _assert_alembic_fails(
    *args: str,
    database: str,
    config: DisposablePostgresConfig,
) -> None:
    environment = config.subprocess_environment(database=database, root_dir=ROOT_DIR)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "apps/shared/alembic.ini", *args],
        cwd=ROOT_DIR,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.returncode == 0:
        pytest.fail("alembic command unexpectedly succeeded")


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


def _revision(database: str, config: DisposablePostgresConfig) -> str:
    engine = create_engine(config.database_url(database))
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()


def _insert_legacy_scope(
    engine,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    app_id: uuid.UUID,
    raw_secret: str,
) -> None:
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, email, name, social_provider, created_at, updated_at
                ) VALUES (
                    :id, :email, :name, 'local', :created_at, :updated_at
                )
                """
            ),
            {
                "id": user_id,
                "email": f"app-auth-{user_id}@example.invalid",
                "name": "App Auth Migration",
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO organization (
                    id, name, created_by, is_active, created_at, updated_at
                ) VALUES (
                    :id, :name, :created_by, true, :created_at, :updated_at
                )
                """
            ),
            {
                "id": organization_id,
                "name": f"App Auth {organization_id}",
                "created_by": user_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO apps (
                    id, organization_id, name, icon, url_slug, auth_secret,
                    is_api_enabled, api_req_per_minute, api_req_per_hour,
                    is_market, created_by, created_at, updated_at
                ) VALUES (
                    :id, :organization_id, :name, CAST(:icon AS jsonb),
                    :url_slug, :auth_secret, true, 60, 3600, false,
                    :created_by, :created_at, :updated_at
                )
                """
            ),
            {
                "id": app_id,
                "organization_id": organization_id,
                "name": "Legacy App",
                "icon": "{}",
                "url_slug": f"legacy-{app_id}",
                "auth_secret": raw_secret,
                "created_by": user_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_old_gateway_app_after_expand(
    engine,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    app_id: uuid.UUID,
    raw_secret: str,
) -> None:
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO apps (
                    id, organization_id, name, icon, url_slug, auth_secret,
                    is_api_enabled, api_req_per_minute, api_req_per_hour,
                    is_market, created_by, created_at, updated_at
                ) VALUES (
                    :id, :organization_id, :name, CAST(:icon AS jsonb),
                    :url_slug, :auth_secret, true, 60, 3600, false,
                    :created_by, :created_at, :updated_at
                )
                """
            ),
            {
                "id": app_id,
                "organization_id": organization_id,
                "name": "Old Gateway App",
                "icon": "{}",
                "url_slug": f"old-gateway-{app_id}",
                "auth_secret": raw_secret,
                "created_by": user_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_unconfigured_app_after_expand(
    engine,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    app_id: uuid.UUID,
) -> None:
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO apps (
                    id, organization_id, name, icon, url_slug, auth_secret,
                    is_api_enabled, api_req_per_minute, api_req_per_hour,
                    is_market, created_by, created_at, updated_at
                ) VALUES (
                    :id, :organization_id, :name, CAST(:icon AS jsonb),
                    :url_slug, NULL, true, 60, 3600, false,
                    :created_by, :created_at, :updated_at
                )
                """
            ),
            {
                "id": app_id,
                "organization_id": organization_id,
                "name": "Unconfigured App",
                "icon": "{}",
                "url_slug": f"unconfigured-{app_id}",
                "created_by": user_id,
                "created_at": now,
                "updated_at": now,
            },
        )


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL App auth evidence",
)
def test_app_auth_expand_gate_rotation_race_and_downgrade_guard_in_postgres():
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
        _run_alembic("upgrade", BASE_REVISION, database=database, config=config)

        engine = create_engine(config.database_url(database), pool_pre_ping=True)
        user_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        app_id = uuid.uuid4()
        legacy_secret = "synthetic-legacy-app-secret"
        _insert_legacy_scope(
            engine,
            user_id=user_id,
            organization_id=organization_id,
            app_id=app_id,
            raw_secret=legacy_secret,
        )
        engine.dispose()

        _run_alembic("upgrade", APP_AUTH_REVISION, database=database, config=config)
        if _revision(database, config) != APP_AUTH_REVISION:
            pytest.fail("App auth migration revision was not applied")

        engine = create_engine(config.database_url(database), pool_pre_ping=True)
        old_gateway_app_id = uuid.uuid4()
        old_gateway_secret = "synthetic-old-gateway-secret"
        _insert_old_gateway_app_after_expand(
            engine,
            user_id=user_id,
            organization_id=organization_id,
            app_id=old_gateway_app_id,
            raw_secret=old_gateway_secret,
        )
        with Session(engine) as session:
            migrated_app = session.get(App, app_id)
            old_gateway_app = session.get(App, old_gateway_app_id)
            if migrated_app is None or old_gateway_app is None:
                pytest.fail("App auth migration evidence rows are missing")
            if migrated_app.auth_secret_generation != 1:
                pytest.fail("Legacy App was not assigned verifier generation 1")
            if migrated_app.auth_secret_verifier != app_auth_secret_verifier(
                legacy_secret
            ):
                pytest.fail("Legacy App verifier backfill is incorrect")
            if not AppAuthSecretService.authenticate(migrated_app, legacy_secret):
                pytest.fail("Migrated legacy secret no longer authenticates")
            if not AppAuthSecretService.authenticate(
                old_gateway_app,
                old_gateway_secret,
            ):
                pytest.fail("Generation 0 old-Gateway secret is not compatible")

        unconfigured_app_id = uuid.uuid4()
        _insert_unconfigured_app_after_expand(
            engine,
            user_id=user_id,
            organization_id=organization_id,
            app_id=unconfigured_app_id,
        )
        engine.dispose()
        _assert_alembic_fails(
            "downgrade",
            BASE_REVISION,
            database=database,
            config=config,
        )
        if _revision(database, config) != APP_AUTH_REVISION:
            pytest.fail("Unconfigured App downgrade guard changed the schema revision")
        engine = create_engine(config.database_url(database), pool_pre_ping=True)
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM apps WHERE id = :app_id"),
                {"app_id": unconfigured_app_id},
            )

        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        barrier = Barrier(2)
        generated_lock = Lock()
        generated: list[str] = []

        def rotate(candidate: str):
            with session_factory() as session:
                barrier.wait(timeout=10)

                def generator() -> str:
                    with generated_lock:
                        generated.append(candidate)
                    return candidate

                try:
                    response = AppAuthSecretService.rotate(
                        session,
                        app_id=app_id,
                        organization_id=organization_id,
                        actor_user_id=user_id,
                        expected_version=1,
                        revoke_previous_immediately=False,
                        lifecycle_mutations_enabled=True,
                        secret_generator=generator,
                    )
                    return "success", response.secret
                except AppAuthSecretVersionConflictError:
                    return "conflict", None

        candidates = (
            "synthetic-rotation-candidate-a",
            "synthetic-rotation-candidate-b",
        )
        with (
            patch(
                "apps.gateway.services.app_auth_secret_service."
                "has_organization_scope_access",
                return_value=True,
            ),
            patch(
                "apps.gateway.services.app_auth_secret_service."
                "AppService.can_deploy_app",
                return_value=True,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(rotate, candidates))

        if sorted(status for status, _ in results) != ["conflict", "success"]:
            pytest.fail("Concurrent rotation did not produce one winner and one loser")
        if len(generated) != 1:
            pytest.fail("A stale rotation generated a credential before CAS rejection")
        winner_secret = next(
            secret for status, secret in results if status == "success"
        )
        if winner_secret is None:
            pytest.fail("Rotation winner did not produce a credential")

        with Session(engine) as session:
            final_app = session.get(App, app_id)
            if final_app is None:
                pytest.fail("Rotated App row is missing")
            if final_app.auth_secret_generation != 2:
                pytest.fail("Rotation winner did not advance generation exactly once")
            if final_app.auth_secret is not None:
                pytest.fail("Enabled lifecycle persisted a raw App credential")
            if not AppAuthSecretService.authenticate(final_app, winner_secret):
                pytest.fail("Rotation winner secret does not authenticate")
            if not AppAuthSecretService.authenticate(final_app, legacy_secret):
                pytest.fail("Previous verifier grace was not preserved")
            rotation_outboxes = [
                row
                for row in session.query(AuditEventOutbox).all()
                if row.payload.get("action") == "app.auth_secret.rotated"
            ]
            if len(rotation_outboxes) != 1:
                pytest.fail("Rotation and audit Outbox were not committed exactly once")
            metadata = rotation_outboxes[0].payload.get("audit_metadata", {})
            if any(key in metadata for key in ("request_id", "ip", "user_agent")):
                pytest.fail("Rotation audit persisted caller-controlled metadata")
        engine.dispose()

        _assert_alembic_fails(
            "downgrade",
            BASE_REVISION,
            database=database,
            config=config,
        )
        if _revision(database, config) != APP_AUTH_REVISION:
            pytest.fail("Failed verifier-only downgrade changed the schema revision")
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
