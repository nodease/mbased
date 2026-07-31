from apps.shared.db.models.knowledge import KnowledgeDocumentIngestionJob
from sqlalchemy.dialects import postgresql


def test_ingestion_job_model_contains_active_document_unique_index() -> None:
    table = KnowledgeDocumentIngestionJob.__table__
    index = next(
        index
        for index in table.indexes
        if index.name == "uq_knowledge_document_ingestion_jobs_active_document"
    )

    assert index.unique is True
    where = str(
        index.dialect_options["postgresql"]["where"].compile(
            dialect=postgresql.dialect()
        )
    )
    assert "document_id IS NOT NULL" in where
    assert "pending" in where
    assert "running" in where
    assert "retry_scheduled" in where


def test_ingestion_job_model_has_lease_and_terminal_constraints() -> None:
    constraint_names = {
        constraint.name for constraint in KnowledgeDocumentIngestionJob.__table__.constraints
    }

    assert "ck_knowledge_document_ingestion_jobs_lease" in constraint_names
    assert (
        "ck_knowledge_document_ingestion_jobs_dispatch_lease" in constraint_names
    )
    assert "ck_knowledge_document_ingestion_jobs_dead_letter" in constraint_names
    assert "uq_knowledge_document_ingestion_jobs_org_idempotency" in constraint_names
