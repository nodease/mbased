from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

from sqlalchemy import inspect
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


REQUIRED_MEMORY_SCHEMA: dict[str, frozenset[str]] = {
    "conversation_sessions": frozenset(
        {
            "id",
            "organization_id",
            "deployment_id",
            "memory_contract_version",
            "storage_generation",
            "lifecycle",
            "lifecycle_revision",
            "content_revision",
            "active_turn_id",
        }
    ),
    "conversation_access_grants": frozenset(
        {"id", "session_id", "verifier_hash", "state", "expires_at"}
    ),
    "conversation_turns": frozenset(
        {
            "id",
            "session_id",
            "sequence",
            "version",
            "request_idempotency_hash",
            "request_fingerprint",
            "status",
        }
    ),
    "conversation_memory_entries": frozenset(
        {
            "id",
            "session_id",
            "turn_id",
            "lifecycle",
            "display_ciphertext",
            "display_key_version",
            "display_format_version",
            "display_content_digest",
            "display_plaintext_byte_length",
            "model_ciphertext",
            "model_key_version",
            "model_format_version",
            "model_content_digest",
            "model_plaintext_byte_length",
        }
    ),
    "conversation_memory_summaries": frozenset(
        {
            "id",
            "session_id",
            "source_revision",
            "status",
            "display_ciphertext",
            "display_key_version",
            "display_format_version",
            "display_content_digest",
            "display_plaintext_byte_length",
            "model_ciphertext",
            "model_key_version",
            "model_format_version",
            "model_content_digest",
            "model_plaintext_byte_length",
        }
    ),
    "memory_data_dependencies": frozenset(
        {
            "id",
            "organization_id",
            "source_kind",
            "canonical_resource_id",
            "authorization_decision_revision",
        }
    ),
    "memory_entry_dependencies": frozenset(
        {"entry_id", "dependency_id", "session_id", "organization_id"}
    ),
    "memory_summary_dependencies": frozenset(
        {"summary_id", "dependency_id", "session_id", "organization_id"}
    ),
    "memory_turn_dispatch_jobs": frozenset(
        {"id", "turn_id", "status", "claim_generation", "storage_generation"}
    ),
    "memory_summary_generation_jobs": frozenset(
        {"id", "session_id", "status", "lease_generation", "source_revision"}
    ),
    "memory_context_plans": frozenset(
        {"id", "session_id", "ordered_references", "content_digest", "expires_at"}
    ),
    "memory_context_leases": frozenset(
        {"id", "plan_id", "state", "claim_generation", "expires_at"}
    ),
    "memory_context_provider_attempts": frozenset(
        {"id", "lease_id", "status", "version", "provider_started_at"}
    ),
    "conversation_purge_jobs": frozenset(
        {
            "id",
            "session_reference_digest",
            "app_id",
            "deployment_id",
            "deployment_version",
            "audience_kind",
            "status",
            "receipt_verifier_hash",
        }
    ),
    "conversation_idempotency_records": frozenset(
        {
            "id",
            "scope_digest",
            "idempotency_key_hash",
            "request_fingerprint",
            "authorization_app_id",
            "authorization_verifier_key_version",
            "authorization_verifier_hash",
            "status",
            "result_lifecycle",
            "result_lifecycle_revision",
            "result_memory_contract_version",
            "result_expires_at",
            "result_previous_lifecycle",
            "result_previous_lifecycle_revision",
        }
    ),
    "conversation_secret_replays": frozenset(
        {
            "id",
            "organization_id",
            "idempotency_record_id",
            "purpose",
            "ciphertext",
            "key_version",
            "associated_data_digest",
            "expires_at",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class MemorySchemaReadinessResult:
    missing_columns: dict[str, list[str]]
    missing_tables: list[str] = field(default_factory=list)
    reason: str | None = None

    @property
    def ready(self) -> bool:
        return (
            not self.missing_columns and not self.missing_tables and self.reason is None
        )


def check_memory_schema_readiness(
    db: Session,
    required_schema: Mapping[str, frozenset[str]] = REQUIRED_MEMORY_SCHEMA,
) -> MemorySchemaReadinessResult:
    try:
        schema_inspector = inspect(db.get_bind())
    except Exception as exc:
        logger.warning(
            "memory.schema.introspection_failed",
            extra={"error_type": type(exc).__name__},
        )
        return MemorySchemaReadinessResult(
            missing_columns={},
            reason="schema_introspection_failed",
        )
    return check_memory_schema_readiness_with_inspector(
        schema_inspector,
        required_schema,
    )


def check_memory_schema_readiness_with_inspector(
    schema_inspector,
    required_schema: Mapping[str, frozenset[str]] = REQUIRED_MEMORY_SCHEMA,
) -> MemorySchemaReadinessResult:
    try:
        missing_tables: list[str] = []
        missing_columns: dict[str, list[str]] = {}
        for table_name in sorted(required_schema):
            required_columns = required_schema[table_name]
            if not schema_inspector.has_table(table_name):
                missing_tables.append(table_name)
                missing_columns[table_name] = sorted(required_columns)
                continue
            existing = {
                str(column["name"])
                for column in schema_inspector.get_columns(table_name)
            }
            missing = sorted(set(required_columns) - existing)
            if missing:
                missing_columns[table_name] = missing
        return MemorySchemaReadinessResult(
            missing_columns=missing_columns,
            missing_tables=missing_tables,
        )
    except Exception as exc:
        logger.warning(
            "memory.schema.introspection_failed",
            extra={"error_type": type(exc).__name__},
        )
        return MemorySchemaReadinessResult(
            missing_columns={},
            reason="schema_introspection_failed",
        )
