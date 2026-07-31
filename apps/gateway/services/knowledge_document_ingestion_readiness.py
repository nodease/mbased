from __future__ import annotations

from sqlalchemy.orm import Session

from apps.shared.services.knowledge_document_ingestion_schema_readiness import (
    inspect_knowledge_document_ingestion_schema,
)


class KnowledgeDocumentIngestionUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def require_knowledge_document_ingestion_schema(db: Session) -> None:
    result = inspect_knowledge_document_ingestion_schema(db.get_bind())
    if not result.ready:
        raise KnowledgeDocumentIngestionUnavailable(
            result.reason_code or "knowledge.ingestion_schema_not_ready"
        )
