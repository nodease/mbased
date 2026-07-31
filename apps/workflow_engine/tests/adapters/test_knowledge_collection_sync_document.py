from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest
from sqlalchemy import and_
from sqlalchemy.dialects import postgresql

from apps.shared.db.models.knowledge import (
    Document,
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
    SourceType,
)
from apps.shared.domain.knowledge_collection_sync import sync_target_revision
from apps.shared.services.connection_runtime_snapshot import (
    ConnectionRuntimeSnapshotProvider,
)
from apps.workflow_engine.adapters import knowledge_collection_sync_document as adapter_module
from apps.workflow_engine.adapters.knowledge_collection_sync_document import (
    SqlAlchemyKnowledgeCollectionSyncDocument,
)
from apps.workflow_engine.application.knowledge_collection_sync import (
    SyncTargetChanged,
    SyncTargetConfigurationInvalid,
    WorkerSyncItem,
)

SNAPSHOT_TIME = datetime(2026, 7, 15, tzinfo=timezone.utc)
MEMBERSHIP_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")


class Query:
    def __init__(self, row) -> None:
        self.row = row
        self.criteria = []

    def filter(self, *args):
        self.criteria.extend(args)
        return self

    def with_for_update(self):
        return self

    def one_or_none(self):
        return self.row


class Db:
    def __init__(self, rows: dict[object, object]) -> None:
        self.rows = rows
        self.queries: dict[object, list[Query]] = {}
        self.events: list[str] = []

    def query(self, model):
        self.events.append(f"query:{getattr(model, '__name__', 'unknown')}")
        query = Query(self.rows.get(model))
        self.queries.setdefault(model, []).append(query)
        return query

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))


def _item() -> WorkerSyncItem:
    collection_id = uuid.uuid4()
    knowledge_base_id = uuid.uuid4()
    document_id = uuid.uuid4()
    return WorkerSyncItem(
        item_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        collection_id=collection_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        position=0,
        target_revision=sync_target_revision(
            collection_id=collection_id,
            collection_item_id=MEMBERSHIP_ID,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            item_rank=0,
            item_created_at=SNAPSHOT_TIME,
            document_updated_at=SNAPSHOT_TIME,
        ),
        status="pending",
        attempt_count=0,
        max_attempts=3,
    )


def _rows(item: WorkerSyncItem):
    document = SimpleNamespace(
        id=item.document_id,
        meta_info={
            "db_config": {
                "connection_id": str(uuid.uuid4()),
                "selections": [{"table_name": "safe_table", "columns": ["id"]}],
                "limit": 5000,
            }
        },
        knowledge_base_id=item.knowledge_base_id,
        source_type=SourceType.DB,
        chunk_size=1000,
        chunk_overlap=200,
        status="completed",
        error_message=None,
        updated_at=SNAPSHOT_TIME,
    )
    return {
        KnowledgeCollection: SimpleNamespace(id=item.collection_id),
        KnowledgeCollectionItem: SimpleNamespace(
            id=MEMBERSHIP_ID, rank=0, created_at=SNAPSHOT_TIME
        ),
        KnowledgeBase: SimpleNamespace(
            id=item.knowledge_base_id,
            embedding_model="text-embedding-3-small",
        ),
        Document: document,
    }


