import sys
import types
import uuid
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=object))

# ------------------------------------------------------------------
# Mocks & Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_encryption():
    with patch(
        "apps.shared.services.ingestion.vector_store_service.encryption_manager"
    ) as mock:
        # Pass-through encryption/decryption
        mock.encrypt.side_effect = lambda x: x
        mock.decrypt.side_effect = lambda x: x
        yield mock


@pytest.fixture
def mock_embedding_service():
    with patch(
        "apps.shared.services.ingestion.vector_store_service.EmbeddingService"
    ) as MockCls:
        instance = MockCls.return_value
        # Mock embedding return: simple list of 1536 floats
        instance.embed_batch.side_effect = lambda texts, model: [
            [0.1] * 1536 for _ in texts
        ]
        yield instance


@pytest.fixture(autouse=True)
def mock_tiktoken():
    encoding = MagicMock()
    encoding.encode.side_effect = lambda text: list(str(text))
    encoding.decode.side_effect = lambda tokens: "".join(tokens)
    with patch(
        "apps.shared.services.ingestion.vector_store_service.tiktoken.encoding_for_model",
        return_value=encoding,
    ), patch(
        "apps.shared.services.ingestion.vector_store_service.tiktoken.get_encoding",
        return_value=encoding,
    ):
        yield encoding


@pytest.fixture
def service(mock_db, mock_encryption, mock_embedding_service):
    from apps.shared.services.ingestion.vector_store_service import VectorStoreService

    user_id = uuid.uuid4()
    return VectorStoreService(db=mock_db, user_id=user_id)


@pytest.fixture
def mock_document(mock_db):
    from apps.shared.db.models.knowledge import Document

    doc_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    mock_doc = MagicMock(spec=Document)
    mock_doc.id = doc_id
    mock_doc.knowledge_base_id = kb_id
    mock_doc.embedding_model = "text-embedding-3-small"
    mock_doc.meta_info = {}

    # DB query for Document returns this doc
    mock_db.query.return_value.filter.return_value.first.return_value = mock_doc
    return mock_doc


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_incremental_update_scenarios(
    service, mock_db, mock_document, mock_embedding_service
):
    """
    증분 업데이트(Incremental Update) 로직 검증
    Scenario 1: 최초 저장 (DB 비어있음 -> 전체 임베딩)
    Scenario 2: 재실행 (데이터 변경 없음 -> 100% 재사용, API 호출 0)
    Scenario 3: 부분 변경 (5개 중 1개만 변경 -> 1개만 임베딩)
    """
    doc_id = mock_document.id

    # ------------------------------------------------------------
    # Scenario 1: First Run (Empty DB)
    # ------------------------------------------------------------
    # Mock: Existing chunks = []
    mock_db.query.return_value.filter.return_value.all.return_value = []

    chunks_input = [
        {"content": f"content {i}", "metadata": {"id": i}} for i in range(5)
    ]

    service.save_chunks(doc_id, chunks_input)

    # Assertions
    # 1. Embed API called for all 5 chunks
    assert mock_embedding_service.embed_batch.call_count >= 1
    # 2. Existing chunks deleted
    mock_db.query.return_value.filter.return_value.delete.assert_called()
    # 3. New chunks bulk saved
    saved_chunks = mock_db.bulk_save_objects.call_args[0][0]
    assert len(saved_chunks) == 5

    print("\n✅ Scenario 1 (First Run) Passed")

    # ------------------------------------------------------------
    # Scenario 2: Re-run (Same Data) -> EXPECT 0 API CALLS
    # ------------------------------------------------------------
    # Mock: DB now has the chunks we just saved
    mock_db.query.return_value.filter.return_value.all.return_value = saved_chunks

    # Reset mock counters
    mock_embedding_service.embed_batch.reset_mock()
    mock_db.bulk_save_objects.reset_mock()

    # Run again with same input
    service.save_chunks(doc_id, chunks_input)

    # Assertions
    # 1. Embed API should NOT be called
    mock_embedding_service.embed_batch.assert_not_called()
    # 2. Bulk save still happens (Atomic Swap)
    mock_db.bulk_save_objects.assert_called()
    reused_saved_chunks = mock_db.bulk_save_objects.call_args[0][0]
    assert len(reused_saved_chunks) == 5
    # 3. Check embeddings are reused (same object reference or value)
    assert reused_saved_chunks[0].embedding == saved_chunks[0].embedding

    print("✅ Scenario 2 (Full Reuse) Passed")

    # ------------------------------------------------------------
    # Scenario 3: Partial Change (1 Chunk Updated)
    # ------------------------------------------------------------
    # Update input: Modify 5th chunk
    input_changed = [c.copy() for c in chunks_input]
    input_changed[4]["content"] = "content 4 UPDATED"

    # Mock: DB still has the original chunks (from Scenario 1)
    mock_db.query.return_value.filter.return_value.all.return_value = saved_chunks

    # Reset counters
    mock_embedding_service.embed_batch.reset_mock()

    # Run with changed input
    service.save_chunks(doc_id, input_changed)

    # Assertions
    # 1. Embed API called exactly ONCE (or enough for batch)
    assert mock_embedding_service.embed_batch.call_count == 1

    # Verify ONLY the changed chunk was passed to embed function
    call_args = mock_embedding_service.embed_batch.call_args
    texts_to_embed = call_args[0][0]  # first arg is texts list

    assert len(texts_to_embed) == 1
    assert texts_to_embed[0] == "content 4 UPDATED"

    print("✅ Scenario 3 (Partial Update) Passed")


