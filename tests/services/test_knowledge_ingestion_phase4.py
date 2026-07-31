import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.gateway.services.ingestion import service as ingestion_service_module
from apps.gateway.services.ingestion.service import (
    ACTIVE_PROCESSING_STALL_TIMEOUT_MESSAGE,
    IngestionOrchestrator,
    PROCESSING_START_TIMEOUT_MESSAGE,
    finalize_stale_processing_start,
    mark_document_processing_queued,
    recover_timed_out_document_with_artifacts,
)
from apps.shared.db.models.knowledge import (
    Document,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeDocumentIngestionJob,
)
from apps.shared.services.knowledge_ingestion_finalizer import (
    KnowledgeIngestionFinalizationError,
    KnowledgeIngestionFinalizer,
)
from apps.shared.services.knowledge_ingestion_fencing import (
    ACTIVE_FENCING_TOKEN_HASH_KEY,
    VERSION_FENCING_TOKEN_HASH_KEY,
    KnowledgeIngestionFencing,
)
from apps.shared.services.knowledge_ingestion_outbox import (
    OUTBOX_STATUS_DEAD_LETTERED,
    OUTBOX_STATUS_LEASED,
    OUTBOX_STATUS_RETRY_SCHEDULED,
    OUTBOX_STATUS_SUCCEEDED,
    KnowledgeIngestionOutboxService,
)
from apps.shared.services.knowledge_ingestion_outbox_processor import (
    KnowledgeIngestionOutboxProcessor,
    SupersededVersionCleanupHandler,
)
from apps.shared.services.knowledge_sync_cursor import (
    KnowledgeSyncCursorError,
    KnowledgeSyncCursorService,
)


ORG_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
KB_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")
DOC_ID = uuid.UUID("30000000-0000-0000-0000-000000000001")
NEW_VERSION_ID = uuid.UUID("40000000-0000-0000-0000-000000000001")
OLD_VERSION_ID = uuid.UUID("50000000-0000-0000-0000-000000000001")


class FakeNoIngestionJobQuery:
    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def first(self):
        return None


class FakeDb:
    def __init__(
        self,
        *,
        legacy_document=None,
        previous_version=None,
        kb=None,
        max_version_number=0,
    ):
        self.legacy_document = legacy_document
        self.previous_version = previous_version
        self.kb = kb
        self.max_version_number = max_version_number
        self.added = []
        self.flush_count = 0

    def get(self, model, value):
        if model is Document and value == DOC_ID:
            return self.legacy_document
        if model is DocumentVersion and value == OLD_VERSION_ID:
            return self.previous_version
        if model is KnowledgeBase and self.kb and value == self.kb.id:
            return self.kb
        return None

    def query(self, *args):
        return FakeScalarQuery(self.max_version_number)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flush_count += 1


class FakeScalarQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args):
        return self

    def scalar(self):
        return self.value


class FakeFinalizer(KnowledgeIngestionFinalizer):
    def __init__(
        self,
        db,
        *,
        kb,
        version,
        previous_version=None,
        chunk_count=3,
        deleted_legacy_count=0,
    ):
        super().__init__(db)
        self.kb = kb
        self.version = version
        self.previous_version = previous_version
        self.chunk_count = chunk_count
        self.deleted_legacy_count = deleted_legacy_count
        self.enqueued = []

    def _lock_document_version(self, document_version_id):
        assert document_version_id == self.version.id
        return self.version

    def _lock_knowledge_base(self, knowledge_base_id):
        assert knowledge_base_id == self.kb.id
        return self.kb

    def _previous_active_version(self, previous_version_id):
        assert previous_version_id == OLD_VERSION_ID
        return self.previous_version

    def _version_chunk_count(self, document_version_id):
        assert document_version_id == self.version.id
        return self.chunk_count

    def _delete_legacy_unversioned_chunks(self, version):
        assert version.id == self.version.id
        return self.deleted_legacy_count

    def _lock_legacy_document(self, legacy_document_id):
        return self.db.get(Document, legacy_document_id)

    def _enqueue_cleanup_event(
        self, *, version, previous_version_id, deleted_legacy_chunk_count
    ):
        event = SimpleNamespace(id=uuid.uuid4())
        self.enqueued.append(
            {
                "version_id": version.id,
                "previous_version_id": previous_version_id,
                "deleted_legacy_chunk_count": deleted_legacy_chunk_count,
            }
        )
        return event


def _version(status="indexing"):
    return SimpleNamespace(
        id=NEW_VERSION_ID,
        organization_id=ORG_ID,
        knowledge_base_id=KB_ID,
        legacy_document_id=DOC_ID,
        source_identity_id=None,
        status=status,
        content_hash="new-content-hash",
        embedding_model="text-embedding-3-small",
        safe_metadata={},
        ready_at=None,
        error_code="old-error",
        updated_at=None,
    )


