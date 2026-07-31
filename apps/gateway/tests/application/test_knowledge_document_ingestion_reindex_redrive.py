import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from apps.gateway.application.knowledge_document_ingestion.use_cases import (
    AdmissionDocumentSnapshot,
    DocumentIngestionConflict,
    DocumentIngestionJobSnapshot,
    DocumentIngestionPolicyBlocked,
    RedriveDocumentIngestion,
    RedriveDocumentIngestionCommand,
    RequestKnowledgeBaseReindex,
    RequestKnowledgeBaseReindexCommand,
)


NOW = datetime(2026, 7, 16, tzinfo=timezone.utc)


def _target(*, document_id=None) -> AdmissionDocumentSnapshot:
    return AdmissionDocumentSnapshot(
        document_id=document_id or uuid.uuid4(),
        knowledge_base_id=KB_ID,
        organization_id=ORG_ID,
        source_type="FILE",
        document_status="completed",
        lifecycle_state="active",
        sync_state="manual",
        chunk_size=500,
        chunk_overlap=50,
        meta_info={"chunking_mode": "flat"},
        content_hash="a" * 64,
        active_document_version_id=uuid.uuid4(),
        embedding_model="text-embedding-3-small",
    )


def _job(
    target: AdmissionDocumentSnapshot,
    *,
    operation="process",
    generation=1,
    status="dead_lettered",
    retryable=True,
    input_revision="b" * 64,
) -> DocumentIngestionJobSnapshot:
    return DocumentIngestionJobSnapshot(
        job_id=uuid.uuid4(),
        organization_id=target.organization_id,
        knowledge_base_id=target.knowledge_base_id,
        document_id=target.document_id,
        operation=operation,
        generation=generation,
        input_revision=input_revision,
        status=status,
        attempt_count=3,
        max_attempts=3,
        retryable=retryable,
        safe_reason_code="ingestion.timeout",
        requested_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        result_document_version_id=None,
    )


ORG_ID = uuid.uuid4()
KB_ID = uuid.uuid4()
ACTOR_ID = uuid.uuid4()


class FakeRepository:
    def __init__(self, targets) -> None:
        self.targets = tuple(targets)
        self.active_by_document = {}
        self.latest_by_document = {}
        self.model_update = None
        self.applied = []
        self.created = []

    def database_now(self):
        return NOW

    def lock_document_scope(self, organization_id, knowledge_base_id, document_id):
        return next(
            (
                target
                for target in self.targets
                if target.organization_id == organization_id
                and target.knowledge_base_id == knowledge_base_id
                and target.document_id == document_id
            ),
            None,
        )

    def lock_knowledge_base_documents(self, organization_id, knowledge_base_id):
        if organization_id != ORG_ID or knowledge_base_id != KB_ID:
            return None
        return self.targets

    def find_active_job(self, document_id):
        return self.active_by_document.get(document_id)

    def latest_job(self, document_id, *, lock=False):
        return self.latest_by_document.get(document_id)

    def next_generation(self, document_id):
        latest = self.latest_by_document.get(document_id)
        return (latest.generation if latest else 0) + 1

    def update_locked_knowledge_base_model(
        self, knowledge_base_id, embedding_model, *, now
    ):
        self.model_update = (knowledge_base_id, embedding_model, now)

    def apply_settings_and_mark_queued(self, target, settings, *, now):
        self.applied.append((target.document_id, settings, now))

    def create_job(
        self,
        *,
        command,
        generation,
        input_revision,
        idempotency_key,
        now,
        max_attempts,
    ):
        job = DocumentIngestionJobSnapshot(
            job_id=uuid.uuid4(),
            organization_id=command.organization_id,
            knowledge_base_id=command.knowledge_base_id,
            document_id=command.document_id,
            operation=command.operation,
            generation=generation,
            input_revision=input_revision,
            status="pending",
            attempt_count=0,
            max_attempts=max_attempts,
            retryable=True,
            safe_reason_code=None,
            requested_at=now,
            started_at=None,
            completed_at=None,
            result_document_version_id=None,
        )
        self.created.append((job, idempotency_key))
        return job


class FakePublisher:
    def __init__(self) -> None:
        self.published = []

    def publish(self, job_id):
        self.published.append(job_id)


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeProgress:
    def __init__(self) -> None:
        self.cleared = []

    def clear(self, document_id):
        self.cleared.append(document_id)