def test_document_adapter_finalizes_new_version_without_legacy_replace(
    monkeypatch,
) -> None:
    item = _item()
    rows = _rows(item)
    processor = Mock()
    processor.process.return_value = SimpleNamespace(
        chunks=[{"content": "row content", "metadata": {}}], metadata={}
    )
    processor_class = Mock(return_value=processor)
    vector = Mock()
    vector_class = Mock(return_value=vector)
    version = SimpleNamespace(id=uuid.uuid4())
    finalizer = Mock()
    finalizer.create_indexing_version.return_value = version
    finalizer_class = Mock(return_value=finalizer)
    monkeypatch.setattr(adapter_module, "DbProcessor", processor_class)
    monkeypatch.setattr(adapter_module, "VectorStoreService", vector_class)
    monkeypatch.setattr(
        adapter_module, "KnowledgeIngestionFinalizer", finalizer_class
    )
    db = Db(rows)
    acquire_lock = Mock(side_effect=lambda *_args: db.events.append("advisory"))
    monkeypatch.setattr(adapter_module, "acquire_document_write_lock", acquire_lock)
    actor_id = uuid.uuid4()
    SqlAlchemyKnowledgeCollectionSyncDocument(db).sync(item, actor_id=actor_id)

    source_config = processor.process.call_args.args[0]
    acquire_lock.assert_called_once_with(db, item.document_id)
    assert db.events[0] == "advisory"
    collection_predicate = and_(*db.queries[KnowledgeCollection][0].criteria)
    compiled = collection_predicate.compile(dialect=postgresql.dialect())
    assert "sync_state" in str(compiled)
    assert "source_deleted" in compiled.params.values()
    assert source_config["limit"] == 1000
    assert rows[Document].meta_info["db_config"]["limit"] == 5000
    vector.save_chunks.assert_called_once_with(
        document_id=item.document_id,
        chunks=[{"content": "row content", "metadata": {}}],
        model_name="text-embedding-3-small",
        commit=False,
        allow_empty_replace=False,
        document_version_id=version.id,
    )
    finalizer.finalize_active_version.assert_called_once_with(version)
    processor_class.assert_called_once_with(
        db_session=db,
        user_id=actor_id,
        connection_snapshot_provider=ANY,
    )
    assert isinstance(
        processor_class.call_args.kwargs["connection_snapshot_provider"],
        ConnectionRuntimeSnapshotProvider,
    )


@pytest.mark.parametrize(
    ("selection_mode", "selection_value", "expected_contents"),
    [
        ("range", "2-3", ["second row", "matching row"]),
        ("keyword", "matching", ["matching row"]),
    ],
)
def test_document_adapter_applies_persisted_chunk_selection(
    monkeypatch,
    selection_mode: str,
    selection_value: str,
    expected_contents: list[str],
) -> None:
    item = _item()
    rows = _rows(item)
    rows[Document].meta_info.update(
        {
            "selection_mode": selection_mode,
            "chunk_range": selection_value if selection_mode == "range" else None,
            "keyword_filter": (
                selection_value if selection_mode == "keyword" else None
            ),
        }
    )
    processor = Mock()
    processor.process.return_value = SimpleNamespace(
        chunks=[
            {"content": "first row", "metadata": {}},
            {"content": "second row", "metadata": {}},
            {"content": "matching row", "metadata": {}},
        ],
        metadata={},
    )
    vector = Mock()
    version = SimpleNamespace(id=uuid.uuid4())
    finalizer = Mock()
    finalizer.create_indexing_version.return_value = version
    monkeypatch.setattr(adapter_module, "DbProcessor", Mock(return_value=processor))
    monkeypatch.setattr(adapter_module, "VectorStoreService", Mock(return_value=vector))
    monkeypatch.setattr(
        adapter_module,
        "KnowledgeIngestionFinalizer",
        Mock(return_value=finalizer),
    )
    monkeypatch.setattr(adapter_module, "acquire_document_write_lock", Mock())
    SqlAlchemyKnowledgeCollectionSyncDocument(Db(rows)).sync(
        item,
        actor_id=uuid.uuid4(),
    )

    saved_chunks = vector.save_chunks.call_args.kwargs["chunks"]
    assert [chunk["content"] for chunk in saved_chunks] == expected_contents
    finalizer.finalize_active_version.assert_called_once_with(version)