def test_finalizer_swaps_active_version_and_supersedes_previous_in_one_boundary():
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    kb = SimpleNamespace(id=KB_ID, active_document_version_id=OLD_VERSION_ID)
    previous = SimpleNamespace(
        id=OLD_VERSION_ID,
        status="ready",
        superseded_at=None,
        updated_at=None,
    )
    legacy_document = SimpleNamespace(
        id=DOC_ID,
        content_hash="old-content-hash",
        embedding_model="old-model",
        updated_at=None,
    )
    db = FakeDb(legacy_document=legacy_document, previous_version=previous, kb=kb)
    finalizer = FakeFinalizer(
        db,
        kb=kb,
        version=_version(),
        previous_version=previous,
        chunk_count=7,
        deleted_legacy_count=2,
    )

    result = finalizer.finalize_active_version(finalizer.version, now=now)

    assert kb.active_document_version_id == NEW_VERSION_ID
    assert finalizer.version.status == "ready"
    assert finalizer.version.ready_at == now
    assert finalizer.version.error_code is None
    assert previous.status == "superseded"
    assert previous.superseded_at == now
    assert legacy_document.content_hash == "new-content-hash"
    assert legacy_document.embedding_model == "text-embedding-3-small"
    assert result.previous_document_version_id == OLD_VERSION_ID
    assert result.chunk_count == 7
    assert result.deleted_legacy_chunk_count == 2
    assert finalizer.enqueued == [
        {
            "version_id": NEW_VERSION_ID,
            "previous_version_id": OLD_VERSION_ID,
            "deleted_legacy_chunk_count": 2,
        }
    ]
    assert db.flush_count == 1


def test_finalizer_refuses_empty_version_without_changing_active_pointer():
    kb = SimpleNamespace(id=KB_ID, active_document_version_id=OLD_VERSION_ID)
    version = _version()
    finalizer = FakeFinalizer(
        FakeDb(kb=kb),
        kb=kb,
        version=version,
        chunk_count=0,
    )

    with pytest.raises(KnowledgeIngestionFinalizationError):
        finalizer.finalize_active_version(version)

    assert kb.active_document_version_id == OLD_VERSION_ID
    assert version.status == "indexing"
    assert finalizer.enqueued == []


def test_finalizer_rejects_stale_worker_fencing_token():
    token_a = "worker-a-token"
    token_b = "worker-b-token"
    kb = SimpleNamespace(id=KB_ID, active_document_version_id=OLD_VERSION_ID)
    legacy_document = SimpleNamespace(
        id=DOC_ID,
        content_hash="old-content-hash",
        embedding_model="old-model",
        meta_info={},
        updated_at=None,
    )
    db = FakeDb(legacy_document=legacy_document, kb=kb)
    version = _version()
    finalizer = FakeFinalizer(db, kb=kb, version=version)
    version.safe_metadata = {
        VERSION_FENCING_TOKEN_HASH_KEY: KnowledgeIngestionFencing.hash_token(token_a)
    }
    legacy_document.meta_info = {
        ACTIVE_FENCING_TOKEN_HASH_KEY: KnowledgeIngestionFencing.hash_token(token_b)
    }

    with pytest.raises(KnowledgeIngestionFinalizationError):
        finalizer.finalize_active_version(version, expected_fencing_token=token_a)

    assert kb.active_document_version_id == OLD_VERSION_ID
    assert version.status == "indexing"
    assert finalizer.enqueued == []


def test_finalizer_clears_fencing_hash_after_successful_finalize():
    token = "worker-token"
    kb = SimpleNamespace(id=KB_ID, active_document_version_id=None)
    legacy_document = SimpleNamespace(
        id=DOC_ID,
        content_hash="old-content-hash",
        embedding_model="old-model",
        meta_info={},
        updated_at=None,
    )
    db = FakeDb(legacy_document=legacy_document, kb=kb)
    version = _version()
    finalizer = FakeFinalizer(db, kb=kb, version=version, chunk_count=1)
    token_hash = KnowledgeIngestionFencing.hash_token(token)
    version.safe_metadata = {VERSION_FENCING_TOKEN_HASH_KEY: token_hash}
    legacy_document.meta_info = {ACTIVE_FENCING_TOKEN_HASH_KEY: token_hash}

    finalizer.finalize_active_version(version, expected_fencing_token=token)

    assert kb.active_document_version_id == NEW_VERSION_ID
    assert ACTIVE_FENCING_TOKEN_HASH_KEY not in legacy_document.meta_info


def test_failed_indexing_version_is_recorded_after_rollback_context():
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    token = "worker-token"
    kb = SimpleNamespace(id=KB_ID, organization_id=ORG_ID)
    db = FakeDb(kb=kb, max_version_number=4)
    finalizer = KnowledgeIngestionFinalizer(db)

    version = finalizer.record_failed_indexing_version(
        organization_id=ORG_ID,
        knowledge_base_id=KB_ID,
        legacy_document_id=DOC_ID,
        source_identity_id=None,
        content_hash="hash-after-extract",
        chunking_fingerprint="fingerprint-v1",
        embedding_model="text-embedding-3-small",
        safe_reason_code="ingestion.processing_failed",
        safe_metadata={"source_type": "FILE"},
        fencing_token=token,
        now=now,
    )

    assert version.status == "failed"
    assert version.version_number == 5
    assert version.error_code == "ingestion.processing_failed"
    assert version.content_hash == "hash-after-extract"
    assert version.updated_at == now
    assert version.safe_metadata["source_type"] == "FILE"
    assert token not in str(version.safe_metadata)
    assert VERSION_FENCING_TOKEN_HASH_KEY in version.safe_metadata
    assert db.added == [version]
    assert db.flush_count == 1


