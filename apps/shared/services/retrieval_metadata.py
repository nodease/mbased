METADATA_SUMMARY_ALLOWED_KEYS = {
    "classification",
    "tags",
    "source_type",
    "source_hash",
    "document_version",
    "effective_from",
    "effective_to",
    "metadata_version",
    "search_method",
    "rerank_score",
    "rrf_score",
    "score",
    "parent_chunk_id",
    "token_count",
    "chunk_level",
    "section_path",
    "heading",
    "hierarchy_fallback",
    "source_tier",
}
MAX_METADATA_SUMMARY_STRING_LENGTH = 200
MAX_METADATA_SUMMARY_LIST_ITEMS = 20


def _safe_metadata_mapping(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    return {}


def chunk_metadata(chunk, doc=None) -> dict:
    chunk_metadata_ = _safe_metadata_mapping(getattr(chunk, "metadata_", None))
    document_metadata = (
        _safe_metadata_mapping(getattr(doc, "meta_info", None)) if doc else {}
    )
    metadata = dict(chunk_metadata_)
    for key, value in document_metadata.items():
        if value is not None:
            metadata[key] = value
    if doc is not None and "source_type" not in metadata:
        source_type = getattr(doc, "source_type", None)
        if source_type is not None:
            metadata["source_type"] = str(getattr(source_type, "value", source_type))
    parent_chunk_id = getattr(chunk, "parent_chunk_id", None)
    if parent_chunk_id is not None and "parent_chunk_id" not in metadata:
        metadata["parent_chunk_id"] = str(parent_chunk_id)
    token_count = getattr(chunk, "token_count", None)
    if token_count is not None and "token_count" not in metadata:
        metadata["token_count"] = token_count
    if "chunk_level" not in metadata:
        metadata["chunk_level"] = getattr(chunk, "chunk_level", None) or "flat"
    source_tier = getattr(chunk, "source_tier", None)
    if source_tier is None:
        document_version = getattr(chunk, "document_version", None)
        source_tier = getattr(document_version, "source_tier", None)
    if source_tier and "source_tier" not in metadata:
        metadata["source_tier"] = str(source_tier)
    section_path = getattr(chunk, "section_path", None)
    if section_path is not None and "section_path" not in metadata:
        metadata["section_path"] = section_path
    heading = getattr(chunk, "heading", None)
    if heading and "heading" not in metadata:
        metadata["heading"] = heading
    return metadata


def metadata_summary(metadata: dict | None) -> dict:
    if not metadata:
        return {}
    summary = {}
    for key in METADATA_SUMMARY_ALLOWED_KEYS:
        if key not in metadata:
            continue
        value = _safe_summary_value(key, metadata[key])
        if value is not None:
            summary[key] = value
    return summary


def _safe_summary_value(key, value, *, nested: bool = False):
    if key == "tags" and not nested and not isinstance(value, list):
        return None
    if isinstance(value, str):
        return value[:MAX_METADATA_SUMMARY_STRING_LENGTH]
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        safe_items = []
        for item in value[:MAX_METADATA_SUMMARY_LIST_ITEMS]:
            safe_item = _safe_summary_value(key, item, nested=True)
            if safe_item is not None:
                safe_items.append(safe_item)
        return safe_items
    return None


def hierarchy_path(metadata: dict | None) -> list[str] | None:
    if not metadata:
        return None
    section_path = metadata.get("section_path")
    if isinstance(section_path, list):
        return [str(item) for item in section_path if str(item).strip()]
    if isinstance(section_path, str) and section_path.strip():
        return [part.strip() for part in section_path.split("/") if part.strip()]
    heading = metadata.get("heading")
    if heading:
        return [str(heading)]
    return None
