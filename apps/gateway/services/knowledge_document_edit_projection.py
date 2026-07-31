from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from apps.shared.services.knowledge_safe_text import safe_label_from_text


_SOURCE_TYPES = frozenset({"FILE", "API", "DB"})
_STRATEGIES = frozenset({"general", "llamaparse"})
_CHUNKING_MODES = frozenset({"flat", "hierarchical"})
_SELECTION_MODES = frozenset({"all", "range", "keyword"})
_RANGE_PATTERN = re.compile(r"^[1-9][0-9]{0,8}-[1-9][0-9]{0,8}$")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_CONFIG_TEXT_LENGTH = 65_536
_MAX_IDENTIFIER_LENGTH = 255
_MAX_TABLES = 200
_MAX_COLUMNS_PER_TABLE = 500
_MAX_JOIN_EDGES = 20
_MAX_TEMPLATE_LENGTH = 20_000
_MAX_AGGREGATE_ITEMS = 5_000
_MAX_PROJECTED_CONFIG_BYTES = 256_000


class _EditConfigUnavailable(ValueError):
    pass


class _ProjectionBudget:
    def __init__(self) -> None:
        self.items = 0

    def consume(self, count: int) -> None:
        self.items += count
        if self.items > _MAX_AGGREGATE_ITEMS:
            raise _EditConfigUnavailable


def project_document_edit_config(document: Any) -> dict[str, object]:
    """Return the bounded settings needed by the write-authorized edit screen."""

    source_type, source_type_is_valid = _source_type(document)
    if not source_type_is_valid:
        return _unavailable(source_type)

    try:
        meta = _metadata(getattr(document, "meta_info", None))
        chunk_size = _bounded_integer(
            getattr(document, "chunk_size", None), minimum=1, maximum=100_000
        )
        chunk_overlap = _bounded_integer(
            getattr(document, "chunk_overlap", None), minimum=0, maximum=99_999
        )
        if chunk_overlap >= chunk_size:
            raise _EditConfigUnavailable

        projected: dict[str, object] = {
            "editable": True,
            "safe_reason_code": None,
            "source_type": source_type,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "segment_identifier": _bounded_text(
                meta.get("segment_identifier", "\n\n"),
                maximum=256,
                allow_empty=False,
            ),
            "remove_urls_emails": _boolean_with_default(
                meta, "remove_urls_emails", False
            ),
            "remove_whitespace": _boolean_with_default(
                meta, "remove_whitespace", True
            ),
            "strategy": _enum_with_default(
                meta, "strategy", "general", _STRATEGIES
            ),
            "chunking_mode": _enum_with_default(
                meta, "chunking_mode", "flat", _CHUNKING_MODES
            ),
        }
        projected.update(_selection_config(meta))

        if source_type == "DB":
            projected["db_config"] = _db_config(meta)
        elif source_type == "API":
            projected["api_config"] = _api_config(meta)

        _enforce_serialized_budget(projected)
        return projected
    except (TypeError, ValueError, json.JSONDecodeError):
        return _unavailable(source_type)


def _unavailable(source_type: str) -> dict[str, object]:
    return {
        "editable": False,
        "safe_reason_code": "document.edit_config_unavailable",
        "source_type": source_type,
    }


def _source_type(document: Any) -> tuple[str, bool]:
    value = getattr(document, "source_type", "FILE")
    if hasattr(value, "value"):
        value = value.value
    normalized = str(value or "FILE").upper()
    if normalized not in _SOURCE_TYPES:
        return "FILE", False
    return normalized, True