def test_content_scan_timeout_records_failed_non_retrieval_visible_version():
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    kb = SimpleNamespace(id=KB_ID, organization_id=ORG_ID)
    db = FakeDb(kb=kb, max_version_number=4)
    finalizer = KnowledgeIngestionFinalizer(db)

    version = finalizer.record_failed_indexing_version(
        organization_id=ORG_ID,
        knowledge_base_id=KB_ID,
        legacy_document_id=DOC_ID,
        source_identity_id=None,
        content_hash="hash-after-scan",
        chunking_fingerprint=None,
        embedding_model="text-embedding-3-small",
        safe_reason_code="content_scan.timeout",
        safe_metadata={
            "source_type": "FILE",
            "content_scan_state": "unknown",
            "risk_tier": "high",
        },
        fencing_token="worker-token",
        now=now,
    )

    assert version.status == "failed"
    assert version.ready_at is None
    assert version.error_code == "content_scan.timeout"
    assert version.safe_metadata["content_scan_state"] == "unknown"
    assert "worker-token" not in str(version.safe_metadata)
    assert db.added == [version]


def test_outbox_retry_and_dead_letter_status_are_explicit():
    service = KnowledgeIngestionOutboxService(SimpleNamespace())
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    retry_event = SimpleNamespace(
        retryable=True,
        attempt_count=1,
        max_attempts=3,
        owner_token="worker",
        fencing_token="fence",
        lease_expires_at=now,
        safe_reason_code=None,
        status="leased",
        next_retry_at=None,
        dead_lettered_at=None,
        updated_at=None,
    )
    dead_letter_event = SimpleNamespace(
        retryable=True,
        attempt_count=3,
        max_attempts=3,
        owner_token="worker",
        fencing_token="fence",
        lease_expires_at=now,
        safe_reason_code=None,
        status="leased",
        next_retry_at=None,
        dead_lettered_at=None,
        updated_at=None,
    )

    service.mark_retry_or_dead_letter(
        retry_event,
        safe_reason_code="outbox.processing_failed",
        now=now,
    )
    service.mark_retry_or_dead_letter(
        dead_letter_event,
        safe_reason_code="outbox.processing_failed",
        now=now,
    )

    assert retry_event.status == OUTBOX_STATUS_RETRY_SCHEDULED
    assert retry_event.owner_token is None
    assert retry_event.fencing_token is None
    assert retry_event.lease_expires_at is None
    assert retry_event.next_retry_at is not None
    assert dead_letter_event.status == OUTBOX_STATUS_DEAD_LETTERED
    assert dead_letter_event.dead_lettered_at == now


class FakeOutboxQuery:
    def __init__(self, rows):
        self.rows = rows
        self.skip_locked = None
        self.limit_value = None

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def with_for_update(self, **kwargs):
        self.skip_locked = kwargs.get("skip_locked")
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        return self.rows[: self.limit_value]


class FakeOutboxDb:
    def __init__(self, rows):
        self.query_obj = FakeOutboxQuery(rows)
        self.flush_count = 0

    def query(self, *args):
        return self.query_obj

    def flush(self):
        self.flush_count += 1


def test_outbox_lease_uses_skip_locked_and_sets_owner_fields():
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    row = SimpleNamespace(
        status="pending",
        owner_token=None,
        fencing_token=None,
        lease_expires_at=None,
        attempt_count=0,
        updated_at=None,
    )
    db = FakeOutboxDb([row])
    service = KnowledgeIngestionOutboxService(db)

    leased = service.lease_due_events(
        owner_token="worker-1",
        limit=1,
        lease_seconds=30,
        now=now,
    )

    assert leased == [row]
    assert db.query_obj.skip_locked is True
    assert db.query_obj.limit_value == 1
    assert row.status == OUTBOX_STATUS_LEASED
    assert row.owner_token == "worker-1"
    assert row.fencing_token
    assert row.lease_expires_at == now + timedelta(seconds=30)
    assert row.attempt_count == 1
    assert db.flush_count == 1


def test_outbox_recovery_moves_expired_lease_to_retry_or_dead_letter():
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    row = SimpleNamespace(
        status=OUTBOX_STATUS_LEASED,
        owner_token="worker",
        fencing_token="fence",
        lease_expires_at=now - timedelta(seconds=1),
        retryable=True,
        attempt_count=5,
        max_attempts=5,
        next_retry_at=None,
        safe_reason_code=None,
        dead_lettered_at=None,
        updated_at=None,
    )
    db = FakeOutboxDb([row])
    service = KnowledgeIngestionOutboxService(db)

    recovered_count = service.recover_stale_leases(now=now)

    assert recovered_count == 1
    assert row.status == OUTBOX_STATUS_DEAD_LETTERED
    assert row.owner_token is None
    assert row.fencing_token is None
    assert row.lease_expires_at is None
    assert row.safe_reason_code == "outbox.lease_expired"
    assert row.dead_lettered_at == now
    assert db.flush_count == 1


