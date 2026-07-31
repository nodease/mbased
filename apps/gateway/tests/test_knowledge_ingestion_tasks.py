import uuid
from types import SimpleNamespace

from apps.gateway import knowledge_ingestion_tasks as tasks
from apps.gateway.application.knowledge_document_ingestion.worker import (
    WorkerDocumentIngestionResult,
)


class FakeDb:
    def __init__(self) -> None:
        self.closed = False

    def close(self):
        self.closed = True


def test_execute_task_rejects_malformed_job_id_without_opening_db(monkeypatch) -> None:
    monkeypatch.setattr(
        tasks,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be opened")),
    )

    assert tasks.execute_document_ingestion.run("raw/private/value") == {
        "status": "invalid_job_id"
    }


def test_execute_task_passes_only_canonical_job_id_and_closes_session(
    monkeypatch,
) -> None:
    job_id = uuid.uuid4()
    db = FakeDb()
    calls = []

    class UseCase:
        def execute(self, value, *, owner_token):
            calls.append((value, owner_token))
            return WorkerDocumentIngestionResult("succeeded")

    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks,
        "build_execute_document_ingestion_job",
        lambda value: UseCase() if value is db else None,
    )

    result = tasks.execute_document_ingestion.run(str(job_id))

    assert result == {"status": "succeeded", "reason_code": None}
    assert calls[0][0] == job_id
    assert uuid.UUID(calls[0][1])
    assert db.closed is True


def test_recovery_task_closes_session(monkeypatch) -> None:
    db = FakeDb()
    use_case = SimpleNamespace(execute=lambda: {"recovered": 1, "published": 1})
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks,
        "build_recover_document_ingestion_jobs",
        lambda value: use_case if value is db else None,
    )

    assert tasks.recover_document_ingestion_jobs.run() == {
        "recovered": 1,
        "published": 1,
    }
    assert db.closed is True


def test_task_delivery_contract_is_late_ack_without_celery_retry() -> None:
    execute = tasks.execute_document_ingestion
    recover = tasks.recover_document_ingestion_jobs

    assert execute.acks_late is True
    assert execute.reject_on_worker_lost is True
    assert execute.max_retries == 0
    assert execute.soft_time_limit == 840
    assert execute.time_limit == 900
    assert recover.acks_late is True
    assert recover.max_retries == 0
