from typing import Any, Literal

from apps.shared.db.models.knowledge import KnowledgeCollection

COLLECTION_SAFE_METADATA_FORBIDDEN_KEYS = {
    "raw",
    "raw_source_url",
    "raw_source_path",
    "raw_source_title",
    "raw_source_id",
    "raw_principal",
    "source_principal",
    "credential",
    "secret",
    "token",
}


def bucket_count(count: int) -> str:
    if count <= 0:
        return "0"
    if count == 1:
        return "1"
    if count <= 10:
        return "2-10"
    if count <= 100:
        return "11-100"
    if count <= 1000:
        return "101-1000"
    return "1000+"


def bulk_permission_count_bucket(
    count: int,
) -> Literal["0", "1", "2-10", "11-50"]:
    if count < 0 or count > 50:
        raise ValueError("bulk permission count must be between 0 and 50")
    if count == 0:
        return "0"
    if count == 1:
        return "1"
    if count <= 10:
        return "2-10"
    return "11-50"


def collection_visibility(collection: KnowledgeCollection) -> str:
    if (collection.safe_metadata or {}).get("visibility") == "public":
        return "public"
    return "private"


def safe_metadata_key_is_forbidden(key: str) -> bool:
    lowered = key.lower()
    return any(
        forbidden in lowered
        for forbidden in COLLECTION_SAFE_METADATA_FORBIDDEN_KEYS
    )


def sanitize_safe_metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, list):
        return [sanitize_safe_metadata_value(item) for item in value[:50]]
    raise TypeError("safe_metadata supports only primitive values and primitive lists.")


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def normalize_required_text(value: str) -> str:
    return " ".join(value.split())