@pytest.mark.parametrize("limit", [0, -1, 5001, "not-a-number", None])
def test_outbox_limit_validation_rejects_invalid_values(limit):
    with pytest.raises(ValueError):
        KnowledgeIngestionOutboxService.validate_limit(limit)


def test_cleanup_superseded_event_refuses_active_version_deletion():
    previous = SimpleNamespace(
        id=OLD_VERSION_ID,
        knowledge_base_id=KB_ID,
        status="superseded",
    )
    kb = SimpleNamespace(id=KB_ID, active_document_version_id=OLD_VERSION_ID)
    event = SimpleNamespace(
        target_ref={"previous_document_version_id": str(OLD_VERSION_ID)}
    )
    db = FakeDb(previous_version=previous, kb=kb)
    outbox = KnowledgeIngestionOutboxService(db)
    handler = SupersededVersionCleanupHandler(db, outbox)

    with pytest.raises(RuntimeError):
        handler.process(event)


def test_cleanup_superseded_event_succeeds_when_previous_version_is_missing():
    event = SimpleNamespace(
        target_ref={"previous_document_version_id": str(OLD_VERSION_ID)},
        status="leased",
        owner_token="worker",
        fencing_token="fence",
        lease_expires_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        next_retry_at=None,
        safe_reason_code="old",
        safe_metadata={},
        updated_at=None,
    )
    db = FakeDb()
    outbox = KnowledgeIngestionOutboxService(db)
    handler = SupersededVersionCleanupHandler(db, outbox)

    deleted_count = handler.process(event)

    assert deleted_count == 0
    assert event.status == OUTBOX_STATUS_SUCCEEDED
    assert event.safe_metadata["version_missing"] is True


def test_cleanup_superseded_event_is_noop_for_non_superseded_version():
    previous = SimpleNamespace(
        id=OLD_VERSION_ID,
        knowledge_base_id=KB_ID,
        status="ready",
    )
    kb = SimpleNamespace(id=KB_ID, active_document_version_id=NEW_VERSION_ID)
    event = SimpleNamespace(
        target_ref={"previous_document_version_id": str(OLD_VERSION_ID)},
        status="leased",
        owner_token="worker",
        fencing_token="fence",
        lease_expires_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        next_retry_at=None,
        safe_reason_code="old",
        safe_metadata={},
        updated_at=None,
    )
    db = FakeDb(previous_version=previous, kb=kb)
    outbox = KnowledgeIngestionOutboxService(db)
    handler = SupersededVersionCleanupHandler(db, outbox)

    deleted_count = handler.process(event)

    assert deleted_count == 0
    assert event.status == OUTBOX_STATUS_SUCCEEDED
    assert event.safe_metadata["version_status"] == "ready"


def test_outbox_processor_dispatches_unsupported_events_to_retry():
    event = SimpleNamespace(event_type="unknown.event")
    processor = KnowledgeIngestionOutboxProcessor(FakeDb())
    processor.outbox = SimpleNamespace(
        mark_retry_or_dead_letter=lambda target, *, safe_reason_code: setattr(
            target, "safe_reason_code", safe_reason_code
        )
    )

    processor._process_event(event)

    assert event.safe_reason_code == "outbox.unsupported_event_type"


def test_outbox_processor_process_due_events_leaves_commit_to_caller():
    class CommitCountingDb(FakeDb):
        def __init__(self):
            super().__init__()
            self.commit_count = 0

        def commit(self):
            self.commit_count += 1

    class FakeOutbox:
        def recover_stale_leases(self):
            return 0

        def lease_due_events(self, *, owner_token, limit):
            return [SimpleNamespace(event_type="unknown.event")]

        def mark_retry_or_dead_letter(self, event, *, safe_reason_code):
            event.safe_reason_code = safe_reason_code

    db = CommitCountingDb()
    processor = KnowledgeIngestionOutboxProcessor(db)
    processor.outbox = FakeOutbox()

    result = processor.process_due_events(owner_token="worker", limit=1)

    assert result.processed_count == 1
    assert db.commit_count == 0


def test_update_status_can_clear_active_fencing_hash_on_completed_paths():
    document_id = uuid.uuid4()
    document = SimpleNamespace(
        status="indexing",
        error_message=None,
        updated_at=None,
        meta_info={
            ACTIVE_FENCING_TOKEN_HASH_KEY: "old-hash",
            "keep": "value",
        },
    )

    class FakeDocumentQuery:
        def get(self, value):
            assert value == document_id
            return document

    class FakeStatusDb:
        commit_count = 0

        def query(self, model):
            assert model is Document
            return FakeDocumentQuery()

        def commit(self):
            self.commit_count += 1

    service = IngestionOrchestrator(FakeStatusDb())

    service._update_status(
        document_id,
        "completed",
        meta_updates={ACTIVE_FENCING_TOKEN_HASH_KEY: None},
    )

    assert document.status == "completed"
    assert ACTIVE_FENCING_TOKEN_HASH_KEY not in document.meta_info
    assert document.meta_info["keep"] == "value"
    assert service.db.commit_count == 1


