import hashlib
from typing import Any

VERSION_FENCING_TOKEN_HASH_KEY = "ingestion_fencing_token_hash"
ACTIVE_FENCING_TOKEN_HASH_KEY = "active_ingestion_fencing_token_hash"


class KnowledgeIngestionFencing:
    """Knowledge ingestion worker ownership token을 안전한 hash metadata로 관리한다."""

    @staticmethod
    def hash_token(fencing_token: str) -> str:
        return hashlib.sha256(fencing_token.encode("utf-8")).hexdigest()

    @classmethod
    def version_metadata(
        cls,
        safe_metadata: dict[str, Any] | None,
        fencing_token: str | None,
    ) -> dict[str, Any]:
        metadata = dict(safe_metadata or {})
        if fencing_token:
            metadata[VERSION_FENCING_TOKEN_HASH_KEY] = cls.hash_token(fencing_token)
        return metadata

    @classmethod
    def active_document_meta_update(cls, fencing_token: str) -> dict[str, str]:
        return {
            ACTIVE_FENCING_TOKEN_HASH_KEY: cls.hash_token(fencing_token),
        }

    @classmethod
    def clear_active_document_metadata(
        cls, meta_info: dict[str, Any] | None
    ) -> dict[str, Any]:
        metadata = dict(meta_info or {})
        metadata.pop(ACTIVE_FENCING_TOKEN_HASH_KEY, None)
        return metadata

    @classmethod
    def version_hash(cls, document_version: Any) -> str | None:
        return (getattr(document_version, "safe_metadata", None) or {}).get(
            VERSION_FENCING_TOKEN_HASH_KEY
        )

    @classmethod
    def active_document_hash(cls, document: Any) -> str | None:
        return (getattr(document, "meta_info", None) or {}).get(
            ACTIVE_FENCING_TOKEN_HASH_KEY
        )