def test_reindex_admits_all_documents_before_publishing() -> None:
    targets = (_target(), _target())
    repository = FakeRepository(targets)
    publisher = FakePublisher()
    uow = FakeUnitOfWork()
    progress = FakeProgress()

    result = RequestKnowledgeBaseReindex(
        repository=repository,
        publisher=publisher,
        unit_of_work=uow,
        progress=progress,
    ).execute(
        RequestKnowledgeBaseReindexCommand(
            actor_id=ACTOR_ID,
            organization_id=ORG_ID,
            knowledge_base_id=KB_ID,
            embedding_model="text-embedding-3-large",
        )
    )

    assert repository.model_update == (
        KB_ID,
        "text-embedding-3-large",
        NOW,
    )
    assert len(repository.created) == 2
    assert len(repository.applied) == 2
    assert uow.commits == 1
    assert progress.cleared == [target.document_id for target in targets]
    assert publisher.published == [job.job_id for job in result.jobs]


def test_reindex_partial_active_set_conflicts_without_mutation() -> None:
    targets = (_target(), _target())
    repository = FakeRepository(targets)
    repository.active_by_document[targets[0].document_id] = replace(
        _job(targets[0]),
        status="running",
        operation="reindex",
    )
    uow = FakeUnitOfWork()

    with pytest.raises(DocumentIngestionConflict):
        RequestKnowledgeBaseReindex(
            repository=repository,
            publisher=FakePublisher(),
            unit_of_work=uow,
        ).execute(
            RequestKnowledgeBaseReindexCommand(
                actor_id=ACTOR_ID,
                organization_id=ORG_ID,
                knowledge_base_id=KB_ID,
                embedding_model="text-embedding-3-large",
            )
        )

    assert repository.model_update is None
    assert repository.applied == []
    assert repository.created == []


def test_reindex_empty_knowledge_base_updates_model_without_phantom_job() -> None:
    repository = FakeRepository(())
    publisher = FakePublisher()
    uow = FakeUnitOfWork()
    progress = FakeProgress()

    result = RequestKnowledgeBaseReindex(
        repository=repository,
        publisher=publisher,
        unit_of_work=uow,
        progress=progress,
    ).execute(
        RequestKnowledgeBaseReindexCommand(
            actor_id=ACTOR_ID,
            organization_id=ORG_ID,
            knowledge_base_id=KB_ID,
            embedding_model="text-embedding-3-large",
        )
    )

    assert result.jobs == ()
    assert repository.model_update is not None
    assert publisher.published == []
    assert uow.commits == 1
    assert progress.cleared == []


def test_redrive_creates_new_generation_for_exact_terminal_job() -> None:
    target = _target()
    previous = _job(target, operation="sync", generation=4)
    repository = FakeRepository((target,))
    repository.latest_by_document[target.document_id] = previous
    publisher = FakePublisher()
    uow = FakeUnitOfWork()
    progress = FakeProgress()

    result = RedriveDocumentIngestion(
        repository=repository,
        publisher=publisher,
        unit_of_work=uow,
        progress=progress,
    ).execute(
        RedriveDocumentIngestionCommand(
            actor_id=ACTOR_ID,
            organization_id=ORG_ID,
            knowledge_base_id=KB_ID,
            document_id=target.document_id,
            expected_job_id=previous.job_id,
        )
    )

    assert result.job.operation == "sync"
    assert result.job.generation == 5
    assert result.job.job_id != previous.job_id
    assert publisher.published == [result.job.job_id]
    assert uow.commits == 1
    assert progress.cleared == [target.document_id]


def test_redrive_rejects_job_changed_after_authorization() -> None:
    target = _target()
    previous = _job(target)
    repository = FakeRepository((target,))
    repository.latest_by_document[target.document_id] = previous

    with pytest.raises(DocumentIngestionPolicyBlocked) as raised:
        RedriveDocumentIngestion(
            repository=repository,
            publisher=FakePublisher(),
            unit_of_work=FakeUnitOfWork(),
        ).execute(
            RedriveDocumentIngestionCommand(
                actor_id=ACTOR_ID,
                organization_id=ORG_ID,
                knowledge_base_id=KB_ID,
                document_id=target.document_id,
                expected_job_id=uuid.uuid4(),
            )
        )

    assert raised.value.reason_code == "ingestion.retry_not_available"
    assert repository.applied == []
    assert repository.created == []