def test_finalize_indexing_version_marks_document_completed_after_finalizer_refresh(
    monkeypatch,
):
    document = SimpleNamespace(
        id=DOC_ID,
        status="indexing",
        error_message="old-error",
        updated_at=None,
        meta_info={ACTIVE_FENCING_TOKEN_HASH_KEY: "old-hash"},
    )

    class CommitCountingDb(FakeDb):
        def __init__(self):
            super().__init__(legacy_document=document)
            self.commit_count = 0

        def commit(self):
            self.commit_count += 1

    class RefreshingFinalizer:
        def __init__(self, db):
            self.db = db

        def finalize_active_version(self, document_version, *, expected_fencing_token):
            assert document_version.id == NEW_VERSION_ID
            assert expected_fencing_token == "worker-token"
            # Simulates populate_existing() refreshing the same Document identity
            # before the final transaction commits.
            document.status = "indexing"
            document.error_message = "old-error"

    db = CommitCountingDb()
    service = IngestionOrchestrator(db)
    monkeypatch.setattr(
        ingestion_service_module,
        "KnowledgeIngestionFinalizer",
        RefreshingFinalizer,
    )

    service._finalize_indexing_version(  # noqa: SLF001
        document,
        SimpleNamespace(id=NEW_VERSION_ID),
        fencing_token="worker-token",
    )

    assert document.status == "completed"
    assert document.error_message is None
    assert document.updated_at is not None
    assert db.commit_count == 1


def test_gateway_ingestion_acquires_shared_lock_before_extract(monkeypatch):
    events: list[str] = []
    document = SimpleNamespace(status="pending")

    class DocumentQuery:
        def get(self, document_id):
            assert document_id == DOC_ID
            return document

    class Session:
        def query(self, model):
            assert model is Document
            return DocumentQuery()

        def close(self):
            events.append("close")

    @contextmanager
    def processing_lock():
        yield True

    session = Session()
    orchestrator = IngestionOrchestrator(SimpleNamespace())
    monkeypatch.setattr(ingestion_service_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        orchestrator,
        "_document_processing_lock",
        lambda _document_id: processing_lock(),
    )
    monkeypatch.setattr(
        orchestrator,
        "_mark_document_indexing",
        lambda *_args: events.append("mark"),
    )
    monkeypatch.setattr(
        ingestion_service_module,
        "acquire_document_write_lock",
        lambda *_args: events.append("advisory"),
    )
    monkeypatch.setattr(
        orchestrator,
        "_extract_raw_blocks",
        lambda _document: events.append("extract") or [],
    )
    monkeypatch.setattr(orchestrator, "_update_status", lambda *_args, **_kwargs: None)

    orchestrator.process_document(DOC_ID)

    assert events[:3] == ["mark", "advisory", "extract"]


def test_document_processing_lock_falls_back_when_redis_lock_unavailable(monkeypatch):
    class BrokenDistributedLock:
        def __init__(self, *_args, **_kwargs):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(
        ingestion_service_module,
        "DistributedLock",
        BrokenDistributedLock,
    )
    service = IngestionOrchestrator(FakeDb())

    with service._document_processing_lock(DOC_ID) as acquired:  # noqa: SLF001
        assert acquired is True


def test_lock_not_acquired_before_start_timeout_keeps_document_queued(monkeypatch):
    queued_at = datetime.now(timezone.utc)
    document = SimpleNamespace(
        id=DOC_ID,
        status="indexing",
        meta_info={"processing_enqueued_at": queued_at.isoformat()},
        error_message=None,
        updated_at=queued_at,
        created_at=queued_at,
    )

    class FakeLockFailureSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.closed = False

        def query(self, model):
            if model is KnowledgeDocumentIngestionJob:
                return FakeNoIngestionJobQuery()
            if model is not Document:
                return FakeNoArtifactQuery()
            return self

        def get(self, value):
            assert value == DOC_ID
            return document

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    class FakeNoArtifactQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return None

    session = FakeLockFailureSession()
    progress_updates = []
    service = IngestionOrchestrator(FakeDb())
    monkeypatch.setattr(ingestion_service_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ingestion_service_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        service,
        "_update_progress_redis",
        lambda document_id, progress, expire=False: progress_updates.append(
            (document_id, progress, expire)
        ),
    )

    service._handle_lock_not_acquired(DOC_ID)  # noqa: SLF001

    assert document.status == "indexing"
    assert document.error_message is None
    assert session.commits == 0
    assert session.rollbacks == 0
    assert session.closed is True
    assert progress_updates == []


