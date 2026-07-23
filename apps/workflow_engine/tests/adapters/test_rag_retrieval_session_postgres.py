# ruff: noqa: E402

import os
from uuid import uuid4

import pytest

RUN_ENV = "NODEASE_RUN_DISPOSABLE_DB_TEST"
DB_PREFIX = "nodease_rag_fanout_test"

if os.getenv(RUN_ENV) != "1":
    pytest.skip(
        f"set {RUN_ENV}=1 to run disposable RAG fan-out evidence",
        allow_module_level=True,
    )

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from apps.shared.tests.helpers.disposable_postgres import (
    DisposablePostgresConfig,
    DisposablePostgresConfigurationError,
    quote_disposable_database_name,
)
from apps.workflow_engine.adapters.rag_retrieval_session import (
    RAGRetrievalSessionRunner,
    RAGRetrievalSessionTimeout,
)
from apps.workflow_engine.adapters.rag_retrieval_executor import (
    NativeThreadRAGRetrievalCancellation,
)


@pytest.fixture
def disposable_rag_database():
    try:
        config = DisposablePostgresConfig.from_environment()
    except DisposablePostgresConfigurationError:
        raise pytest.fail.Exception(
            "disposable PostgreSQL connection settings are not safely configured",
            pytrace=False,
        ) from None

    database = f"{DB_PREFIX}_{uuid4().hex[:12]}"
    quoted_database = quote_disposable_database_name(database, prefix=DB_PREFIX)
    admin_engine = create_engine(
        config.database_url(config.maintenance_database),
        isolation_level="AUTOCOMMIT",
    )
    engine = None
    database_created = False
    try:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f"CREATE DATABASE {quoted_database}"))
            database_created = True
            engine = create_engine(config.database_url(database), pool_size=1)
        except SQLAlchemyError:
            raise pytest.fail.Exception(
                "disposable PostgreSQL setup failed",
                pytrace=False,
            ) from None
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        try:
            if database_created:
                with admin_engine.connect() as connection:
                    connection.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) "
                            "FROM pg_stat_activity "
                            "WHERE datname = :database "
                            "AND pid <> pg_backend_pid()"
                        ),
                        {"database": database},
                    )
                    connection.execute(text(f"DROP DATABASE {quoted_database}"))
        except SQLAlchemyError:
            raise pytest.fail.Exception(
                "disposable PostgreSQL cleanup failed",
                pytrace=False,
            ) from None
        finally:
            admin_engine.dispose()


def test_rag_session_is_read_only_and_local_timeout_does_not_leak(
    disposable_rag_database,
):
    session_factory = sessionmaker(bind=disposable_rag_database)
    runner = RAGRetrievalSessionRunner(session_factory=session_factory)

    settings = runner.run(
        timeout_ms=500,
        cancellation=NativeThreadRAGRetrievalCancellation(),
        operation=lambda session: (
            session.execute(text("SHOW transaction_read_only")).scalar_one(),
            session.execute(text("SHOW statement_timeout")).scalar_one(),
        ),
    )

    assert settings[0] == "on"
    assert settings[1].endswith("ms")
    assert 1 <= int(settings[1][:-2]) <= 500
    with session_factory() as session:
        assert session.execute(text("SHOW statement_timeout")).scalar_one() == "0"
        assert session.execute(text("SELECT 1")).scalar_one() == 1


def test_rag_statement_timeout_rolls_back_before_connection_reuse(
    disposable_rag_database,
):
    session_factory = sessionmaker(bind=disposable_rag_database)
    runner = RAGRetrievalSessionRunner(session_factory=session_factory)

    with pytest.raises(RAGRetrievalSessionTimeout):
        runner.run(
            timeout_ms=50,
            cancellation=NativeThreadRAGRetrievalCancellation(),
            operation=lambda session: session.execute(
                text("SELECT pg_sleep(0.2)")
            ).scalar_one(),
        )

    with session_factory() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1


def test_rag_statement_timeout_shrinks_across_multiple_statements(
    disposable_rag_database,
):
    session_factory = sessionmaker(bind=disposable_rag_database)
    runner = RAGRetrievalSessionRunner(session_factory=session_factory)

    def exceed_cumulative_budget(session):
        session.execute(text("SELECT pg_sleep(0.03)")).scalar_one()
        session.execute(text("SELECT pg_sleep(0.03)")).scalar_one()

    with pytest.raises(RAGRetrievalSessionTimeout):
        runner.run(
            timeout_ms=50,
            cancellation=NativeThreadRAGRetrievalCancellation(),
            operation=exceed_cumulative_budget,
        )

    with session_factory() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