def _metadata(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _EditConfigUnavailable
    return value


def _bounded_integer(value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _EditConfigUnavailable
    if value < minimum or value > maximum:
        raise _EditConfigUnavailable
    return value


def _bounded_text(
    value: Any,
    *,
    maximum: int,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise _EditConfigUnavailable
    if not allow_empty and not value:
        raise _EditConfigUnavailable
    if _CONTROL_CHARACTER_PATTERN.search(value):
        raise _EditConfigUnavailable
    return value


def _boolean_with_default(
    mapping: Mapping[str, Any], key: str, default: bool
) -> bool:
    if key not in mapping:
        return default
    value = mapping[key]
    if not isinstance(value, bool):
        raise _EditConfigUnavailable
    return value


def _enum_with_default(
    mapping: Mapping[str, Any],
    key: str,
    default: str,
    allowed: frozenset[str],
) -> str:
    value = mapping.get(key, default)
    if not isinstance(value, str):
        raise _EditConfigUnavailable
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise _EditConfigUnavailable
    return normalized


def _selection_config(meta: Mapping[str, Any]) -> dict[str, object]:
    mode = _enum_with_default(meta, "selection_mode", "all", _SELECTION_MODES)
    chunk_range = meta.get("chunk_range")
    keyword_filter = meta.get("keyword_filter")

    if mode == "range":
        value = _bounded_text(chunk_range, maximum=64, allow_empty=False)
        if not _RANGE_PATTERN.fullmatch(value):
            raise _EditConfigUnavailable
        start, end = (int(part) for part in value.split("-", 1))
        if start > end:
            raise _EditConfigUnavailable
        chunk_range = value
        keyword_filter = None
    elif mode == "keyword":
        keyword_filter = _bounded_text(
            keyword_filter, maximum=1_000, allow_empty=False
        )
        chunk_range = None
    else:
        chunk_range = None
        keyword_filter = None

    return {
        "selection_mode": mode,
        "chunk_range": chunk_range,
        "keyword_filter": keyword_filter,
    }


def _decoded_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str) or len(value) > _MAX_CONFIG_TEXT_LENGTH:
        raise _EditConfigUnavailable
    decoded = json.loads(value)
    if not isinstance(decoded, Mapping):
        raise _EditConfigUnavailable
    return decoded


def _db_config(meta: Mapping[str, Any]) -> dict[str, object]:
    config = _decoded_mapping(meta.get("db_config"))
    budget = _ProjectionBudget()
    connection_id = config.get("connection_id", meta.get("connection_id"))
    try:
        projected_connection_id = UUID(str(connection_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise _EditConfigUnavailable from exc

    selected_from_rows, sensitive_from_rows = _selection_rows(
        config.get("selections"),
        budget,
    )
    selected_items = _string_list_mapping(config.get("selected_items"), budget)
    sensitive_columns = _string_list_mapping(
        config.get("sensitive_columns"),
        budget,
    )

    if selected_from_rows:
        if selected_items and selected_items != selected_from_rows:
            raise _EditConfigUnavailable
        selected_items = selected_from_rows
    if sensitive_from_rows:
        if sensitive_columns and sensitive_columns != sensitive_from_rows:
            raise _EditConfigUnavailable
        sensitive_columns = sensitive_from_rows

    template = config.get("template")
    if template is not None:
        template = _bounded_text(template, maximum=_MAX_TEMPLATE_LENGTH)

    return {
        "connection_id": projected_connection_id,
        "selected_items": selected_items,
        "sensitive_columns": sensitive_columns,
        "aliases": _aliases(config.get("aliases"), budget),
        "template": template,
        "join_config": _join_config(config.get("join_config"), budget),
    }


def _selection_rows(
    value: Any,
    budget: _ProjectionBudget,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    if value is None:
        return {}, {}
    if not isinstance(value, list) or len(value) > _MAX_TABLES:
        raise _EditConfigUnavailable
    budget.consume(len(value))

    selected: dict[str, list[str]] = {}
    sensitive: dict[str, list[str]] = {}
    for row in value:
        if not isinstance(row, Mapping):
            raise _EditConfigUnavailable
        table_name = _identifier(row.get("table_name"))
        if table_name in selected:
            raise _EditConfigUnavailable
        columns = _string_list(row.get("columns"), budget)
        sensitive_columns = _string_list(
            row.get("sensitive_columns", []),
            budget,
        )
        if not set(sensitive_columns).issubset(columns):
            raise _EditConfigUnavailable
        selected[table_name] = columns
        if sensitive_columns:
            sensitive[table_name] = sensitive_columns
    return selected, sensitive


def _string_list_mapping(
    value: Any,
    budget: _ProjectionBudget,
) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > _MAX_TABLES:
        raise _EditConfigUnavailable
    budget.consume(len(value))
    return {
        _identifier(key): _string_list(items, budget)
        for key, items in value.items()
    }


def _string_list(value: Any, budget: _ProjectionBudget) -> list[str]:
    if not isinstance(value, list) or len(value) > _MAX_COLUMNS_PER_TABLE:
        raise _EditConfigUnavailable
    budget.consume(len(value))
    projected = [_identifier(item) for item in value]
    if len(set(projected)) != len(projected):
        raise _EditConfigUnavailable
    return projected


def _identifier(value: Any) -> str:
    return _bounded_text(
        value,
        maximum=_MAX_IDENTIFIER_LENGTH,
        allow_empty=False,
    )


def _aliases(value: Any, budget: _ProjectionBudget) -> dict[str, dict[str, str]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > _MAX_TABLES:
        raise _EditConfigUnavailable
    budget.consume(len(value))
    projected: dict[str, dict[str, str]] = {}
    for table, table_aliases in value.items():
        table_name = _identifier(table)
        if not isinstance(table_aliases, Mapping):
            raise _EditConfigUnavailable
        if len(table_aliases) > _MAX_COLUMNS_PER_TABLE:
            raise _EditConfigUnavailable
        budget.consume(len(table_aliases) * 2)
        projected[table_name] = {
            _identifier(column): _bounded_text(alias, maximum=512)
            for column, alias in table_aliases.items()
        }
    return projected


def _join_config(value: Any, budget: _ProjectionBudget) -> dict[str, object]:
    if value is None:
        return {"enabled": False, "base_table": None, "joins": []}
    if not isinstance(value, Mapping):
        raise _EditConfigUnavailable

    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise _EditConfigUnavailable
    base_table = value.get("base_table")
    if base_table is not None:
        base_table = _identifier(base_table)
    joins = value.get("joins", [])
    if not isinstance(joins, list) or len(joins) > _MAX_JOIN_EDGES:
        raise _EditConfigUnavailable
    budget.consume(len(joins) * 4)

    projected_joins = []
    for join in joins:
        if not isinstance(join, Mapping):
            raise _EditConfigUnavailable
        projected_joins.append(
            {
                "from_table": _identifier(join.get("from_table")),
                "to_table": _identifier(join.get("to_table")),
                "from_column": _identifier(join.get("from_column")),
                "to_column": _identifier(join.get("to_column")),
            }
        )
    return {"enabled": enabled, "base_table": base_table, "joins": projected_joins}


def _api_config(meta: Mapping[str, Any]) -> dict[str, object]:
    config = meta.get("api_config")
    if not isinstance(config, Mapping):
        raise _EditConfigUnavailable

    encrypted_url = _optional_encrypted_value(config.get("url_encrypted"))
    encrypted_headers = _optional_encrypted_value(config.get("headers_encrypted"))
    encrypted_body = _optional_encrypted_value(config.get("body_encrypted"))
    if encrypted_url is None:
        raise _EditConfigUnavailable

    method = config.get("method", "GET")
    if not isinstance(method, str) or method.upper() not in {"GET", "POST"}:
        raise _EditConfigUnavailable
    safe_label = safe_label_from_text(config.get("safe_label")) or "API source"
    return {
        "configured": True,
        "method": method.upper(),
        "safe_label": safe_label,
        "has_headers": encrypted_headers is not None,
        "has_body": encrypted_body is not None,
    }


def _optional_encrypted_value(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > _MAX_CONFIG_TEXT_LENGTH:
        raise _EditConfigUnavailable
    return value


def _enforce_serialized_budget(value: Mapping[str, object]) -> None:
    serialized = json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(serialized) > _MAX_PROJECTED_CONFIG_BYTES:
        raise _EditConfigUnavailable
