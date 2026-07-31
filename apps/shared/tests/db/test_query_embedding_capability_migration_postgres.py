"""Opt-in PostgreSQL evidence for the query-embedding migration chain."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from apps.shared.alembic.versions import (
    b17c8d9e0f12_add_query_embedding_provider_capability as revision,
)
from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

ROOT_DIR = Path(__file__).resolve().parents[4]
RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "mbased_query_embedding"


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


def _start_alembic(
    *args: str,
    database: str,
    config: DisposablePostgresConfig,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "alembic", "-c", "apps/shared/alembic.ini", *args],
        cwd=ROOT_DIR,
        env=config.subprocess_environment(database=database, root_dir=ROOT_DIR),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


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


def _index_definitions(connection, table_name: str) -> dict[str, str]:
    rows = connection.execute(
        text(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = :table_name
            """
        ),
        {"table_name": table_name},
    ).mappings()
    return {row["indexname"]: row["indexdef"] for row in rows}


def _assert_upgraded_schema(connection) -> None:
    inspector = inspect(connection)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("llm_deployment_credential_policies")
    }
    assert columns["purpose"]["nullable"] is False
    assert "main_generation" in str(columns["purpose"]["default"])

    policy_checks = {
        check["name"]
        for check in inspector.get_check_constraints(
            "llm_deployment_credential_policies"
        )
    }
    capability_checks = {
        check["name"]
        for check in inspector.get_check_constraints(
            "provider_execution_capabilities"
        )
    }
    usage_checks = {
        check["name"]
        for check in inspector.get_check_constraints("provider_usage_operations")
    }
    assert "ck_llm_deploy_credential_policy_revision" in policy_checks
    assert (
        "ck_provider_execution_capability_query_embedding_output"
        in capability_checks
    )
    assert "ck_provider_usage_operation_query_embedding_output" in usage_checks

    policy_indexes = _index_definitions(
        connection,
        "llm_deployment_credential_policies",
    )
    assert "uq_llm_deploy_credential_policy_active" in policy_indexes
    query_index = policy_indexes[
        "uq_llm_deploy_credential_policy_active_query_embedding"
    ]
    assert "model_id" in query_index
    lookup_index = policy_indexes["ix_llm_deploy_credential_policy_lookup"]
    assert "purpose" in lookup_index
    assert "model_id" in lookup_index

    usage_indexes = _index_definitions(connection, "provider_usage_operations")
    assert "query_embedding" in usage_indexes[
        "ix_provider_usage_operation_workflow_budget_period"
    ]
    assert "query_embedding" in usage_indexes[
        "ix_provider_usage_operation_subject_cost_period"
    ]


def _insert_policy_row(
    connection,
    *,
    organization_id: uuid.UUID | None = None,
    deployment_id: uuid.UUID | None = None,
    node_location_digest: str = "a" * 64,
    purpose: str = "query_embedding",
    model_id: uuid.UUID | None = None,
) -> uuid.UUID:
    policy_id = uuid.uuid4()
    connection.execute(text("SET LOCAL session_replication_role = replica"))
    connection.execute(
        text(
            """
            INSERT INTO llm_deployment_credential_policies (
                id,
                organization_id,
                workflow_id,
                deployment_id,
                deployment_version,
                node_id,
                container_path,
                node_location_digest,
                purpose,
                model_id,
                credential_id,
                credential_principal_user_id,
                policy_revision,
                is_active
            ) VALUES (
                :id,
                :organization_id,
                :workflow_id,
                :deployment_id,
                1,
                'llm-1',
                CAST('[]' AS jsonb),
                :node_location_digest,
                :purpose,
                :model_id,
                :credential_id,
                :credential_principal_user_id,
                1,
                true
            )
            """
        ),
        {
            "id": policy_id,
            "organization_id": organization_id or uuid.uuid4(),
            "workflow_id": uuid.uuid4(),
            "deployment_id": deployment_id or uuid.uuid4(),
            "node_location_digest": node_location_digest,
            "purpose": purpose,
            "model_id": model_id or uuid.uuid4(),
            "credential_id": uuid.uuid4(),
            "credential_principal_user_id": uuid.uuid4(),
        },
    )
    return policy_id


