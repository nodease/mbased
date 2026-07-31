from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from apps.shared.db.models.knowledge import Document
from sqlalchemy import bindparam, func, text
from sqlalchemy.sql.elements import TextClause

_UTC_ISO_TIMESTAMP_PATTERN = (
    r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])"
    r"T([01]\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d{1,6})?\+00:00$"
)


@dataclass(frozen=True)
class NormalizedTagFilter:
    mode: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class NormalizedMetadataFilter:
    classification: tuple[str, ...] = ()
    tags: Optional[NormalizedTagFilter] = None
    source_type: tuple[str, ...] = ()
    effective_at: Optional[datetime] = None

    @property
    def has_filters(self) -> bool:
        return bool(
            self.classification
            or self.tags
            or self.source_type
            or self.effective_at is not None
        )

    def audit_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        if self.classification:
            summary["classification"] = list(self.classification)
        if self.tags:
            summary["tags"] = {"mode": self.tags.mode, "count": len(self.tags.values)}
        if self.source_type:
            summary["source_type"] = list(self.source_type)
        if self.effective_at:
            summary["effective_at"] = self.effective_at.isoformat()
        return summary


@dataclass(frozen=True)
class KeywordFilterClause:
    fragments: tuple[str, ...]
    params: dict[str, Any]
    expanding_params: tuple[str, ...] = ()


