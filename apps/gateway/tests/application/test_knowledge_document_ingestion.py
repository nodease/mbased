from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from apps.gateway.application.knowledge_document_ingestion.use_cases import (
    AdmissionDocumentSnapshot,
    DocumentIngestionConflict,
    DocumentIngestionJobSnapshot,
    DocumentIngestionPolicyBlocked,
    DocumentIngestionPersistenceFailed,
    DocumentIngestionRequestResult,
    DocumentIngestionSettings,
    RequestDocumentIngestion,
    RequestDocumentIngestionCommand,
)


NOW = datetime(2026, 7, 16, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self, target: AdmissionDocumentSnapshot) -> None:
        self.target = target
        self.active: DocumentIngestionJobSnapshot | None = None
        self.applied = False
        self.created = False

    def database_now(self) -> datetime:
        return NOW

    def lock_document_scope(self, organization_id, knowledge_base_id, document_id):
        if (
            organization_id,
            knowledge_base_id,
            document_id,
        ) != (
            self.target.organization_id,
            self.target.knowledge_base_id,
            self.target.document_id,
        ):
            return None
        return self.target

    def find_active_job(self, document_id):
        assert document_id == self.target.document_id
        return self.active

    def next_generation(self, document_id):
        assert document_id == self.target.document_id
        return 1

    def apply_settings_and_mark_queued(self, target, settings, *, now):
        assert target == self.target
        assert now == NOW
        self.applied = True

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
        self.created = True
        return _job(
            command,
            generation=generation,
            input_revision=input_revision,
            max_attempts=max_attempts,
        )


class FakePublisher:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.published: list[uuid.UUID] = []

    def publish(self, job_id: uuid.UUID) -> None:
        self.published.append(job_id)
        if self.fails:
            raise RuntimeError("broker unavailable")


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def flush(self) -> None:
        self.flush_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeProgress:
    def __init__(self) -> None:
        self.cleared = []

    def clear(self, document_id):
        self.cleared.append(document_id)


def _target() -> AdmissionDocumentSnapshot:
    return AdmissionDocumentSnapshot(
        document_id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        source_type="FILE",
        document_status="pending",
        lifecycle_state="active",
        sync_state="manual",
        chunk_size=500,
        chunk_overlap=50,
        meta_info={"chunking_mode": "flat", "strategy": "general"},
        content_hash=None,
        active_document_version_id=None,
        embedding_model="text-embedding-3-small",
    )


def _command(
    target: AdmissionDocumentSnapshot,
    *,
    settings: DocumentIngestionSettings | None = None,
) -> RequestDocumentIngestionCommand:
    return RequestDocumentIngestionCommand(
        actor_id=uuid.uuid4(),
        organization_id=target.organization_id,
        knowledge_base_id=target.knowledge_base_id,
        document_id=target.document_id,
        operation="process",
        settings=settings
        or DocumentIngestionSettings(
            chunk_size=500,
            chunk_overlap=50,
            embedding_model="text-embedding-3-small",
            meta_updates={"chunking_mode": "flat", "strategy": "general"},
        ),
    )


def _job(
    command: RequestDocumentIngestionCommand,
    *,
    generation: int,
    input_revision: str,
    max_attempts: int = 3,
) -> DocumentIngestionJobSnapshot:
    return DocumentIngestionJobSnapshot(
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
        requested_at=NOW,
        started_at=None,
        completed_at=None,
        result_document_version_id=None,
    )


def _use_case(repository, publisher=None, unit_of_work=None, progress=None):
    return RequestDocumentIngestion(
        repository=repository,
        publisher=publisher or FakePublisher(),
        unit_of_work=unit_of_work or FakeUnitOfWork(),
        progress=progress,
    )


def test_new_request_commits_job_and_document_before_publish() -> None:
    target = _target()
    repository = FakeRepository(target)
    publisher = FakePublisher()
    unit_of_work = FakeUnitOfWork()
    progress = FakeProgress()

    result = _use_case(repository, publisher, unit_of_work, progress).execute(
        _command(target)
    )

    assert isinstance(result, DocumentIngestionRequestResult)
    assert result.reused is False
    assert result.dispatch_deferred is False
    assert repository.applied is True
    assert repository.created is True
    assert unit_of_work.flush_count == 1
    assert unit_of_work.commit_count == 1
    assert progress.cleared == [target.document_id]
    assert publisher.published == [result.job.job_id]


