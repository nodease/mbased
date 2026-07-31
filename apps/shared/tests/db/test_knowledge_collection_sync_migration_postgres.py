from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from apps.shared.alembic.versions import (
    a7b8c9d0e1f2_add_knowledge_collection_sync_jobs as revision,
)
from apps.shared.db.session import engine
from apps.shared.services.ingestion.vector_store_service import (
    acquire_document_write_lock,
)
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session


def test_collection_sync_schema_preserves_snapshot_item_after_live_target_delete(
    monkeypatch,
) -> None:
    schema = f"test_kc_sync_{uuid.uuid4().hex}"
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("local PostgreSQL is unavailable; connection details omitted")
    transaction = connection.begin()
    try:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        connection.execute(text("CREATE TABLE organization (id UUID PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE users (id UUID PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE knowledge_collections ("
                "id UUID PRIMARY KEY, organization_id UUID NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE knowledge_bases ("
                "id UUID PRIMARY KEY, organization_id UUID NOT NULL, "
                "CONSTRAINT uq_test_kb_org UNIQUE (id, organization_id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE documents ("
                "id UUID PRIMARY KEY, knowledge_base_id UUID NOT NULL)"
            )
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(revision, "op", operations)

        revision.upgrade()
        inspector = inspect(connection)
        assert {
            "knowledge_collection_sync_jobs",
            "knowledge_collection_sync_job_items",
        } <= set(inspector.get_table_names(schema=schema))
        item_columns = {
            column["name"]: column
            for column in inspector.get_columns(
                "knowledge_collection_sync_job_items", schema=schema
            )
        }
        assert item_columns["target_revision"]["nullable"] is False
        job_indexes = {
            index["name"]: index
            for index in inspector.get_indexes(
                "knowledge_collection_sync_jobs", schema=schema
            )
        }
        assert job_indexes["uq_knowledge_collection_sync_jobs_active"]["unique"]
        assert "status" in str(
            job_indexes["uq_knowledge_collection_sync_jobs_active"].get(
                "dialect_options", {}
            )
        )

        organization_id = uuid.uuid4()
        other_organization_id = uuid.uuid4()
        user_id = uuid.uuid4()
        collection_id = uuid.uuid4()
        knowledge_base_id = uuid.uuid4()
        other_knowledge_base_id = uuid.uuid4()
        document_id = uuid.uuid4()
        other_document_id = uuid.uuid4()
        connection.execute(
            text("INSERT INTO organization (id) VALUES (:first), (:second)"),
            {"first": organization_id, "second": other_organization_id},
        )
        connection.execute(
            text("INSERT INTO users (id) VALUES (:id)"), {"id": user_id}
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_collections (id, organization_id) "
                "VALUES (:id, :organization_id)"
            ),
            {"id": collection_id, "organization_id": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases (id, organization_id) "
                "VALUES (:id, :organization_id), (:other_id, :organization_id)"
            ),
            {
                "id": knowledge_base_id,
                "other_id": other_knowledge_base_id,
                "organization_id": organization_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO documents (id, knowledge_base_id) "
                "VALUES (:first, :knowledge_base_id), "
                "(:second, :knowledge_base_id)"
            ),
            {
                "first": document_id,
                "second": other_document_id,
                "knowledge_base_id": knowledge_base_id,
            },
        )

        first_job_id = uuid.uuid4()
        _insert_job(
            connection,
            job_id=first_job_id,
            organization_id=organization_id,
            collection_id=collection_id,
            requested_by=user_id,
            request_key_hash="a" * 64,
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                _insert_job(
                    connection,
                    job_id=uuid.uuid4(),
                    organization_id=organization_id,
                    collection_id=collection_id,
                    requested_by=user_id,
                    request_key_hash="b" * 64,
                )

        connection.execute(
            text(
                "UPDATE knowledge_collection_sync_jobs "
                "SET status='succeeded', retryable=false, completed_at=now() "
                "WHERE id=:id"
            ),
            {"id": first_job_id},
        )
        second_job_id = uuid.uuid4()
        _insert_job(
            connection,
            job_id=second_job_id,
            organization_id=organization_id,
            collection_id=collection_id,
            requested_by=user_id,
            request_key_hash="b" * 64,
        )

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                _insert_job(
                    connection,
                    job_id=uuid.uuid4(),
                    organization_id=other_organization_id,
                    collection_id=collection_id,
                    requested_by=user_id,
                    request_key_hash="c" * 64,
                )

        connection.execute(
            text(
                "INSERT INTO knowledge_collection_sync_job_items ("
                "id, organization_id, job_id, collection_id, knowledge_base_id, "
                "document_id, position, target_revision) VALUES ("
                ":id, :organization_id, :job_id, :collection_id, "
                ":knowledge_base_id, :document_id, 0, :target_revision)"
            ),
            {
                "id": uuid.uuid4(),
                "organization_id": organization_id,
                "job_id": second_job_id,
                "collection_id": collection_id,
                "knowledge_base_id": knowledge_base_id,
                "document_id": document_id,
                "target_revision": "e" * 64,
            },
        )
        connection.execute(
            text("DELETE FROM documents WHERE id=:document_id"),
            {"document_id": document_id},
        )
        connection.execute(
            text("DELETE FROM knowledge_bases WHERE id=:knowledge_base_id"),
            {"knowledge_base_id": knowledge_base_id},
        )
        assert connection.execute(
            text(
                "SELECT count(*) FROM knowledge_collection_sync_job_items "
                "WHERE job_id=:job_id"
            ),
            {"job_id": second_job_id},
        ).scalar_one() == 1
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO knowledge_collection_sync_job_items ("
                        "id, organization_id, job_id, collection_id, "
                        "knowledge_base_id, document_id, position, target_revision) VALUES ("
                        ":id, :organization_id, :job_id, :collection_id, "
                        ":knowledge_base_id, :document_id, 2, :target_revision)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "organization_id": other_organization_id,
                        "job_id": second_job_id,
                        "collection_id": collection_id,
                        "knowledge_base_id": knowledge_base_id,
                        "document_id": other_document_id,
                        "target_revision": "f" * 64,
                    },
                )

        revision.downgrade()
        assert "knowledge_collection_sync_jobs" not in inspect(
            connection
        ).get_table_names(schema=schema)
    finally:
        transaction.rollback()
        connection.close()