def _get_value(source: Any, key: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def _string_tuple(values: Iterable[Any] | None, *, upper: bool = False) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized = []
    for value in values:
        item = str(value).strip()
        if not item:
            continue
        normalized.append(item.upper() if upper else item.lower())
    return tuple(dict.fromkeys(normalized))


def _tag_json_expr(document_ref: str, chunk_ref: str | None) -> str:
    if chunk_ref is None:
        return f"""
        CASE
            WHEN jsonb_typeof({document_ref}->'tags') = 'array' THEN {document_ref}->'tags'
            ELSE '[]'::jsonb
        END
    """
    return f"""
        CASE
            WHEN jsonb_typeof({document_ref}->'tags') = 'array' THEN {document_ref}->'tags'
            WHEN {document_ref} ? 'tags' THEN '[]'::jsonb
            WHEN jsonb_typeof({chunk_ref}->'tags') = 'array' THEN {chunk_ref}->'tags'
            ELSE '[]'::jsonb
        END
    """


def _effective_fragment(field_expr: str, operator: str, param_name: str) -> str:
    return (
        f"(NULLIF({field_expr}, '') IS NULL OR "
        f"({field_expr} ~ :metadata_effective_pattern "
        f"AND {field_expr} {operator} :{param_name}))"
    )


def _normalized_effective_at(value: datetime) -> str:
    """저장 metadata와 비교할 UTC ISO 문자열로 정규화한다."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="seconds")


def normalize_metadata_filter(
    *,
    metadata_filter: Any = None,
    classification_filter: Iterable[Any] | None = None,
    tags: Any = None,
    source_type: Iterable[Any] | None = None,
    effective_at: datetime | None = None,
) -> NormalizedMetadataFilter:
    """SearchQuery shortcut fields와 metadata_filter를 하나의 내부 계약으로 정규화한다."""

    classification = _string_tuple(
        classification_filter or _get_value(metadata_filter, "classification")
    )
    source_type_values = _string_tuple(
        source_type or _get_value(metadata_filter, "source_type"), upper=True
    )
    effective_at_value = effective_at or _get_value(metadata_filter, "effective_at")

    tag_source = tags or _get_value(metadata_filter, "tags")
    normalized_tags = None
    if tag_source is not None:
        mode = str(_get_value(tag_source, "mode") or "contains_any")
        values = _string_tuple(_get_value(tag_source, "values"), upper=False)
        if values:
            normalized_tags = NormalizedTagFilter(mode=mode, values=values)

    return NormalizedMetadataFilter(
        classification=classification,
        tags=normalized_tags,
        source_type=source_type_values,
        effective_at=effective_at_value,
    )


def build_sqlalchemy_filter_conditions(
    metadata_filter: NormalizedMetadataFilter | None,
    *,
    chunk_metadata_fallback: bool = True,
) -> list[Any]:
    """Document/DocumentChunk join query에 붙일 metadata filter 조건을 만든다."""

    if metadata_filter is None or not metadata_filter.has_filters:
        return []

    conditions: list[Any] = []
    if metadata_filter.classification:
        classification = func.coalesce(
            Document.meta_info["classification"].astext,
            "internal",
        )
        conditions.append(classification.in_(metadata_filter.classification))

    if metadata_filter.source_type:
        conditions.append(Document.source_type.in_(metadata_filter.source_type))

    if metadata_filter.tags:
        tag_values = metadata_filter.tags.values
        tag_expr = _tag_json_expr(
            "documents.meta_info",
            "document_chunks.metadata" if chunk_metadata_fallback else None,
        )

        def tag_condition(param_name: str, param_value: str) -> Any:
            condition = text(f"""
                EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text({tag_expr}) AS tag(value)
                    WHERE lower(tag.value) = :{param_name}
                )
            """)
            return condition.bindparams(bindparam(param_name, value=param_value))

        if metadata_filter.tags.mode == "contains_all":
            for index, tag in enumerate(tag_values):
                conditions.append(tag_condition(f"metadata_tag_{index}", tag))
        else:
            param_name = "metadata_tags"
            condition = text(f"""
                EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text({tag_expr}) AS tag(value)
                    WHERE lower(tag.value) IN :{param_name}
                )
            """)
            conditions.append(
                condition.bindparams(
                    bindparam(param_name, value=list(tag_values), expanding=True)
                )
            )

    if metadata_filter.effective_at:
        effective_value = _normalized_effective_at(metadata_filter.effective_at)
        from_condition = text(
            _effective_fragment(
                "documents.meta_info->>'effective_from'",
                "<=",
                "metadata_effective_at",
            )
        ).bindparams(
            bindparam("metadata_effective_pattern", value=_UTC_ISO_TIMESTAMP_PATTERN),
            bindparam("metadata_effective_at", value=effective_value),
        )
        to_condition = text(
            _effective_fragment(
                "documents.meta_info->>'effective_to'",
                ">",
                "metadata_effective_at",
            )
        ).bindparams(
            bindparam("metadata_effective_pattern", value=_UTC_ISO_TIMESTAMP_PATTERN),
            bindparam("metadata_effective_at", value=effective_value),
        )
        conditions.extend([from_condition, to_condition])

    return conditions


def build_keyword_filter_clause(
    metadata_filter: NormalizedMetadataFilter | None,
    *,
    chunk_metadata_fallback: bool = True,
) -> KeywordFilterClause:
    """Keyword search raw SQL에 안전하게 붙일 고정 filter 조각과 bind 값을 만든다."""

    if metadata_filter is None or not metadata_filter.has_filters:
        return KeywordFilterClause(fragments=(), params={})

    fragments: list[str] = []
    params: dict[str, Any] = {}
    expanding_params: list[str] = []

    if metadata_filter.classification:
        fragments.append(
            "COALESCE(d.meta_info->>'classification', 'internal') "
            "IN :metadata_classification"
        )
        params["metadata_classification"] = list(metadata_filter.classification)
        expanding_params.append("metadata_classification")

    if metadata_filter.source_type:
        fragments.append("d.source_type IN :metadata_source_type")
        params["metadata_source_type"] = list(metadata_filter.source_type)
        expanding_params.append("metadata_source_type")

    if metadata_filter.tags:
        tag_expr = _tag_json_expr(
            "d.meta_info",
            "dc.metadata" if chunk_metadata_fallback else None,
        )
        if metadata_filter.tags.mode == "contains_all":
            for index, tag in enumerate(metadata_filter.tags.values):
                param_name = f"metadata_tag_{index}"
                fragments.append(
                    "EXISTS ("
                    f"SELECT 1 FROM jsonb_array_elements_text({tag_expr}) AS tag(value) "
                    f"WHERE lower(tag.value) = :{param_name}"
                    ")"
                )
                params[param_name] = tag
        else:
            fragments.append(
                "EXISTS ("
                f"SELECT 1 FROM jsonb_array_elements_text({tag_expr}) AS tag(value) "
                "WHERE lower(tag.value) IN :metadata_tags"
                ")"
            )
            params["metadata_tags"] = list(metadata_filter.tags.values)
            expanding_params.append("metadata_tags")

    if metadata_filter.effective_at:
        params["metadata_effective_pattern"] = _UTC_ISO_TIMESTAMP_PATTERN
        params["metadata_effective_at"] = _normalized_effective_at(
            metadata_filter.effective_at
        )
        fragments.extend(
            [
                _effective_fragment(
                    "d.meta_info->>'effective_from'",
                    "<=",
                    "metadata_effective_at",
                ),
                _effective_fragment(
                    "d.meta_info->>'effective_to'",
                    ">",
                    "metadata_effective_at",
                ),
            ]
        )

    return KeywordFilterClause(
        fragments=tuple(fragments),
        params=params,
        expanding_params=tuple(expanding_params),
    )


def bind_keyword_filter_params(
    stmt: TextClause,
    clause: KeywordFilterClause,
) -> TextClause:
    for param_name in clause.expanding_params:
        stmt = stmt.bindparams(bindparam(param_name, expanding=True))
    return stmt