def test_lock_not_acquired_after_start_timeout_finalizes_failed(monkeypatch):
    queued_at = datetime.now(timezone.utc) - timedelta(seconds=600)
    document = SimpleNamespace(
        id=DOC_ID,
        status="indexing",
        meta_info={"processing_enqueued_at": queued_at.isoformat()},
        error_message=None,
        updated_at=queued_at,
        created_at=queued_at,
    )

    class FakeLockFailureSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.closed = False

        def query(self, model):
            if model is KnowledgeDocumentIngestionJob:
                return FakeNoIngestionJobQuery()
            if model is not Document:
                return FakeNoArtifactQuery()
            return self

        def get(self, value):
            assert value == DOC_ID
            return document

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    class FakeNoArtifactQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return None

    session = FakeLockFailureSession()
    progress_updates = []
    service = IngestionOrchestrator(FakeDb())
    monkeypatch.setattr(ingestion_service_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ingestion_service_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        service,
        "_update_progress_redis",
        lambda document_id, progress, expire=False: progress_updates.append(
            (document_id, progress, expire)
        ),
    )

    service._handle_lock_not_acquired(DOC_ID)  # noqa: SLF001

    assert document.status == "failed"
    assert document.meta_info["progress"] == 0
    assert ACTIVE_FENCING_TOKEN_HASH_KEY not in document.meta_info
    assert "did not start" in document.error_message
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed is True
    assert progress_updates == [(DOC_ID, 0, True)]


def test_stale_processing_start_without_active_fencing_finalizes_failed():
    queued_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    checked_at = queued_at + timedelta(seconds=61)
    document = SimpleNamespace(
        id=DOC_ID,
        status="indexing",
        meta_info={"processing_enqueued_at": queued_at.isoformat()},
        error_message=None,
        updated_at=queued_at,
        created_at=queued_at,
    )

    class FakeDocumentQuery:
        def get(self, value):
            assert value == DOC_ID
            return document

    class FakeNoArtifactQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return None

    class FakeStaleDb:
        commit_count = 0

        def query(self, model):
            if model is KnowledgeDocumentIngestionJob:
                return FakeNoIngestionJobQuery()
            if model is not Document:
                return FakeNoArtifactQuery()
            return FakeDocumentQuery()

        def commit(self):
            self.commit_count += 1

    db = FakeStaleDb()

    assert (
        finalize_stale_processing_start(
            db,
            DOC_ID,
            timeout_seconds=60,
            now=checked_at,
        )
        is True
    )
    assert document.status == "failed"
    assert document.meta_info["progress"] == 0
    assert document.meta_info["processing_current_step"] == (
        "Processing did not start in time."
    )
    assert "did not start" in document.error_message
    assert document.updated_at == checked_at
    assert db.commit_count == 1


def test_stale_processing_start_keeps_document_with_chunks():
    queued_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    checked_at = queued_at + timedelta(seconds=600)
    document = SimpleNamespace(
        id=DOC_ID,
        status="indexing",
        meta_info={"processing_enqueued_at": queued_at.isoformat()},
        error_message=None,
        updated_at=queued_at,
        created_at=queued_at,
    )

    class FakeDocumentQuery:
        def get(self, value):
            assert value == DOC_ID
            return document

    class FakeArtifactQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return object()

    class FakeArtifactDb:
        commit_count = 0

        def query(self, model):
            if model is KnowledgeDocumentIngestionJob:
                return FakeNoIngestionJobQuery()
            if model is Document:
                return FakeDocumentQuery()
            return FakeArtifactQuery()

        def commit(self):
            self.commit_count += 1

    db = FakeArtifactDb()

    assert finalize_stale_processing_start(db, DOC_ID, now=checked_at) is False
    assert document.status == "indexing"
    assert document.error_message is None
    assert db.commit_count == 0


def test_timed_out_failed_document_with_chunks_recovers_completed():
    failed_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    checked_at = failed_at + timedelta(seconds=30)
    document = SimpleNamespace(
        id=DOC_ID,
        status="failed",
        meta_info={
            "progress": 0,
            "processing_current_step": "Processing did not start in time.",
        },
        error_message=PROCESSING_START_TIMEOUT_MESSAGE,
        updated_at=failed_at,
        created_at=failed_at,
    )

    class FakeDocumentQuery:
        def get(self, value):
            assert value == DOC_ID
            return document

    class FakeArtifactQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return object()

    class FakeRecoveryDb:
        commit_count = 0

        def query(self, model):
            if model is Document:
                return FakeDocumentQuery()
            return FakeArtifactQuery()

        def commit(self):
            self.commit_count += 1

    db = FakeRecoveryDb()

    assert (
        recover_timed_out_document_with_artifacts(
            db,
            DOC_ID,
            now=checked_at,
        )
        is True
    )
    assert document.status == "completed"
    assert document.error_message is None
    assert document.meta_info["progress"] == 100
    assert document.meta_info["processing_recovered_from_timeout"] is True
    assert document.updated_at == checked_at
    assert db.commit_count == 1


