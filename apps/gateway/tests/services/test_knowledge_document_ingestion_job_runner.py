from types import SimpleNamespace
from uuid import uuid4

import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from apps.gateway.application.knowledge_document_ingestion.worker import (
    DocumentIngestionLeaseLost,
    DocumentIngestionPermanentFailure,
    DocumentIngestionRetryableFailure,
    WorkerDocumentIngestionJob,
)
from apps.gateway.services.ingestion import job_runner
from apps.gateway.services.ingestion.processors import file_processor
from apps.gateway.services.ingestion.processors.file_processor import FileProcessor
from apps.gateway.services.ingestion.job_runner import (
    KnowledgeDocumentIngestionJobRunner,
)
from apps.gateway.services.ingestion.service import (
    DurableIngestionLeaseLost,
    DurableIngestionSourceFailure,
)
from apps.shared.db.models.knowledge import Document, KnowledgeBase
from apps.shared.services.egress_guard import EgressGuardError


class FakeSession:
    def __init__(self, document, knowledge_base) -> None:
        self.document = document
        self.knowledge_base = knowledge_base
        self.closed = False

    def get(self, model, _identifier):
        if model is Document:
            return self.document
        if model is KnowledgeBase:
            return self.knowledge_base
        raise AssertionError(f"unexpected model: {model}")

    def close(self):
        self.closed = True


def _worker_job(organization_id, knowledge_base_id, document_id):
    return WorkerDocumentIngestionJob(
        job_id=uuid4(),
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        requested_by_user_id=uuid4(),
        operation="process",
        generation=1,
        status="running",
        attempt_count=1,
        max_attempts=3,
        retryable=True,
        owner_token="owner-token",
        fencing_token="fencing-token",
        lease_expires_at=None,
        next_retry_at=None,
    )


def test_soft_time_limit_is_retryable_timeout(monkeypatch) -> None:
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        chunk_size=800,
        chunk_overlap=80,
    )
    knowledge_base = SimpleNamespace(
        id=knowledge_base_id,
        organization_id=organization_id,
        lifecycle_state="active",
        sync_state="active",
        embedding_model="text-embedding-3-small",
    )
    sessions: list[FakeSession] = []

    def session_factory():
        session = FakeSession(document, knowledge_base)
        sessions.append(session)
        return session

    monkeypatch.setattr(
        job_runner.IngestionOrchestrator,
        "process_document_for_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SoftTimeLimitExceeded()),
    )
    job = WorkerDocumentIngestionJob(
        job_id=uuid4(),
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        requested_by_user_id=uuid4(),
        operation="process",
        generation=1,
        status="running",
        attempt_count=1,
        max_attempts=3,
        retryable=True,
        owner_token="owner-token",
        fencing_token="fencing-token",
        lease_expires_at=None,
        next_retry_at=None,
    )

    with pytest.raises(DocumentIngestionRetryableFailure) as exc_info:
        KnowledgeDocumentIngestionJobRunner(
            session_factory,
            heartbeat_seconds=60,
        ).run(job)

    assert exc_info.value.reason_code == "ingestion.timeout"
    assert sessions[-1].closed is True


def test_temporary_source_failure_is_retryable(monkeypatch) -> None:
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        chunk_size=800,
        chunk_overlap=80,
    )
    knowledge_base = SimpleNamespace(
        id=knowledge_base_id,
        organization_id=organization_id,
        lifecycle_state="active",
        sync_state="active",
        embedding_model="text-embedding-3-small",
    )

    def session_factory():
        return FakeSession(document, knowledge_base)

    monkeypatch.setattr(
        job_runner.IngestionOrchestrator,
        "process_document_for_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DurableIngestionSourceFailure("source.temporarily_unavailable")
        ),
    )
    job = WorkerDocumentIngestionJob(
        job_id=uuid4(),
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        requested_by_user_id=uuid4(),
        operation="process",
        generation=1,
        status="running",
        attempt_count=1,
        max_attempts=3,
        retryable=True,
        owner_token="owner-token",
        fencing_token="fencing-token",
        lease_expires_at=None,
        next_retry_at=None,
    )

    with pytest.raises(DocumentIngestionRetryableFailure) as raised:
        KnowledgeDocumentIngestionJobRunner(
            session_factory,
            heartbeat_seconds=60,
        ).run(job)

    assert raised.value.reason_code == "ingestion.source_temporarily_unavailable"


