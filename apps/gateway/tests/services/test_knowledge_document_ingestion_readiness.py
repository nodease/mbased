from types import SimpleNamespace

import pytest

from apps.gateway.services import knowledge_document_ingestion_readiness as readiness


def test_ready_schema_allows_ingestion(monkeypatch):
    engine = object()
    db = SimpleNamespace(get_bind=lambda: engine)
    monkeypatch.setattr(
        readiness,
        "inspect_knowledge_document_ingestion_schema",
        lambda candidate: SimpleNamespace(ready=candidate is engine, reason_code=None),
    )

    readiness.require_knowledge_document_ingestion_schema(db)


def test_unready_schema_raises_transport_neutral_error(monkeypatch):
    db = SimpleNamespace(get_bind=lambda: object())
    monkeypatch.setattr(
        readiness,
        "inspect_knowledge_document_ingestion_schema",
        lambda _engine: SimpleNamespace(
            ready=False,
            reason_code="knowledge.ingestion_table_missing",
        ),
    )

    with pytest.raises(readiness.KnowledgeDocumentIngestionUnavailable) as exc_info:
        readiness.require_knowledge_document_ingestion_schema(db)

    assert exc_info.value.reason_code == "knowledge.ingestion_table_missing"
