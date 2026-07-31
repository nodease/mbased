from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from apps.shared.db.session import engine
from apps.shared.services.knowledge_collection_sync_targets import (
    scan_collection_sync_targets,
)
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


def test_target_scan_enforces_document_atom_and_tenant_snapshot() -> None:
    schema = f"test_kc_sync_targets_{uuid.uuid4().hex}"
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
                "CREATE TABLE knowledge_bases ("
                "id UUID PRIMARY KEY, organization_id UUID, "
                "lifecycle_state VARCHAR(50) NOT NULL, "
                "sync_state VARCHAR(50) NOT NULL, source_identity_id UUID)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE knowledge_collection_items ("
                "id UUID PRIMARY KEY, organization_id UUID NOT NULL, "
                "collection_id UUID NOT NULL, knowledge_base_id UUID NOT NULL, "
                "rank INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE documents ("
                "id UUID PRIMARY KEY, knowledge_base_id UUID NOT NULL, "
                "source_type VARCHAR(16) NOT NULL, updated_at TIMESTAMPTZ)"
            )
        )

        organization_id = uuid.uuid4()
        other_organization_id = uuid.uuid4()
        collection_id = uuid.uuid4()
        kb_id = uuid.uuid4()
        item_id = uuid.uuid4()
        document_id = uuid.uuid4()
        created_at = datetime(2026, 7, 15, tzinfo=timezone.utc)
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, organization_id, lifecycle_state, sync_state) "
                "VALUES (:id, :organization_id, 'active', 'manual')"
            ),
            {"id": kb_id, "organization_id": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_collection_items "
                "(id, organization_id, collection_id, knowledge_base_id, rank, created_at) "
                "VALUES (:id, :organization_id, :collection_id, :kb_id, 0, :created_at)"
            ),
            {
                "id": item_id,
                "organization_id": organization_id,
                "collection_id": collection_id,
                "kb_id": kb_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, knowledge_base_id, source_type, updated_at) "
                "VALUES (:id, :kb_id, 'DB', :updated_at)"
            ),
            {
                "id": document_id,
                "kb_id": kb_id,
                "updated_at": created_at,
            },
        )

        initial = _scan(connection, organization_id, collection_id)
        assert initial.is_supported
        initial_snapshot = initial.snapshot_revision(collection_id)

        connection.execute(
            text("UPDATE documents SET updated_at=:updated_at WHERE id=:id"),
            {
                "id": document_id,
                "updated_at": created_at + timedelta(minutes=1),
            },
        )
        content_changed = _scan(connection, organization_id, collection_id)
        assert content_changed.snapshot_revision(collection_id) == initial_snapshot
        assert (
            content_changed.targets[0].document_updated_at
            != initial.targets[0].document_updated_at
        )

        sibling_file_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, knowledge_base_id, source_type, updated_at) "
                "VALUES (:id, :kb_id, 'FILE', :updated_at)"
            ),
            {"id": sibling_file_id, "kb_id": kb_id, "updated_at": created_at},
        )
        multi_document = _scan(connection, organization_id, collection_id)
        assert multi_document.has_multi_document_target
        assert multi_document.blocking_reason == "sync.not_supported"
        connection.execute(
            text("DELETE FROM documents WHERE id=:id"), {"id": sibling_file_id}
        )

        other_kb_id = uuid.uuid4()
        other_item_id = uuid.uuid4()
        other_document_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, organization_id, lifecycle_state, sync_state) "
                "VALUES (:id, :organization_id, 'active', 'manual')"
            ),
            {"id": other_kb_id, "organization_id": other_organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_collection_items "
                "(id, organization_id, collection_id, knowledge_base_id, rank, created_at) "
                "VALUES (:id, :organization_id, :collection_id, :kb_id, 1, :created_at)"
            ),
            {
                "id": other_item_id,
                "organization_id": other_organization_id,
                "collection_id": collection_id,
                "kb_id": other_kb_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, knowledge_base_id, source_type, updated_at) "
                "VALUES (:id, :kb_id, 'API', :updated_at)"
            ),
            {
                "id": other_document_id,
                "kb_id": other_kb_id,
                "updated_at": created_at,
            },
        )
        assert _scan(connection, organization_id, collection_id).is_supported

        second_kb_id = uuid.uuid4()
        second_item_id = uuid.uuid4()
        second_document_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, organization_id, lifecycle_state, sync_state) "
                "VALUES (:id, :organization_id, 'active', 'manual')"
            ),
            {"id": second_kb_id, "organization_id": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_collection_items "
                "(id, organization_id, collection_id, knowledge_base_id, rank, created_at) "
                "VALUES (:id, :organization_id, :collection_id, :kb_id, 1, :created_at)"
            ),
            {
                "id": second_item_id,
                "organization_id": organization_id,
                "collection_id": collection_id,
                "kb_id": second_kb_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, knowledge_base_id, source_type, updated_at) "
                "VALUES (:id, :kb_id, 'DB', :updated_at)"
            ),
            {
                "id": second_document_id,
                "kb_id": second_kb_id,
                "updated_at": created_at,
            },
        )
        membership_changed = _scan(connection, organization_id, collection_id)
        assert membership_changed.is_supported
        assert membership_changed.snapshot_revision(collection_id) != initial_snapshot

        connection.execute(
            text("UPDATE documents SET source_type='API' WHERE id=:id"),
            {"id": second_document_id},
        )
        api_child = _scan(connection, organization_id, collection_id)
        assert api_child.has_api_document
        assert api_child.blocking_reason == "sync.not_supported"
    finally:
        transaction.rollback()
        connection.close()


def _scan(connection, organization_id: uuid.UUID, collection_id: uuid.UUID):
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        return scan_collection_sync_targets(
            session,
            organization_id,
            collection_id,
            limit=101,
        )