def test_vector_store_document_lock_serializes_postgres_writers() -> None:
    first = Session(bind=engine)
    second = Session(bind=engine)
    try:
        first.connection()
        second.connection()
    except OperationalError:
        first.close()
        second.close()
        pytest.skip("local PostgreSQL is unavailable; connection details omitted")

    document_id = uuid.uuid4()
    second_started = Event()
    second_acquired = Event()
    failure: list[BaseException] = []

    def acquire_second() -> None:
        try:
            second_started.set()
            acquire_document_write_lock(second, document_id)
            second_acquired.set()
        except BaseException as exc:  # pragma: no cover - reported in parent thread
            failure.append(exc)

    try:
        acquire_document_write_lock(first, document_id)
        worker = Thread(target=acquire_second, daemon=True)
        worker.start()
        assert second_started.wait(timeout=2)
        assert not second_acquired.wait(timeout=0.2)
        first.commit()
        assert second_acquired.wait(timeout=2)
        worker.join(timeout=2)
        assert not failure
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()


def _insert_job(
    connection,
    *,
    job_id: uuid.UUID,
    organization_id: uuid.UUID,
    collection_id: uuid.UUID,
    requested_by: uuid.UUID,
    request_key_hash: str,
) -> None:
    now = datetime.now(timezone.utc)
    connection.execute(
        text(
            "INSERT INTO knowledge_collection_sync_jobs ("
            "id, organization_id, collection_id, requested_by, request_key_hash, "
            "target_snapshot_revision, previous_sync_state, status, total_count, "
            "next_retry_at, execution_deadline_at) VALUES ("
            ":id, :organization_id, :collection_id, :requested_by, "
            ":request_key_hash, :target_snapshot_revision, 'manual', 'queued', 1, "
            ":next_retry_at, :execution_deadline_at)"
        ),
        {
            "id": job_id,
            "organization_id": organization_id,
            "collection_id": collection_id,
            "requested_by": requested_by,
            "request_key_hash": request_key_hash,
            "target_snapshot_revision": "d" * 64,
            "next_retry_at": now,
            "execution_deadline_at": now + timedelta(minutes=30),
        },
    )
