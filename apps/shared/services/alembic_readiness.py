import logging
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlembicReadinessResult:
    code_heads: list[str] = field(default_factory=list)
    database_revisions: list[str] = field(default_factory=list)
    missing_version_table: bool = False
    database_behind: bool = False
    split_heads: bool = False
    unknown_database_revisions: list[str] = field(default_factory=list)
    reason: str | None = None

    @property
    def ready(self) -> bool:
        return (
            not self.missing_version_table
            and not self.database_behind
            and not self.split_heads
            and not self.unknown_database_revisions
            and self.reason is None
        )


def _stable_unique(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_value in values:
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return sorted(result)


def _safe_identifier(value: str) -> bool:
    return bool(value) and all(part.isidentifier() for part in value.split("."))


def evaluate_alembic_readiness(
    *,
    code_heads: Iterable[str | None],
    database_revisions: Iterable[str | None],
    known_revisions: Iterable[str | None] = (),
    has_version_table: bool = True,
) -> AlembicReadinessResult:
    """Evaluate migration readiness without importing app/runtime layers."""
    normalized_code_heads = _stable_unique(code_heads)
    normalized_database_revisions = _stable_unique(database_revisions)
    normalized_known_revisions = set(_stable_unique(known_revisions))

    if not has_version_table:
        return AlembicReadinessResult(
            code_heads=normalized_code_heads,
            database_revisions=[],
            missing_version_table=True,
        )

    unknown_database_revisions = [
        revision
        for revision in normalized_database_revisions
        if normalized_known_revisions and revision not in normalized_known_revisions
    ]
    return AlembicReadinessResult(
        code_heads=normalized_code_heads,
        database_revisions=normalized_database_revisions,
        database_behind=bool(
            normalized_code_heads
            and set(normalized_database_revisions) != set(normalized_code_heads)
        ),
        split_heads=len(normalized_code_heads) > 1,
        unknown_database_revisions=unknown_database_revisions,
    )


def check_alembic_readiness_with_inspector(
    schema_inspector,
    *,
    code_heads: Iterable[str | None],
    known_revisions: Iterable[str | None] = (),
    version_table: str = "alembic_version",
) -> AlembicReadinessResult:
    if not _safe_identifier(version_table):
        return AlembicReadinessResult(reason="invalid_version_table")
    try:
        if not schema_inspector.has_table(version_table):
            return evaluate_alembic_readiness(
                code_heads=code_heads,
                database_revisions=[],
                known_revisions=known_revisions,
                has_version_table=False,
            )
        bind = schema_inspector.bind
        statement = text(f"SELECT version_num FROM {version_table}")
        if hasattr(bind, "execute"):
            rows = bind.execute(statement).fetchall()
        else:
            with bind.connect() as connection:
                rows = connection.execute(statement).fetchall()
        database_revisions = [row[0] for row in rows]
        return evaluate_alembic_readiness(
            code_heads=code_heads,
            database_revisions=database_revisions,
            known_revisions=known_revisions,
        )
    except Exception as exc:
        logger.warning(
            "alembic.readiness.introspection_failed",
            extra={"error_type": type(exc).__name__},
        )
        return AlembicReadinessResult(reason="alembic_introspection_failed")
