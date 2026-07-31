from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

CHUNKING_MODE_FLAT = "flat"
CHUNKING_MODE_HIERARCHICAL = "hierarchical"
CHUNKING_MODES = {CHUNKING_MODE_FLAT, CHUNKING_MODE_HIERARCHICAL}

HIERARCHY_MODE_AUTO = "auto"
HIERARCHY_MODE_FLAT = "flat"
HIERARCHY_MODE_PARENT_CHILD = "parent_child"
HIERARCHY_MODES = {
    HIERARCHY_MODE_AUTO,
    HIERARCHY_MODE_FLAT,
    HIERARCHY_MODE_PARENT_CHILD,
}

HIERARCHY_VERSION = 1
DEFAULT_PARENT_TARGET_SIZE = 4000
MIN_PARENT_TARGET_SIZE = 2000
MAX_PARENT_TARGET_SIZE = 8000
MIN_PARENT_OVERLAP_SIZE = 200
VALID_CHUNK_LEVELS = {"parent", "child", "flat"}
EVIDENCE_CHUNK_LEVELS = {"child", "flat", None}


@dataclass(frozen=True)
class RAGHierarchyError(ValueError):
    reason: str
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


def source_type_value(source_type: Any) -> str:
    return str(getattr(source_type, "value", source_type) or "").upper()


def normalize_chunking_mode(value: Any) -> str:
    mode = str(value or CHUNKING_MODE_FLAT).strip().lower()
    if mode not in CHUNKING_MODES:
        raise RAGHierarchyError(
            reason="invalid_chunking_mode",
            message="chunkingMode must be flat or hierarchical.",
            status_code=400,
        )
    return mode


def normalize_hierarchy_mode(value: Any) -> str:
    mode = str(value or HIERARCHY_MODE_AUTO).strip().lower()
    if mode not in HIERARCHY_MODES:
        return HIERARCHY_MODE_AUTO
    return mode


def validate_chunking_request(
    *,
    chunking_mode: Any,
    source_type: Any,
    selection_mode: Any = "all",
) -> str:
    mode = normalize_chunking_mode(chunking_mode)
    source = source_type_value(source_type)
    selection = str(selection_mode or "all").strip().lower()

    if mode == CHUNKING_MODE_HIERARCHICAL and source == "DB":
        raise RAGHierarchyError(
            reason="unsupported_chunking_mode_for_source",
            message="hierarchical chunking is not supported for DB source.",
            status_code=400,
        )
    if mode == CHUNKING_MODE_HIERARCHICAL and selection == "range":
        raise RAGHierarchyError(
            reason="invalid_chunking_selection",
            message="hierarchical chunking does not support range selection.",
            status_code=400,
        )
    return mode


def document_chunking_mode(meta_info: dict[str, Any] | None) -> str:
    if not isinstance(meta_info, dict):
        return CHUNKING_MODE_FLAT
    return normalize_chunking_mode((meta_info or {}).get("chunking_mode"))


def chunking_fingerprint_hash(
    *,
    meta_info: dict[str, Any] | None,
    chunk_size: int,
    chunk_overlap: int,
    source_type: Any,
) -> str:
    meta = dict(meta_info or {}) if isinstance(meta_info, dict) else {}
    mode = normalize_chunking_mode(meta.get("chunking_mode"))
    payload = {
        "chunking_mode": mode,
        "hierarchy_version": HIERARCHY_VERSION,
        "chunk_size": int(chunk_size),
        "chunk_overlap": int(chunk_overlap),
        "segment_identifier": meta.get("segment_identifier", "\n\n"),
        "remove_urls_emails": bool(meta.get("remove_urls_emails", False)),
        "remove_whitespace": bool(meta.get("remove_whitespace", True)),
        "strategy": str(meta.get("strategy", "general")),
        "source_type": source_type_value(source_type),
        "selection_mode": str(meta.get("selection_mode", "all")),
        "chunk_range": meta.get("chunk_range"),
        "keyword_filter": meta.get("keyword_filter"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def parent_candidate_limit(top_k: int) -> int:
    return min(max(int(top_k) * 4, 8), 40)


def child_pool_limit(top_k: int) -> int:
    return min(max(int(top_k) * 10, 20), 100)


def parent_target_size(child_chunk_size: int) -> int:
    return min(
        max(int(child_chunk_size) * 4, MIN_PARENT_TARGET_SIZE),
        MAX_PARENT_TARGET_SIZE,
    )


def parent_overlap_size(child_chunk_overlap: int, target_size: int) -> int:
    return min(
        max(int(child_chunk_overlap) * 2, MIN_PARENT_OVERLAP_SIZE),
        int(target_size) // 4,
    )


def normalized_chunk_level(value: Any) -> str | None:
    if value is None:
        return None
    level = str(value).strip().lower()
    return level if level in VALID_CHUNK_LEVELS else "unknown"


def is_evidence_chunk_level(value: Any) -> bool:
    return normalized_chunk_level(value) in EVIDENCE_CHUNK_LEVELS


def is_parent_chunk_level(value: Any) -> bool:
    return normalized_chunk_level(value) == "parent"


def contains_non_flat_payload(chunks: Iterable[dict[str, Any]]) -> bool:
    for chunk in chunks:
        level = normalized_chunk_level(chunk.get("chunk_level"))
        if level not in {None, "flat"}:
            return True
        if normalize_chunking_mode(chunk.get("chunking_mode")) == CHUNKING_MODE_HIERARCHICAL:
            return True
    return False