def test_stale_processing_start_keeps_active_fencing_document():
    queued_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    checked_at = queued_at + timedelta(seconds=600)
    document = SimpleNamespace(
        id=DOC_ID,
        status="indexing",
        meta_info={
            "processing_enqueued_at": queued_at.isoformat(),
            ACTIVE_FENCING_TOKEN_HASH_KEY: "active-hash",
        },
        error_message=None,
        updated_at=queued_at,
        created_at=queued_at,
    )

    class FakeDocumentQuery:
        def get(self, value):
            assert value == DOC_ID
            return document

    class FakeActiveDb:
        commit_count = 0

        def query(self, model):
            if model is KnowledgeDocumentIngestionJob:
                return FakeNoIngestionJobQuery()
            assert model is Document
            return FakeDocumentQuery()

        def commit(self):
            self.commit_count += 1

    db = FakeActiveDb()

    assert (
        finalize_stale_processing_start(db, DOC_ID, now=checked_at) is False
    )
    assert document.status == "indexing"
    assert document.error_message is None
    assert db.commit_count == 0


def test_stale_active_processing_without_artifacts_finalizes_failed():
    started_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    checked_at = started_at + timedelta(seconds=901)
    document = SimpleNamespace(
        id=DOC_ID,
        status="indexing",
        meta_info={
            "processing_enqueued_at": started_at.isoformat(),
            "processing_started_at": started_at.isoformat(),
            ACTIVE_FENCING_TOKEN_HASH_KEY: "active-hash",
        },
        error_message=None,
        updated_at=started_at,
        created_at=started_at,
    )

    class FakeDocumentQuery:
        def get(self, value):
            assert value == DOC_ID
            return document

    class FakeNoArtifactQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return None

    class FakeStaleActiveDb:
        commit_count = 0

        def query(self, model):
            if model is KnowledgeDocumentIngestionJob:
                return FakeNoIngestionJobQuery()
            if model is Document:
                return FakeDocumentQuery()
            return FakeNoArtifactQuery()

        def commit(self):
            self.commit_count += 1

    db = FakeStaleActiveDb()

    assert finalize_stale_processing_start(db, DOC_ID, now=checked_at) is True
    assert document.status == "failed"
    assert document.error_message == ACTIVE_PROCESSING_STALL_TIMEOUT_MESSAGE
    assert ACTIVE_FENCING_TOKEN_HASH_KEY not in document.meta_info
    assert document.meta_info["processing_current_step"] == (
        "Processing did not complete in time."
    )
    assert db.commit_count == 1


def test_stale_active_processing_with_recent_progress_heartbeat_keeps_document():
    started_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    checked_at = started_at + timedelta(seconds=901)
    document = SimpleNamespace(
        id=DOC_ID,
        status="indexing",
        meta_info={
            "processing_enqueued_at": started_at.isoformat(),
            "processing_started_at": started_at.isoformat(),
            "processing_progress_updated_at": (
                checked_at - timedelta(seconds=30)
            ).isoformat(),
            ACTIVE_FENCING_TOKEN_HASH_KEY: "active-hash",
        },
        error_message=None,
        updated_at=started_at,
        created_at=started_at,
    )

    class FakeDocumentQuery:
        def get(self, value):
            assert value == DOC_ID
            return document

    class FakeActiveDb:
        commit_count = 0

        def query(self, model):
            if model is KnowledgeDocumentIngestionJob:
                return FakeNoIngestionJobQuery()
            assert model is Document
            return FakeDocumentQuery()

        def commit(self):
            self.commit_count += 1

    db = FakeActiveDb()

    assert finalize_stale_processing_start(db, DOC_ID, now=checked_at) is False
    assert document.status == "indexing"
    assert document.error_message is None
    assert ACTIVE_FENCING_TOKEN_HASH_KEY in document.meta_info
    assert db.commit_count == 0


def test_stale_active_processing_with_chunks_keeps_document_indexing():
    started_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    checked_at = started_at + timedelta(seconds=901)
    document = SimpleNamespace(
        id=DOC_ID,
        status="indexing",
        meta_info={
            "processing_enqueued_at": started_at.isoformat(),
            "processing_started_at": started_at.isoformat(),
            ACTIVE_FENCING_TOKEN_HASH_KEY: "active-hash",
        },
        error_message=None,
        updated_at=started_at,
        created_at=started_at,
    )

    class FakeDocumentQuery:
        def get(self, value):
            assert value == DOC_ID
            return document

    class FakeArtifactQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return object()

    class FakeArtifactDb:
        commit_count = 0

        def query(self, model):
            if model is KnowledgeDocumentIngestionJob:
                return FakeNoIngestionJobQuery()
            if model is Document:
                return FakeDocumentQuery()
            return FakeArtifactQuery()

        def commit(self):
            self.commit_count += 1

    db = FakeArtifactDb()

    assert finalize_stale_processing_start(db, DOC_ID, now=checked_at) is False
    assert document.status == "indexing"
    assert document.error_message is None
    assert ACTIVE_FENCING_TOKEN_HASH_KEY in document.meta_info
    assert db.commit_count == 0


