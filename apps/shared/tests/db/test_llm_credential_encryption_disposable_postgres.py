"""Opt-in PostgreSQL verification for LLM credential encryption migration."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest
from apps.shared.db.models.llm import LLMCredential
from apps.shared.services.credential_encryption import CredentialEncryptionService
from apps.shared.services.llm_credential_config import LLMCredentialConfigService
from apps.shared.services.llm_credential_rotation import (
    LLMCredentialRotationService,
)
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_LLM_CREDENTIAL_DB_TEST"
DB_PREFIX = "mbased_llm_credential"
PRE_ENCRYPTION_REVISION = "b0c1d2e3f4a5"
ENCRYPTION_REVISION = "c2e8f4a91d67"


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL integration tests",
)
def test_llm_credential_upgrade_rotation_concurrency_and_downgrade_guard():
    with _disposable_database(target_revision=PRE_ENCRYPTION_REVISION) as (
        database,
        config,
    ):
        engine = create_engine(config.database_url(database))
        try:
            credential_ids = _seed_legacy_credentials(engine)
            _run_alembic_command(database, config, "upgrade", ENCRYPTION_REVISION)

            schema = inspect(engine)
            columns = {
                column["name"]: column
                for column in schema.get_columns("llm_credentials")
            }
            assert columns["encryption_key_version"]["nullable"] is True
            assert columns["encryption_algorithm"]["nullable"] is True
            assert "ck_llm_credentials_encryption_metadata_pair" in {
                constraint["name"]
                for constraint in schema.get_check_constraints("llm_credentials")
            }
            assert "ix_llm_credentials_encryption_key_version" in {
                index["name"] for index in schema.get_indexes("llm_credentials")
            }

            with engine.connect() as connection:
                legacy_rows = connection.execute(
                    text(
                        "SELECT encrypted_config, encryption_key_version, "
                        "encryption_algorithm FROM llm_credentials ORDER BY id"
                    )
                ).all()
            assert len(legacy_rows) == 2
            assert all(row.encryption_key_version is None for row in legacy_rows)
            assert all(row.encryption_algorithm is None for row in legacy_rows)

            old_key = Fernet.generate_key().decode("utf-8")
            new_key = Fernet.generate_key().decode("utf-8")
            encryption = CredentialEncryptionService(
                {"v1": old_key, "v2": new_key},
                "v2",
                subject_label="LLM credential",
            )
            config_service = LLMCredentialConfigService(encryption)
            barrier = Barrier(2)

            class SynchronizedConfigService(LLMCredentialConfigService):
                def protect(self, stored_config):
                    barrier.wait(timeout=10)
                    return super().protect(stored_config)

            synchronized_service = SynchronizedConfigService(encryption)

            def rotate_one() -> int:
                with Session(engine) as session:
                    return LLMCredentialRotationService(
                        session,
                        config_service=synchronized_service,
                    ).rotate_batch(batch_size=1)

            with ThreadPoolExecutor(max_workers=2) as executor:
                processed = list(executor.map(lambda _: rotate_one(), range(2)))

            assert processed == [1, 1]
            with Session(engine) as session:
                credentials = (
                    session.query(LLMCredential)
                    .filter(LLMCredential.id.in_(credential_ids))
                    .order_by(LLMCredential.id.asc())
                    .all()
                )
                assert all(
                    credential.encryption_key_version == "v2"
                    for credential in credentials
                )
                assert {
                    config_service.load(credential)["apiKey"]
                    for credential in credentials
                } == {"synthetic-legacy-one", "synthetic-legacy-two"}

            failed_downgrade = _run_alembic_command(
                database,
                config,
                "downgrade",
                PRE_ENCRYPTION_REVISION,
                expect_success=False,
            )
            assert failed_downgrade.returncode != 0

            with engine.begin() as connection:
                connection.execute(text("DELETE FROM llm_credentials"))
            _run_alembic_command(
                database,
                config,
                "downgrade",
                PRE_ENCRYPTION_REVISION,
            )
            downgraded_columns = {
                column["name"]
                for column in inspect(engine).get_columns("llm_credentials")
            }
            assert "encryption_key_version" not in downgraded_columns
            assert "encryption_algorithm" not in downgraded_columns
        finally:
            engine.dispose()


def _seed_legacy_credentials(engine) -> list[uuid.UUID]:
    user_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    credential_ids = [uuid.uuid4(), uuid.uuid4()]
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, name, social_provider, created_at, updated_at) "
                "VALUES (:id, :email, 'LLM Migration User', 'local', :now, :now)"
            ),
            {
                "id": user_id,
                "email": f"llm-migration-{user_id}@example.invalid",
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO llm_providers "
                "(id, name, description, type, base_url, auth_type, doc_url, "
                "created_at, updated_at) VALUES "
                "(:id, 'openai', NULL, 'custom', 'https://api.example.test', "
                "'api_key', 'https://docs.example.test', :now, :now)"
            ),
            {"id": provider_id, "now": now},
        )
        for index, credential_id in enumerate(credential_ids, start=1):
            connection.execute(
                text(
                    "INSERT INTO llm_credentials "
                    "(id, provider_id, user_id, organization_id, credential_name, "
                    "encrypted_config, config_preview, is_valid, quota_type, "
                    "quota_limit, quota_used, last_used_at, created_at, updated_at) "
                    "VALUES (:id, :provider_id, :user_id, NULL, :name, :config, "
                    "NULL, true, 'none', -1, 0, NULL, :now, :now)"
                ),
                {
                    "id": credential_id,
                    "provider_id": provider_id,
                    "user_id": user_id,
                    "name": f"legacy-{index}",
                    "config": (
                        '{"apiKey":"synthetic-legacy-one","baseUrl":null}'
                        if index == 1
                        else '{"apiKey":"synthetic-legacy-two","baseUrl":null}'
                    ),
                    "now": now,
                },
            )
    return credential_ids


def _run_alembic_command(
    database: str,
    config: DisposablePostgresConfig,
    *args: str,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
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
    if expect_success and result.returncode != 0:
        pytest.fail("LLM credential migration command failed; output omitted")
    return result


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


@contextmanager
def _disposable_database(*, target_revision: str):
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
        _run_alembic_command(database, config, "upgrade", target_revision)
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
