"""Opt-in PostgreSQL race evidence for durable Mail processing."""

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
from apps.shared.db.models.app import App
from apps.shared.db.models.mail_credential import MailCredential
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import (
    DeploymentType,
    WorkflowDeployment,
)
from apps.shared.domain.mail_processing import (
    MailSourceReference,
    build_message_identity_hash,
)
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from apps.workflow_engine.adapters.mail_processing_repository import (
    SqlAlchemyMailProcessingRepository,
)
from apps.workflow_engine.application.mail_processing import (
    ProcessingRegistration,
    ProtectedReference,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_mail_processing"


def _run_alembic(database: str, config: DisposablePostgresConfig) -> None:
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
        env=config.subprocess_environment(database=database, root_dir=ROOT_DIR),
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
            f"alembic command returned unexpected exit code {returncode}; "
            "stdout/stderr omitted to avoid leaking local configuration"
        )


def _seed_scope(engine) -> dict[str, uuid.UUID]:
    ids = {
        "user": uuid.uuid4(),
        "organization": uuid.uuid4(),
        "app": uuid.uuid4(),
        "workflow": uuid.uuid4(),
        "deployment": uuid.uuid4(),
        "credential": uuid.uuid4(),
    }
    with Session(engine) as session:
        session.add(
            User(
                id=ids["user"],
                email=f"mail-race-{ids['user']}@example.invalid",
                name="Mail Race Test",
                social_provider="local",
            )
        )
        session.flush()
        session.add(
            Organization(
                id=ids["organization"],
                name="Mail Race Test Organization",
                created_by=ids["user"],
                is_active=True,
            )
        )
        session.flush()
        app = App(
            id=ids["app"],
            organization_id=ids["organization"],
            name="Mail Race App",
            url_slug=f"mail-race-{ids['app']}",
            auth_secret=None,
            created_by=ids["user"],
        )
        session.add(app)
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
        app.workflow_id = ids["workflow"]
        session.add(
            WorkflowDeployment(
                id=ids["deployment"],
                app_id=ids["app"],
                version=1,
                type=DeploymentType.SCHEDULE,
                graph_snapshot={"nodes": [], "edges": []},
                created_by=ids["user"],
                is_active=True,
            )
        )
        session.add(
            MailCredential(
                id=ids["credential"],
                organization_id=ids["organization"],
                credential_name="Mail Race Credential",
                provider="gmail",
                email_address="mail-race@example.invalid",
                auth_type="oauth2",
                imap_host="imap.gmail.com",
                imap_port=993,
                use_ssl=True,
                encrypted_secret="synthetic-ciphertext",
                encryption_key_version="v1",
                encryption_algorithm="fernet-v1",
                status="active",
                created_by=ids["user"],
            )
        )
        session.commit()
    return ids


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL Mail race evidence",
)
def test_message_registration_and_draft_admission_have_single_database_winner():
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
            config.database_url(database), isolation_level="AUTOCOMMIT"
        )
        try:
            with extension_engine.connect() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        finally:
            extension_engine.dispose()
        _run_alembic(database, config)
        engine = create_engine(config.database_url(database))
        ids = _seed_scope(engine)
        source = MailSourceReference(
            provider_message_id="gmail-race-message",
            message_id="<mail-race@example.invalid>",
        )
        registration = ProcessingRegistration(
            organization_id=ids["organization"],
            workflow_id=ids["workflow"],
            deployment_id=ids["deployment"],
            source_node_id="mail-source",
            credential_id=ids["credential"],
            provider="gmail",
            source=source,
        )
        identity_hash = build_message_identity_hash(
            organization_id=ids["organization"],
            workflow_id=ids["workflow"],
            source_node_id="mail-source",
            credential_id=ids["credential"],
            provider="gmail",
            source=source,
        )
        registration_barrier = Barrier(2)

        def register() -> uuid.UUID:
            with Session(engine) as session:
                registration_barrier.wait(timeout=10)
                return SqlAlchemyMailProcessingRepository(session).register_message(
                    registration=registration,
                    message_identity_hash=identity_hash,
                    source_reference=ProtectedReference(
                        ciphertext="synthetic-source-ciphertext",
                        key_version="v1",
                        algorithm="fernet-v1",
                    ),
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            processing_ids = list(executor.map(lambda _index: register(), range(2)))
        assert len(set(processing_ids)) == 1

        admission_barrier = Barrier(2)

        def claim(index: int) -> bool:
            with Session(engine) as session:
                admission_barrier.wait(timeout=10)
                admission = SqlAlchemyMailProcessingRepository(
                    session
                ).claim_draft_effect(
                    processing_id=processing_ids[0],
                    organization_id=ids["organization"],
                    workflow_id=ids["workflow"],
                    deployment_id=ids["deployment"],
                    node_id="gmail-draft",
                    operation_key_hash="a" * 64,
                    input_digest="b" * 64,
                    lease_owner_hash=str(index) * 64,
                    lease_expires_at=datetime.now(timezone.utc)
                    + timedelta(minutes=5),
                    max_attempts=3,
                )
                return admission.acquired

        with ThreadPoolExecutor(max_workers=2) as executor:
            acquired = list(executor.map(claim, range(1, 3)))
        assert sorted(acquired) == [False, True]
    finally:
        if engine is not None:
            engine.dispose()
        if database_created:
            try:
                with admin_engine.connect() as connection:
                    connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup could not connect; "
                    "connection details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()
