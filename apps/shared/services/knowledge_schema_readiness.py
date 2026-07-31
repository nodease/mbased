import logging
from dataclasses import dataclass, field
from typing import Mapping

from sqlalchemy import inspect
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnowledgeSchemaReadinessResult:
    missing_columns: dict[str, list[str]]
    reason: str | None = None
    missing_tables: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return (
            not self.missing_columns
            and not self.missing_tables
            and self.reason is None
        )


def table_has_column(db: Session, table_name: str, column_name: str) -> bool:
    try:
        return any(
            column["name"] == column_name
            for column in inspect(db.get_bind()).get_columns(table_name)
        )
    except Exception as exc:
        logger.warning(
            "knowledge.schema.column_introspection_failed",
            extra={
                "table": table_name,
                "column": column_name,
                "error_type": type(exc).__name__,
            },
        )
        return False


def check_knowledge_schema_readiness(
    db: Session,
    required_columns: Mapping[str, set[str]],
) -> KnowledgeSchemaReadinessResult:
    try:
        inspector = inspect(db.get_bind())
    except Exception as exc:
        logger.warning(
            "knowledge.schema.introspection_failed",
            extra={"error_type": type(exc).__name__},
        )
        return KnowledgeSchemaReadinessResult(
            missing_columns={},
            reason="schema_introspection_failed",
        )

    return check_knowledge_schema_readiness_with_inspector(
        inspector,
        required_columns,
    )


def check_knowledge_schema_readiness_with_inspector(
    schema_inspector,
    required_columns: Mapping[str, set[str]],
) -> KnowledgeSchemaReadinessResult:
    try:
        missing_tables: list[str] = []
        missing: dict[str, list[str]] = {}
        for table_name in sorted(required_columns):
            column_names = required_columns[table_name]
            if not schema_inspector.has_table(table_name):
                missing_tables.append(table_name)
                missing[table_name] = sorted(column_names)
                continue
            existing = {
                column["name"] for column in schema_inspector.get_columns(table_name)
            }
            missing_columns = sorted(column_names - existing)
            if missing_columns:
                missing[table_name] = missing_columns
        return KnowledgeSchemaReadinessResult(
            missing_columns=missing,
            missing_tables=missing_tables,
        )
    except Exception as exc:
        logger.warning(
            "knowledge.schema.introspection_failed",
            extra={"error_type": type(exc).__name__},
        )
        return KnowledgeSchemaReadinessResult(
            missing_columns={},
            reason="schema_introspection_failed",
        )