def test_mark_document_processing_queued_records_progress_and_timestamp():
    document = SimpleNamespace(
        status="failed",
        meta_info={
            ACTIVE_FENCING_TOKEN_HASH_KEY: "stale-hash",
            "processing_started_at": "2026-07-04T12:00:00+00:00",
            "processing_recovered_from_timeout": True,
        },
        error_message="old error",
    )

    mark_document_processing_queued(document)

    assert document.status == "indexing"
    assert document.error_message is None
    assert document.meta_info["progress"] == 0
    assert "processing_enqueued_at" in document.meta_info
    assert document.meta_info["processing_current_step"] == "Processing queued."
    assert ACTIVE_FENCING_TOKEN_HASH_KEY not in document.meta_info
    assert "processing_started_at" not in document.meta_info
    assert "processing_recovered_from_timeout" not in document.meta_info


def test_content_cursor_advances_only_after_active_ready_version():
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    kb = SimpleNamespace(id=KB_ID, active_document_version_id=NEW_VERSION_ID)
    version = SimpleNamespace(
        id=NEW_VERSION_ID,
        knowledge_base_id=KB_ID,
        status="ready",
    )
    source_identity = SimpleNamespace(safe_metadata={}, updated_at=None)
    db = FakeDb(kb=kb)
    service = KnowledgeSyncCursorService(db)

    service.advance_content_cursor_after_finalization(
        source_identity=source_identity,
        document_version=version,
        content_cursor_ref="cursor-ref-v2",
        now=now,
    )

    assert source_identity.safe_metadata["sync_refs"] == {
        "content_cursor_ref": "cursor-ref-v2",
        "content_document_version_id": str(NEW_VERSION_ID),
        "content_committed_at": now.isoformat(),
    }
    assert db.flush_count == 1


def test_content_cursor_refuses_unready_or_inactive_version():
    kb = SimpleNamespace(id=KB_ID, active_document_version_id=OLD_VERSION_ID)
    version = SimpleNamespace(
        id=NEW_VERSION_ID,
        knowledge_base_id=KB_ID,
        status="indexing",
    )
    service = KnowledgeSyncCursorService(FakeDb(kb=kb))

    with pytest.raises(KnowledgeSyncCursorError):
        service.advance_content_cursor_after_finalization(
            source_identity=SimpleNamespace(safe_metadata={}),
            document_version=version,
            content_cursor_ref="cursor-ref-v2",
        )


def test_acl_watermark_advances_independently_from_content_cursor():
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    source_identity = SimpleNamespace(
        safe_metadata={
            "sync_refs": {
                "content_cursor_ref": "cursor-ref-v2",
                "content_document_version_id": str(NEW_VERSION_ID),
            }
        },
        updated_at=None,
    )
    service = KnowledgeSyncCursorService(FakeDb())

    service.advance_acl_watermark_after_permission_commit(
        source_identity=source_identity,
        acl_watermark_ref="acl-ref-7",
        freshness_epoch=7,
        candidate_cache_epoch=3,
        now=now,
    )

    assert source_identity.safe_metadata["sync_refs"]["content_cursor_ref"] == (
        "cursor-ref-v2"
    )
    assert source_identity.safe_metadata["sync_refs"]["acl_watermark_ref"] == (
        "acl-ref-7"
    )
    assert source_identity.safe_metadata["sync_refs"]["acl_freshness_epoch"] == 7
    assert source_identity.safe_metadata["sync_refs"]["candidate_cache_epoch"] == 3


def test_ingestion_error_message_does_not_store_raw_exception_detail():
    orchestrator = IngestionOrchestrator(db=SimpleNamespace())

    assert orchestrator._safe_ingestion_error_message(
        RuntimeError("postgres://internal-host/secret-table")
    ) == "문서 처리에 실패했습니다."
    assert orchestrator._safe_ingestion_error_message(
        RuntimeError("AutoOpen macro http://internal-host/token parser traceback")
    ) == "문서 처리에 실패했습니다."
    assert orchestrator._safe_ingestion_error_message(
        KnowledgeIngestionFinalizationError("version_has_no_chunks")
    ) == "문서 색인 최종화에 실패했습니다."


def test_reindex_failure_stores_fixed_error_and_logs_only_exception_type(
    monkeypatch,
    caplog,
):
    document = SimpleNamespace(id=DOC_ID)

    class DocumentQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return [document]

    db = SimpleNamespace(query=lambda _model: DocumentQuery())
    orchestrator = IngestionOrchestrator(db=db)
    status_updates = []

    monkeypatch.setattr(
        orchestrator,
        "_update_status",
        lambda *args, **kwargs: status_updates.append((args, kwargs)),
    )

    def fail_processing(_document_id):
        raise RuntimeError("legacy-reindex-exception-marker")

    monkeypatch.setattr(orchestrator, "process_document", fail_processing)
    caplog.set_level(
        logging.ERROR,
        logger="apps.gateway.services.ingestion.service",
    )

    orchestrator.reindex_knowledge_base(KB_ID, "embedding-model-v2")

    assert status_updates == [
        ((DOC_ID, "pending"), {}),
        ((DOC_ID, "failed", "문서 처리에 실패했습니다."), {}),
    ]
    assert "RuntimeError" in caplog.text
    assert "legacy-reindex-exception-marker" not in caplog.text
