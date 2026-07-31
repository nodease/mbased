from __future__ import annotations

import os
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from apps.shared.alembic.versions import (
    ae2f3a4b5c6d_add_canonical_node_locations as migration,
)
from apps.shared.domain.workflow_node_location import CanonicalWorkflowNodeLocation
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_node_location"


def test_canonical_node_location_migration_follows_provider_capability_head():
    assert migration.revision == "ae2f3a4b5c6d"
    assert migration.down_revision == "ad1e2f3a4b5c"


def test_root_backfill_digest_matches_the_domain_contract():
    assert migration._root_location_digest("llm-1") == (
        CanonicalWorkflowNodeLocation((), "llm-1").digest
    )


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL migration evidence",
)
def test_location_migration_backfills_and_separates_nested_policy_keys(
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
        with test_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE llm_deployment_credential_policies (
                        id UUID PRIMARY KEY,
                        organization_id UUID NOT NULL,
                        deployment_id UUID NOT NULL,
                        deployment_version INTEGER NOT NULL,
                        node_id VARCHAR(255) NOT NULL,
                        is_active BOOLEAN NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE UNIQUE INDEX uq_llm_deploy_credential_policy_active
                    ON llm_deployment_credential_policies (
                        organization_id, deployment_id, deployment_version, node_id
                    ) WHERE is_active
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE INDEX ix_llm_deploy_credential_policy_lookup
                    ON llm_deployment_credential_policies (
                        organization_id, deployment_id, deployment_version,
                        node_id, is_active
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE provider_execution_capabilities (
                        id UUID PRIMARY KEY,
                        node_id VARCHAR(255) NOT NULL
                    )
                    """
                )
            )
            policy_id = uuid.uuid4()
            capability_id = uuid.uuid4()
            organization_id = uuid.uuid4()
            deployment_id = uuid.uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO llm_deployment_credential_policies (
                        id, organization_id, deployment_id, deployment_version,
                        node_id, is_active
                    ) VALUES (
                        :id, :organization_id, :deployment_id, 1, 'llm-1', true
                    )
                    """
                ),
                {
                    "id": policy_id,
                    "organization_id": organization_id,
                    "deployment_id": deployment_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO provider_execution_capabilities (id, node_id)
                    VALUES (:capability_id, 'llm-1')
                    """
                ),
                {
                    "capability_id": capability_id,
                },
            )
            monkeypatch.setattr(
                migration,
                "op",
                Operations(MigrationContext.configure(connection)),
            )
            monkeypatch.setattr(migration, "_BACKFILL_BATCH_SIZE", 1)
            backfill_selects = {
                "llm_deployment_credential_policies": 0,
                "provider_execution_capabilities": 0,
            }

            def count_backfill_selects(
                _connection,
                _cursor,
                statement,
                _parameters,
                _context,
                _executemany,
            ) -> None:
                normalized = statement.strip().lower()
                if not normalized.startswith("select"):
                    return
                for table_name in backfill_selects:
                    if table_name in normalized:
                        backfill_selects[table_name] += 1

            event.listen(connection, "before_cursor_execute", count_backfill_selects)
            try:
                migration.upgrade()
            finally:
                event.remove(
                    connection,
                    "before_cursor_execute",
                    count_backfill_selects,
                )

            assert all(count >= 2 for count in backfill_selects.values())

            expected_root = CanonicalWorkflowNodeLocation((), "llm-1")
            root_row = connection.execute(
                text(
                    """
                    SELECT container_path, node_location_digest
                    FROM llm_deployment_credential_policies WHERE id = :id
                    """
                ),
                {"id": policy_id},
            ).mappings().one()
            assert root_row["container_path"] == []
            assert root_row["node_location_digest"] == expected_root.digest
            assert connection.execute(
                text(
                    """
                    SELECT node_location_digest
                    FROM provider_execution_capabilities WHERE id = :id
                    """
                ),
                {"id": capability_id},
            ).scalar_one() == expected_root.digest

            nested = CanonicalWorkflowNodeLocation(
                (("loop", "loop-a"),),
                "llm-1",
            )
            nested_id = uuid.uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO llm_deployment_credential_policies (
                        id, organization_id, deployment_id, deployment_version,
                        node_id, is_active, container_path, node_location_digest
                    ) VALUES (
                        :id, :organization_id, :deployment_id, 1, 'llm-1', true,
                        CAST(:container_path AS jsonb), :digest
                    )
                    """
                ),
                {
                    "id": nested_id,
                    "organization_id": organization_id,
                    "deployment_id": deployment_id,
                    "container_path": '[{"kind":"loop","node_id":"loop-a"}]',
                    "digest": nested.digest,
                },
            )
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            INSERT INTO llm_deployment_credential_policies (
                                id, organization_id, deployment_id,
                                deployment_version, node_id, is_active,
                                container_path, node_location_digest
                            ) VALUES (
                                :id, :organization_id, :deployment_id, 1,
                                'llm-1', true,
                                CAST(:container_path AS jsonb), :digest
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid4(),
                            "organization_id": organization_id,
                            "deployment_id": deployment_id,
                            "container_path": (
                                '[{"kind":"loop","node_id":"loop-a"}]'
                            ),
                            "digest": nested.digest,
                        },
                    )
            assert connection.execute(
                text(
                    "SELECT count(*) FROM llm_deployment_credential_policies "
                    "WHERE is_active"
                )
            ).scalar_one() == 2

            with pytest.raises(RuntimeError):
                migration.downgrade()

            connection.execute(
                text("DELETE FROM llm_deployment_credential_policies WHERE id = :id"),
                {"id": nested_id},
            )
            migration.downgrade()
            assert "container_path" not in {
                column["name"]
                for column in inspect(connection).get_columns(
                    "llm_deployment_credential_policies"
                )
            }
    except OperationalError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL is unavailable; connection details omitted",
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
                    connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup failed; details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()
