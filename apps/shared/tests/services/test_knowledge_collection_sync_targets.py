from __future__ import annotations

import uuid
from datetime import datetime, timezone

from apps.shared.services.knowledge_collection_sync_targets import (
    scan_collection_sync_targets,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def test_target_scan_uses_tenant_scope_and_rejects_multi_document_db_kb() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    organization_id = uuid.uuid4()
    other_organization_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE knowledge_bases ("
                "id CHAR(32) PRIMARY KEY, organization_id CHAR(32), "
                "lifecycle_state VARCHAR(50), sync_state VARCHAR(50), "
                "source_identity_id CHAR(32))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE knowledge_collection_items ("
                "id CHAR(32) PRIMARY KEY, organization_id CHAR(32), "
                "collection_id CHAR(32), knowledge_base_id CHAR(32), "
                "rank INTEGER, created_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE documents ("
                "id CHAR(32) PRIMARY KEY, knowledge_base_id CHAR(32), "
                "source_type VARCHAR(16), updated_at DATETIME)"
            )
        )
        _insert_kb_target(
            connection,
            organization_id=organization_id,
            collection_id=collection_id,
            kb_id=kb_id,
            rank=0,
            source_type="DB",
            now=now,
        )
        _insert_kb_target(
            connection,
            organization_id=other_organization_id,
            collection_id=collection_id,
            kb_id=uuid.uuid4(),
            rank=1,
            source_type="API",
            now=now,
        )

    with Session(engine) as session:
        initial = scan_collection_sync_targets(
            session,
            organization_id,
            collection_id,
            limit=101,
        )
        assert initial.is_supported
        assert len(initial.targets) == 1

        session.execute(
            text(
                "INSERT INTO documents "
                "(id, knowledge_base_id, source_type, updated_at) "
                "VALUES (:id, :kb_id, 'FILE', :updated_at)"
            ),
            {"id": uuid.uuid4().hex, "kb_id": kb_id.hex, "updated_at": now},
        )
        session.commit()

        multi_document = scan_collection_sync_targets(
            session,
            organization_id,
            collection_id,
            limit=101,
        )
        assert multi_document.has_multi_document_target
        assert multi_document.blocking_reason == "sync.not_supported"


def _insert_kb_target(
    connection,
    *,
    organization_id: uuid.UUID,
    collection_id: uuid.UUID,
    kb_id: uuid.UUID,
    rank: int,
    source_type: str,
    now: datetime,
) -> None:
    connection.execute(
        text(
            "INSERT INTO knowledge_bases "
            "(id, organization_id, lifecycle_state, sync_state, source_identity_id) "
            "VALUES (:id, :organization_id, 'active', 'manual', NULL)"
        ),
        {"id": kb_id.hex, "organization_id": organization_id.hex},
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_collection_items "
            "(id, organization_id, collection_id, knowledge_base_id, rank, created_at) "
            "VALUES (:id, :organization_id, :collection_id, :kb_id, :rank, :created_at)"
        ),
        {
            "id": uuid.uuid4().hex,
            "organization_id": organization_id.hex,
            "collection_id": collection_id.hex,
            "kb_id": kb_id.hex,
            "rank": rank,
            "created_at": now,
        },
    )
    connection.execute(
        text(
            "INSERT INTO documents "
            "(id, knowledge_base_id, source_type, updated_at) "
            "VALUES (:id, :kb_id, :source_type, :updated_at)"
        ),
        {
            "id": uuid.uuid4().hex,
            "kb_id": kb_id.hex,
            "source_type": source_type,
            "updated_at": now,
        },
    )
