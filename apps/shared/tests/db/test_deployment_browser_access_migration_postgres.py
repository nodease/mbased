from __future__ import annotations

import os
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from apps.shared.alembic.versions import (
    fd4e5f6a7b89_add_deployment_browser_access_policy as revision,
)
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import OperationalError

RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_browser_access"


def test_workflow_deployment_model_declares_nullable_browser_access_policy() -> None:
    column = WorkflowDeployment.__table__.c.browser_access_policy

    assert column.nullable is True
    assert isinstance(column.type, JSONB)


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL migration integration",
)
def test_browser_access_policy_revision_preserves_legacy_rows_and_downgrades(
    monkeypatch,
) -> None:
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
    test_engine = None

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {quoted_database}"))
        database_created = True

        test_engine = create_engine(config.database_url(database))
        legacy_id = uuid.uuid4()
        with test_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE workflow_deployments (
                        id UUID PRIMARY KEY
                    )
                    """
                )
            )
            connection.execute(
                text("INSERT INTO workflow_deployments (id) VALUES (:id)"),
                {"id": legacy_id},
            )
            operations = Operations(MigrationContext.configure(connection))
            monkeypatch.setattr(revision, "op", operations)

            revision.upgrade()

            columns = {
                column["name"]: column
                for column in inspect(connection).get_columns(
                    "workflow_deployments"
                )
            }
            assert set(columns) == {"id", "browser_access_policy"}
            assert columns["browser_access_policy"]["nullable"] is True
            assert connection.execute(
                text(
                    """
                    SELECT browser_access_policy
                    FROM workflow_deployments
                    WHERE id = :id
                    """
                ),
                {"id": legacy_id},
            ).scalar_one() is None

            revision.downgrade()

            downgraded_columns = {
                column["name"]
                for column in inspect(connection).get_columns(
                    "workflow_deployments"
                )
            }
            assert downgraded_columns == {"id"}
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable or rejected the connection; "
            "connection details omitted",
            pytrace=False,
        ) from None
    finally:
        if test_engine is not None:
            test_engine.dispose()
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