@pytest.mark.parametrize(
    ("selection_mode", "chunk_range", "keyword_filter"),
    [
        ("keyword", None, "not-present"),
        ("range", "invalid", None),
    ],
)
def test_document_adapter_preserves_active_version_when_selection_is_invalid(
    monkeypatch,
    selection_mode: str,
    chunk_range: str | None,
    keyword_filter: str | None,
) -> None:
    item = _item()
    rows = _rows(item)
    rows[Document].meta_info.update(
        {
            "selection_mode": selection_mode,
            "chunk_range": chunk_range,
            "keyword_filter": keyword_filter,
        }
    )
    processor = Mock()
    processor.process.return_value = SimpleNamespace(
        chunks=[{"content": "row content", "metadata": {}}],
        metadata={},
    )
    vector_class = Mock()
    finalizer_class = Mock()
    monkeypatch.setattr(adapter_module, "DbProcessor", Mock(return_value=processor))
    monkeypatch.setattr(adapter_module, "VectorStoreService", vector_class)
    monkeypatch.setattr(
        adapter_module,
        "KnowledgeIngestionFinalizer",
        finalizer_class,
    )
    monkeypatch.setattr(adapter_module, "acquire_document_write_lock", Mock())
    with pytest.raises(SyncTargetConfigurationInvalid):
        SqlAlchemyKnowledgeCollectionSyncDocument(Db(rows)).sync(
            item,
            actor_id=uuid.uuid4(),
        )

    vector_class.assert_not_called()
    finalizer_class.assert_not_called()


def test_changed_target_revision_is_skipped_before_connection_lookup(
    monkeypatch,
) -> None:
    item = _item()
    rows = _rows(item)
    rows[Document].updated_at = SNAPSHOT_TIME + timedelta(seconds=1)
    processor_class = Mock()
    monkeypatch.setattr(adapter_module, "DbProcessor", processor_class)

    with pytest.raises(SyncTargetChanged):
        SqlAlchemyKnowledgeCollectionSyncDocument(Db(rows)).sync(
            item, actor_id=uuid.uuid4()
        )

    processor_class.assert_not_called()


def test_document_adapter_rejects_empty_result_before_version_swap(
    monkeypatch,
) -> None:
    item = _item()
    rows = _rows(item)
    processor = Mock()
    processor.process.return_value = SimpleNamespace(chunks=[], metadata={})
    monkeypatch.setattr(adapter_module, "DbProcessor", Mock(return_value=processor))
    vector_class = Mock()
    finalizer_class = Mock()
    monkeypatch.setattr(adapter_module, "VectorStoreService", vector_class)
    monkeypatch.setattr(
        adapter_module, "KnowledgeIngestionFinalizer", finalizer_class
    )
    with pytest.raises(SyncTargetConfigurationInvalid):
        SqlAlchemyKnowledgeCollectionSyncDocument(Db(rows)).sync(
            item, actor_id=uuid.uuid4()
        )

    vector_class.assert_not_called()
    finalizer_class.assert_not_called()


def test_connection_authorization_is_delegated_to_processor_with_actor(
    monkeypatch,
) -> None:
    item = _item()
    rows = _rows(item)
    processor = Mock()
    processor.process.return_value = SimpleNamespace(
        chunks=[],
        metadata={
            "error": "Resource unavailable",
            "error_code": "configuration_invalid",
            "reason_code": "resource.hidden",
        },
    )
    processor_class = Mock(return_value=processor)
    monkeypatch.setattr(adapter_module, "DbProcessor", processor_class)
    actor_id = uuid.uuid4()
    db = Db(rows)

    with pytest.raises(SyncTargetConfigurationInvalid):
        SqlAlchemyKnowledgeCollectionSyncDocument(db).sync(item, actor_id=actor_id)

    processor_class.assert_called_once_with(
        db_session=db,
        user_id=actor_id,
        connection_snapshot_provider=ANY,
    )
    assert isinstance(
        processor_class.call_args.kwargs["connection_snapshot_provider"],
        ConnectionRuntimeSnapshotProvider,
    )
    assert "query:Connection" not in db.events


def test_unlinked_target_is_skipped_before_connection_lookup() -> None:
    item = _item()
    rows = _rows(item)
    rows[KnowledgeCollectionItem] = None

    with pytest.raises(SyncTargetChanged):
        SqlAlchemyKnowledgeCollectionSyncDocument(Db(rows)).sync(
            item, actor_id=uuid.uuid4()
        )