def test_vector_store_rejects_hierarchical_document_mode(service, mock_document):
    mock_document.meta_info = {"chunking_mode": "hierarchical"}

    with pytest.raises(RuntimeError, match="flat-only"):
        service.save_chunks(mock_document.id, [{"content": "x", "metadata": {}}])


def test_vector_store_rejects_non_flat_payload(service, mock_document):
    with pytest.raises(RuntimeError, match="hierarchical chunks"):
        service.save_chunks(
            mock_document.id,
            [{"content": "x", "metadata": {}, "chunk_level": "child"}],
        )


def test_empty_sync_result_replaces_chunks_inside_caller_transaction(
    service, mock_db, mock_document
):
    service.save_chunks(
        mock_document.id,
        [],
        commit=False,
        allow_empty_replace=True,
    )

    mock_db.query.return_value.filter.return_value.delete.assert_called_once()
    mock_db.flush.assert_called_once()
    mock_db.commit.assert_not_called()


def test_postgres_document_replacement_acquires_transaction_lock(
    service, mock_db, mock_document
):
    mock_db.get_bind.return_value.dialect.name = "postgresql"

    service.save_chunks(
        mock_document.id,
        [],
        commit=False,
        allow_empty_replace=True,
    )

    statement, parameters = mock_db.execute.call_args.args
    assert "pg_advisory_xact_lock" in str(statement)
    assert isinstance(parameters["lock_key"], int)


def test_versioned_save_replaces_only_target_version_chunks(
    service,
    mock_db,
    mock_document,
) -> None:
    from apps.shared.db.models.knowledge import (
        Document,
        DocumentChunk,
        DocumentVersion,
    )

    version_id = uuid.uuid4()
    document_query = MagicMock()
    document_query.filter.return_value = document_query
    document_query.first.return_value = mock_document
    version_query = MagicMock()
    version_query.filter.return_value = version_query
    version_query.one_or_none.return_value = MagicMock(id=version_id)
    chunk_query = MagicMock()
    chunk_query.filter.return_value = chunk_query
    chunk_query.all.return_value = []

    def query(model):
        if model is Document:
            return document_query
        if model is DocumentVersion:
            return version_query
        if model is DocumentChunk:
            return chunk_query
        raise AssertionError(f"unexpected model: {model}")

    mock_db.query.side_effect = query

    service.save_chunks(
        mock_document.id,
        [{"content": "versioned content", "metadata": {}}],
        document_version_id=version_id,
        commit=False,
    )

    saved_chunks = mock_db.bulk_save_objects.call_args.args[0]
    assert [chunk.document_version_id for chunk in saved_chunks] == [version_id]
    assert any(
        "document_version_id" in str(predicate)
        for call in chunk_query.filter.call_args_list
        for predicate in call.args
    )
    chunk_query.delete.assert_called_once_with(synchronize_session=False)
    mock_db.flush.assert_called_once()
    mock_db.commit.assert_not_called()


def test_unversioned_save_replaces_only_unversioned_chunks(
    service,
    mock_db,
    mock_document,
) -> None:
    from apps.shared.db.models.knowledge import Document, DocumentChunk

    document_query = MagicMock()
    document_query.filter.return_value = document_query
    document_query.first.return_value = mock_document
    chunk_query = MagicMock()
    chunk_query.filter.return_value = chunk_query
    chunk_query.all.return_value = []

    def query(model):
        if model is Document:
            return document_query
        if model is DocumentChunk:
            return chunk_query
        raise AssertionError(f"unexpected model: {model}")

    mock_db.query.side_effect = query

    service.save_chunks(
        mock_document.id,
        [{"content": "legacy content", "metadata": {}}],
        commit=False,
    )

    predicates = [
        str(predicate)
        for call in chunk_query.filter.call_args_list
        for predicate in call.args
    ]
    assert predicates.count("document_chunks.document_version_id IS NULL") == 2
    saved_chunks = mock_db.bulk_save_objects.call_args.args[0]
    assert [chunk.document_version_id for chunk in saved_chunks] == [None]
    chunk_query.delete.assert_called_once_with(synchronize_session=False)
    mock_db.flush.assert_called_once()
    mock_db.commit.assert_not_called()


def test_embedding_failure_uses_sanitized_exception_and_log(
    service, mock_document, mock_embedding_service, caplog
):
    mock_embedding_service.embed_batch.side_effect = RuntimeError(
        "credential-bearing provider detail"
    )

    with pytest.raises(RuntimeError, match="^embedding_generation_failed$"):
        service.save_chunks(
            mock_document.id,
            [{"content": "new content", "metadata": {}}],
            commit=False,
        )

    assert "credential-bearing provider detail" not in caplog.text