def _insert_query_capability_guard_row(connection) -> uuid.UUID:
    capability_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    connection.execute(text("SET LOCAL session_replication_role = replica"))
    connection.execute(
        text(
            """
            INSERT INTO provider_execution_capabilities (
                id, organization_id, policy_id, workflow_id, deployment_id,
                deployment_version, node_id, container_path,
                node_location_digest, node_invocation_id,
                execution_admission_id, provider_attempt_id, purpose,
                provider_id, model_id, credential_id,
                credential_principal_user_id, execution_subject_kind,
                execution_subject_id, billing_principal_kind,
                billing_principal_id, audit_actor_kind, audit_actor_id,
                capability_revision, policy_revision, permission_revision,
                relation_revision, egress_revision, pricing_revision,
                input_token_cap, output_token_cap, cost_cap_microusd,
                state, expires_at
            ) VALUES (
                :id, :organization_id, :policy_id, :workflow_id, :deployment_id,
                1, 'llm-1', CAST('[]' AS jsonb), :node_location_digest,
                :node_invocation_id, :execution_admission_id,
                :provider_attempt_id, 'query_embedding', :provider_id,
                :model_id, :credential_id, :actor_id, 'user', :actor_id,
                'organization', :organization_id, 'user', :actor_id,
                1, 1, :revision, :revision, :revision, :revision,
                1, 0, 1, 'active', now() + interval '5 minutes'
            )
            """
        ),
        {
            "id": capability_id,
            "organization_id": organization_id,
            "policy_id": uuid.uuid4(),
            "workflow_id": uuid.uuid4(),
            "deployment_id": uuid.uuid4(),
            "node_location_digest": "b" * 64,
            "node_invocation_id": uuid.uuid4(),
            "execution_admission_id": uuid.uuid4(),
            "provider_attempt_id": uuid.uuid4(),
            "provider_id": uuid.uuid4(),
            "model_id": uuid.uuid4(),
            "credential_id": uuid.uuid4(),
            "actor_id": actor_id,
            "revision": "c" * 64,
        },
    )
    return capability_id


def _insert_query_usage_guard_row(connection) -> uuid.UUID:
    operation_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    connection.execute(text("SET LOCAL session_replication_role = replica"))
    connection.execute(
        text(
            """
            INSERT INTO provider_usage_operations (
                id, organization_id, workflow_id, deployment_id,
                deployment_version, node_id, container_path,
                node_invocation_id, execution_admission_id,
                provider_attempt_id, purpose, capability_id,
                capability_revision, capability_expires_at, policy_id,
                policy_revision, provider_id, model_id, model_api_id,
                credential_id, permission_revision, relation_revision,
                egress_revision, pricing_revision, input_price_per_1k,
                output_price_per_1k, input_token_cap, output_token_cap,
                cost_cap_microusd, admitted_input_tokens,
                admitted_output_tokens, execution_subject_kind,
                execution_subject_id, credential_principal_kind,
                credential_principal_id, billing_principal_kind,
                billing_principal_id, audit_actor_kind, audit_actor_id,
                state
            ) VALUES (
                :id, :organization_id, :workflow_id, :deployment_id,
                1, 'llm-1', CAST('[]' AS jsonb), :node_invocation_id,
                :execution_admission_id, :provider_attempt_id,
                'query_embedding', :capability_id, 1,
                now() + interval '5 minutes', :policy_id, 1,
                :provider_id, :model_id, 'embed-safe', :credential_id,
                :revision, :revision, :revision, :revision,
                0, 0, 1, 0, 1, 1, 0, 'user', :actor_id,
                'user', :actor_id, 'organization', :organization_id,
                'user', :actor_id, 'intent'
            )
            """
        ),
        {
            "id": operation_id,
            "organization_id": organization_id,
            "workflow_id": uuid.uuid4(),
            "deployment_id": uuid.uuid4(),
            "node_invocation_id": uuid.uuid4(),
            "execution_admission_id": uuid.uuid4(),
            "provider_attempt_id": uuid.uuid4(),
            "capability_id": uuid.uuid4(),
            "policy_id": uuid.uuid4(),
            "provider_id": uuid.uuid4(),
            "model_id": uuid.uuid4(),
            "credential_id": uuid.uuid4(),
            "revision": "d" * 64,
            "actor_id": actor_id,
        },
    )
    return operation_id


