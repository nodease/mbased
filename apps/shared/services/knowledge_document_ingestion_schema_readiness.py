from __future__ import annotations

from dataclasses import dataclass

from apps.shared.domain.knowledge_document_ingestion import validate_runtime_settings
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

TABLE_NAME = "knowledge_document_ingestion_jobs"
REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "organization_id",
        "knowledge_base_id",
        "document_id",
        "requested_by_user_id",
        "operation_kind",
        "generation",
        "input_revision",
        "idempotency_key",
        "status",
        "attempt_count",
        "max_attempts",
        "retryable",
        "safe_reason_code",
        "owner_token",
        "fencing_token",
        "lease_expires_at",
        "heartbeat_at",
        "dispatch_lease_expires_at",
        "next_retry_at",
        "requested_at",
        "started_at",
        "completed_at",
        "dead_lettered_at",
        "updated_at",
        "result_document_version_id",
        "safe_metadata",
    }
)
REQUIRED_INDEXES = frozenset(
    {
        "uq_knowledge_document_ingestion_jobs_active_document",
        "ix_knowledge_document_ingestion_jobs_due",
        "ix_knowledge_document_ingestion_jobs_stale_lease",
    }
)
REQUIRED_UNIQUE_CONSTRAINTS = frozenset(
    {"uq_knowledge_document_ingestion_jobs_org_idempotency"}
)


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentIngestionSchemaReadiness:
    ready: bool
    missing_columns: tuple[str, ...] = ()
    missing_indexes: tuple[str, ...] = ()
    missing_unique_constraints: tuple[str, ...] = ()
    reason_code: str | None = None


class KnowledgeDocumentIngestionSchemaNotReady(RuntimeError):
    def __init__(self, result: KnowledgeDocumentIngestionSchemaReadiness) -> None:
        super().__init__(result.reason_code or "knowledge.ingestion_schema_not_ready")
        self.result = result


def inspect_knowledge_document_ingestion_schema(
    engine: Engine,
) -> KnowledgeDocumentIngestionSchemaReadiness:
    try:
        inspector = inspect(engine)
        if not inspector.has_table(TABLE_NAME):
            return KnowledgeDocumentIngestionSchemaReadiness(
                ready=False,
                reason_code="knowledge.ingestion_table_missing",
            )
        columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
        indexes = {index["name"] for index in inspector.get_indexes(TABLE_NAME)}
        unique_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(TABLE_NAME)
            if constraint.get("name")
        }
    except Exception:
        return KnowledgeDocumentIngestionSchemaReadiness(
            ready=False,
            reason_code="knowledge.ingestion_schema_introspection_failed",
        )

    missing_columns = tuple(sorted(REQUIRED_COLUMNS - columns))
    missing_indexes = tuple(sorted(REQUIRED_INDEXES - indexes))
    missing_unique_constraints = tuple(
        sorted(REQUIRED_UNIQUE_CONSTRAINTS - unique_constraints)
    )
    if missing_columns or missing_indexes or missing_unique_constraints:
        return KnowledgeDocumentIngestionSchemaReadiness(
            ready=False,
            missing_columns=missing_columns,
            missing_indexes=missing_indexes,
            missing_unique_constraints=missing_unique_constraints,
            reason_code="knowledge.ingestion_schema_incomplete",
        )
    return KnowledgeDocumentIngestionSchemaReadiness(ready=True)


def require_knowledge_document_ingestion_ready(
    engine: Engine,
    *,
    lease_seconds: int,
    heartbeat_seconds: int,
) -> None:
    validate_runtime_settings(
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )
    result = inspect_knowledge_document_ingestion_schema(engine)
    if not result.ready:
        raise KnowledgeDocumentIngestionSchemaNotReady(result)