def test_publish_failure_keeps_committed_admission_for_recovery() -> None:
    target = _target()
    repository = FakeRepository(target)
    unit_of_work = FakeUnitOfWork()

    result = _use_case(
        repository,
        FakePublisher(fails=True),
        unit_of_work,
    ).execute(_command(target))

    assert result.dispatch_deferred is True
    assert unit_of_work.commit_count == 1
    assert unit_of_work.rollback_count == 0


def test_same_active_intent_is_reused_without_mutating_document() -> None:
    target = _target()
    repository = FakeRepository(target)
    command = _command(target)
    first = _use_case(repository).execute(command)
    repository.applied = False
    repository.created = False
    repository.active = first.job
    unit_of_work = FakeUnitOfWork()

    result = _use_case(repository, unit_of_work=unit_of_work).execute(command)

    assert result.reused is True
    assert result.job.job_id == first.job.job_id
    assert repository.applied is False
    assert repository.created is False
    assert unit_of_work.rollback_count == 1


def test_resume_retry_reuses_active_job_after_document_status_changes() -> None:
    target = replace(_target(), document_status="waiting_for_approval")
    repository = FakeRepository(target)
    command = replace(
        _command(target),
        operation="resume",
        required_document_status="waiting_for_approval",
    )
    first = _use_case(repository).execute(command)
    repository.target = replace(target, document_status="indexing")
    repository.active = first.job
    repository.applied = False
    repository.created = False
    unit_of_work = FakeUnitOfWork()

    retried = _use_case(repository, unit_of_work=unit_of_work).execute(command)

    assert retried.reused is True
    assert retried.job.job_id == first.job.job_id
    assert repository.applied is False
    assert repository.created is False
    assert unit_of_work.rollback_count == 1


def test_active_intent_reuse_precedes_reference_revision_precondition() -> None:
    target = replace(_target(), document_updated_at=NOW)
    repository = FakeRepository(target)
    command = replace(
        _command(target),
        expected_document_updated_at=NOW,
        require_document_revision_match=True,
    )
    first = _use_case(repository).execute(command)
    repository.target = replace(
        target,
        document_status="indexing",
        document_updated_at=datetime(2026, 7, 16, 0, 1, tzinfo=timezone.utc),
    )
    repository.active = first.job
    repository.applied = False
    repository.created = False

    retried = _use_case(repository).execute(command)

    assert retried.reused is True
    assert repository.applied is False
    assert repository.created is False


def test_new_reference_intent_rejects_stale_document_revision() -> None:
    target = replace(
        _target(),
        document_updated_at=datetime(2026, 7, 16, 0, 1, tzinfo=timezone.utc),
    )
    repository = FakeRepository(target)
    command = replace(
        _command(target),
        expected_document_updated_at=NOW,
        require_document_revision_match=True,
    )

    with pytest.raises(DocumentIngestionPolicyBlocked) as raised:
        _use_case(repository).execute(command)

    assert raised.value.reason_code == "connection.reference_conflict"
    assert repository.applied is False
    assert repository.created is False


def test_reference_uow_safe_commit_reason_is_preserved() -> None:
    class ReferenceBusy(Exception):
        code = "connection.reference_busy"

    class BusyUnitOfWork(FakeUnitOfWork):
        def commit(self) -> None:
            raise ReferenceBusy()

    target = _target()

    with pytest.raises(DocumentIngestionPersistenceFailed) as raised:
        _use_case(
            FakeRepository(target),
            unit_of_work=BusyUnitOfWork(),
        ).execute(_command(target))

    assert raised.value.reason_code == "connection.reference_busy"


def test_changed_settings_conflict_before_document_mutation() -> None:
    target = _target()
    repository = FakeRepository(target)
    first = _use_case(repository).execute(_command(target))
    repository.active = first.job
    repository.applied = False
    repository.created = False
    changed = _command(
        target,
        settings=DocumentIngestionSettings(
            chunk_size=800,
            chunk_overlap=50,
            embedding_model="text-embedding-3-small",
            meta_updates={"chunking_mode": "flat", "strategy": "general"},
        ),
    )

    with pytest.raises(DocumentIngestionConflict):
        _use_case(repository).execute(changed)

    assert repository.applied is False
    assert repository.created is False


@pytest.mark.parametrize(
    "target",
    [
        replace(_target(), lifecycle_state="archived"),
        replace(_target(), sync_state="source_deleted"),
    ],
)
def test_blocked_lifecycle_does_not_create_job(target) -> None:
    repository = FakeRepository(target)

    with pytest.raises(DocumentIngestionPolicyBlocked):
        _use_case(repository).execute(_command(target))

    assert repository.applied is False
    assert repository.created is False