def _wait_for_exclusive_table_lock(connection, *, timeout_seconds: float = 10) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        waiting = connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_locks AS pending
                    JOIN pg_class AS relation ON relation.oid = pending.relation
                    WHERE NOT pending.granted
                      AND pending.mode = 'AccessExclusiveLock'
                      AND relation.relname IN (
                          'llm_deployment_credential_policies',
                          'provider_execution_capabilities',
                          'provider_usage_operations'
                      )
                )
                """
            )
        ).scalar()
        if waiting:
            return
        time.sleep(0.05)
    pytest.fail("downgrade did not wait for the protected table lock")


def _wait_for_transaction_dependency(connection, *, timeout_seconds: float = 10) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        waiting = connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_locks "
                "WHERE NOT granted AND locktype = 'transactionid'"
                ")"
            )
        ).scalar()
        if waiting:
            return
        time.sleep(0.05)
    pytest.fail("competing policy write did not wait for the active transaction")


def _assert_policy_slot_constraints(test_engine) -> None:
    organization_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    node_location_digest = "e" * 64
    model_a = uuid.uuid4()
    model_b = uuid.uuid4()
    inserted_ids = []
    with test_engine.begin() as connection:
        inserted_ids.append(
            _insert_policy_row(
                connection,
                organization_id=organization_id,
                deployment_id=deployment_id,
                node_location_digest=node_location_digest,
                purpose="main_generation",
                model_id=model_a,
            )
        )
        inserted_ids.append(
            _insert_policy_row(
                connection,
                organization_id=organization_id,
                deployment_id=deployment_id,
                node_location_digest=node_location_digest,
                model_id=model_a,
            )
        )
        inserted_ids.append(
            _insert_policy_row(
                connection,
                organization_id=organization_id,
                deployment_id=deployment_id,
                node_location_digest=node_location_digest,
                model_id=model_b,
            )
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                _insert_policy_row(
                    connection,
                    organization_id=organization_id,
                    deployment_id=deployment_id,
                    node_location_digest=node_location_digest,
                    purpose="main_generation",
                    model_id=model_b,
                )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                _insert_policy_row(
                    connection,
                    organization_id=organization_id,
                    deployment_id=deployment_id,
                    node_location_digest=node_location_digest,
                    model_id=model_a,
                )

        count = connection.execute(
            text(
                "SELECT count(*) FROM llm_deployment_credential_policies "
                "WHERE organization_id = :organization_id "
                "AND deployment_id = :deployment_id"
            ),
            {
                "organization_id": organization_id,
                "deployment_id": deployment_id,
            },
        ).scalar_one()
        assert count == 3
        connection.execute(
            text(
                "DELETE FROM llm_deployment_credential_policies "
                "WHERE id = ANY(:policy_ids)"
            ),
            {"policy_ids": inserted_ids},
        )


def _assert_concurrent_policy_first_write(test_engine) -> None:
    organization_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    model_id = uuid.uuid4()
    node_location_digest = "f" * 64
    second_started = threading.Event()
    first_connection = test_engine.connect()
    first_transaction = first_connection.begin()
    first_policy_id = _insert_policy_row(
        first_connection,
        organization_id=organization_id,
        deployment_id=deployment_id,
        node_location_digest=node_location_digest,
        model_id=model_id,
    )

    def insert_competing_policy() -> str:
        try:
            with test_engine.begin() as connection:
                connection.execute(
                    text("SET LOCAL session_replication_role = replica")
                )
                second_started.set()
                _insert_policy_row(
                    connection,
                    organization_id=organization_id,
                    deployment_id=deployment_id,
                    node_location_digest=node_location_digest,
                    model_id=model_id,
                )
        except IntegrityError:
            return "conflict"
        return "inserted"

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(insert_competing_policy)
            assert second_started.wait(timeout=5)
            with test_engine.connect() as observer:
                _wait_for_transaction_dependency(observer)
            first_transaction.commit()
            assert future.result(timeout=10) == "conflict"
    finally:
        if first_transaction.is_active:
            first_transaction.rollback()
        first_connection.close()

    with test_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM llm_deployment_credential_policies "
                "WHERE id = :policy_id"
            ),
            {"policy_id": first_policy_id},
        )


def _assert_query_rows_guard_downgrade(
    test_engine,
    *,
    database: str,
    config: DisposablePostgresConfig,
) -> None:
    assert isinstance(revision.down_revision, str)
    cases = (
        (
            "llm_deployment_credential_policies",
            _insert_policy_row,
        ),
        (
            "provider_execution_capabilities",
            _insert_query_capability_guard_row,
        ),
        (
            "provider_usage_operations",
            _insert_query_usage_guard_row,
        ),
    )
    for table_name, insert_row in cases:
        with test_engine.begin() as connection:
            row_id = insert_row(connection)
        assert (
            _alembic_returncode(
                "downgrade",
                revision.down_revision,
                database=database,
                config=config,
            )
            != 0
        )
        with test_engine.begin() as connection:
            connection.execute(
                text(f"DELETE FROM {table_name} WHERE id = :row_id"),
                {"row_id": row_id},
            )


def _assert_downgrade_serializes_with_query_writer(
    test_engine,
    *,
    database: str,
    config: DisposablePostgresConfig,
) -> None:
    assert isinstance(revision.down_revision, str)
    writer = test_engine.connect()
    transaction = writer.begin()
    policy_id = _insert_policy_row(writer)
    process = _start_alembic(
        "downgrade",
        revision.down_revision,
        database=database,
        config=config,
    )
    try:
        with test_engine.connect() as observer:
            _wait_for_exclusive_table_lock(observer)
        transaction.commit()
        process.communicate(timeout=180)
        assert process.returncode != 0
    finally:
        if transaction.is_active:
            transaction.rollback()
        writer.close()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)

    with test_engine.begin() as connection:
        columns = {
            column["name"]
            for column in inspect(connection).get_columns(
                "llm_deployment_credential_policies"
            )
        }
        assert "purpose" in columns
        assert connection.execute(
            text(
                "SELECT purpose FROM llm_deployment_credential_policies "
                "WHERE id = :policy_id"
            ),
            {"policy_id": policy_id},
        ).scalar_one() == "query_embedding"
        connection.execute(
            text(
                "DELETE FROM llm_deployment_credential_policies "
                "WHERE id = :policy_id"
            ),
            {"policy_id": policy_id},
        )


@pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run disposable PostgreSQL migration integration",
)
def test_query_embedding_upgrade_guarded_downgrade_and_reupgrade() -> None:
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

        extension_engine = create_engine(
            config.database_url(database),
            isolation_level="AUTOCOMMIT",
        )
        try:
            with extension_engine.connect() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        finally:
            extension_engine.dispose()

        assert isinstance(revision.down_revision, str)
        _run_alembic(
            "upgrade",
            revision.down_revision,
            database=database,
            config=config,
        )
        _run_alembic(
            "upgrade",
            revision.revision,
            database=database,
            config=config,
        )

        test_engine = create_engine(config.database_url(database))
        with test_engine.connect() as connection:
            _assert_upgraded_schema(connection)
        _assert_policy_slot_constraints(test_engine)
        _assert_concurrent_policy_first_write(test_engine)
        _assert_query_rows_guard_downgrade(
            test_engine,
            database=database,
            config=config,
        )
        _assert_downgrade_serializes_with_query_writer(
            test_engine,
            database=database,
            config=config,
        )
        test_engine.dispose()
        test_engine = None

        _run_alembic(
            "downgrade",
            revision.down_revision,
            database=database,
            config=config,
        )
        inspection_engine = create_engine(config.database_url(database))
        try:
            with inspection_engine.connect() as connection:
                columns = {
                    column["name"]
                    for column in inspect(connection).get_columns(
                        "llm_deployment_credential_policies"
                    )
                }
                assert "purpose" not in columns
                policy_indexes = _index_definitions(
                    connection,
                    "llm_deployment_credential_policies",
                )
                assert (
                    "uq_llm_deploy_credential_policy_active_query_embedding"
                    not in policy_indexes
                )
                assert "purpose" not in policy_indexes[
                    "uq_llm_deploy_credential_policy_active"
                ]
        finally:
            inspection_engine.dispose()

        _run_alembic("upgrade", "head", database=database, config=config)
        inspection_engine = create_engine(config.database_url(database))
        try:
            with inspection_engine.connect() as connection:
                _assert_upgraded_schema(connection)
        finally:
            inspection_engine.dispose()
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
                    connection.execute(
                        text(f"DROP DATABASE IF EXISTS {quoted_database}")
                    )
            except OperationalError:
                raise pytest.fail.Exception(
                    "disposable PostgreSQL cleanup failed; "
                    "connection details omitted",
                    pytrace=False,
                ) from None
        admin_engine.dispose()
