from __future__ import annotations

import uuid

import pytest
from apps.shared.db.session import engine
from apps.shared.services.ingestion.vector_store_service import VectorStoreService
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


def test_unversioned_chunk_delete_preserves_versioned_rows() -> None:
    schema = f"test_vector_scope_{uuid.uuid4().hex}"
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("local PostgreSQL is unavailable; connection details omitted")
    transaction = connection.begin()
    try:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        connection.execute(
            text(
                "CREATE TABLE document_chunks ("
                "id UUID PRIMARY KEY, document_id UUID NOT NULL, "
                "document_version_id UUID NULL)"
            )
        )

        document_id = uuid.uuid4()
        unversioned_id = uuid.uuid4()
        active_version_chunk_id = uuid.uuid4()
        historical_version_chunk_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO document_chunks (id, document_id, document_version_id) "
                "VALUES (:unversioned_id, :document_id, NULL), "
                "(:active_id, :document_id, :active_version_id), "
                "(:historical_id, :document_id, :historical_version_id)"
            ),
            {
                "unversioned_id": unversioned_id,
                "active_id": active_version_chunk_id,
                "historical_id": historical_version_chunk_id,
                "document_id": document_id,
                "active_version_id": uuid.uuid4(),
                "historical_version_id": uuid.uuid4(),
            },
        )

        with Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
        ) as session:
            deleted = VectorStoreService(
                db=session,
                user_id=uuid.uuid4(),
            )._chunk_scope_query(document_id, None).delete(
                synchronize_session=False
            )
            session.flush()
            remaining_ids = set(
                session.execute(
                    text(
                        "SELECT id FROM document_chunks "
                        "WHERE document_id=:document_id"
                    ),
                    {"document_id": document_id},
                ).scalars()
            )
            assert deleted == 1
            assert remaining_ids == {
                active_version_chunk_id,
                historical_version_chunk_id,
            }
            assert unversioned_id not in remaining_ids
    finally:
        transaction.rollback()
        connection.close()