def test_external_parser_unavailable_is_permanent_and_preserves_reason(
    monkeypatch,
) -> None:
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        chunk_size=800,
        chunk_overlap=80,
    )
    knowledge_base = SimpleNamespace(
        id=knowledge_base_id,
        organization_id=organization_id,
        lifecycle_state="active",
        sync_state="active",
        embedding_model="text-embedding-3-small",
    )

    monkeypatch.setattr(
        job_runner.IngestionOrchestrator,
        "process_document_for_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DurableIngestionSourceFailure(
                "knowledge.raw_parser_egress_unavailable"
            )
        ),
    )

    with pytest.raises(DocumentIngestionPermanentFailure) as raised:
        KnowledgeDocumentIngestionJobRunner(
            lambda: FakeSession(document, knowledge_base),
            heartbeat_seconds=60,
        ).run(_worker_job(organization_id, knowledge_base_id, document_id))

    assert raised.value.reason_code == "knowledge.raw_parser_egress_unavailable"


@pytest.mark.parametrize("lease_session_unavailable", [False, True])
def test_runner_wires_current_job_lease_guard_into_progress_path(
    monkeypatch,
    lease_session_unavailable,
) -> None:
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        chunk_size=800,
        chunk_overlap=80,
    )
    knowledge_base = SimpleNamespace(
        id=knowledge_base_id,
        organization_id=organization_id,
        lifecycle_state="active",
        sync_state="active",
        embedding_model="text-embedding-3-small",
    )

    class ExpiredLeaseRepository:
        def __init__(self, _session) -> None:
            pass

        def is_owned_worker_job_current(self, *_args, **_kwargs) -> bool:
            return False

    def process_with_progress_guard(*_args, **kwargs):
        if not kwargs["lease_is_current"]():
            raise DurableIngestionLeaseLost()
        pytest.fail("expired lease must stop durable progress")

    monkeypatch.setattr(
        job_runner,
        "SqlAlchemyDocumentIngestionRepository",
        ExpiredLeaseRepository,
    )
    monkeypatch.setattr(
        job_runner.IngestionOrchestrator,
        "process_document_for_job",
        process_with_progress_guard,
    )

    session_count = 0

    def session_factory():
        nonlocal session_count
        session_count += 1
        if lease_session_unavailable and session_count == 2:
            raise RuntimeError("lease database unavailable")
        return FakeSession(document, knowledge_base)

    with pytest.raises(DocumentIngestionLeaseLost):
        KnowledgeDocumentIngestionJobRunner(
            session_factory,
            heartbeat_seconds=60,
        ).run(_worker_job(organization_id, knowledge_base_id, document_id))


@pytest.mark.parametrize(
    ("egress_reason", "expected_exception", "expected_reason"),
    [
        (
            "egress.timeout",
            DocumentIngestionRetryableFailure,
            "ingestion.source_temporarily_unavailable",
        ),
        (
            "egress.connection_failed",
            DocumentIngestionRetryableFailure,
            "ingestion.source_temporarily_unavailable",
        ),
        (
            "egress.dns_resolution_failed",
            DocumentIngestionRetryableFailure,
            "ingestion.source_temporarily_unavailable",
        ),
        (
            "egress.private_target",
            DocumentIngestionPermanentFailure,
            "ingestion.processing_failed",
        ),
    ],
)
def test_file_egress_failure_uses_safe_retry_allowlist(
    monkeypatch,
    egress_reason,
    expected_exception,
    expected_reason,
) -> None:
    organization_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        chunk_size=800,
        chunk_overlap=80,
    )
    knowledge_base = SimpleNamespace(
        id=knowledge_base_id,
        organization_id=organization_id,
        lifecycle_state="active",
        sync_state="active",
        embedding_model="text-embedding-3-small",
    )

    monkeypatch.setattr(
        file_processor,
        "download_url_to_temp_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EgressGuardError(egress_reason)
        ),
    )

    def process_remote_file(*_args, **_kwargs):
        processor = FileProcessor.__new__(FileProcessor)
        return processor._download_file("https://files.example.test/document.pdf")

    monkeypatch.setattr(
        job_runner.IngestionOrchestrator,
        "process_document_for_job",
        process_remote_file,
    )

    with pytest.raises(expected_exception) as raised:
        KnowledgeDocumentIngestionJobRunner(
            lambda: FakeSession(document, knowledge_base),
            heartbeat_seconds=60,
        ).run(_worker_job(organization_id, knowledge_base_id, document_id))

    assert raised.value.reason_code == expected_reason
