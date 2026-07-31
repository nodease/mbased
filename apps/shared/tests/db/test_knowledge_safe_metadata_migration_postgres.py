import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from apps.shared.alembic.versions import (
    fa7c8d9e0f12_add_knowledge_base_safe_metadata as revision,
)
from apps.shared.db.session import engine
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError


def test_safe_metadata_revision_upgrades_and_downgrades_in_isolated_schema(
    monkeypatch,
):
    schema = f"test_safe_metadata_{uuid.uuid4().hex}"
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("PostgreSQL integration database is unavailable")
    transaction = connection.begin()
    try:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        connection.execute(
            text(
                """
                CREATE TABLE knowledge_bases (
                    id UUID PRIMARY KEY
                )
                """
            )
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(revision, "op", operations)

        revision.upgrade()
        upgraded_columns = {
            column["name"]
            for column in inspect(connection).get_columns(
                "knowledge_bases",
                schema=schema,
            )
        }
        assert upgraded_columns == {"id", "safe_metadata"}
        stored = connection.execute(
            text(
                """
                INSERT INTO knowledge_bases (id)
                VALUES (:id)
                RETURNING safe_metadata
                """
            ),
            {"id": uuid.uuid4()},
        ).scalar_one()
        assert stored == {}

        revision.downgrade()
        downgraded_columns = {
            column["name"]
            for column in inspect(connection).get_columns(
                "knowledge_bases",
                schema=schema,
            )
        }
        assert downgraded_columns == {"id"}
    finally:
        transaction.rollback()
        connection.close()
