"""Canonical database predicates for routable Knowledge resources."""

from __future__ import annotations

from typing import Any

from apps.shared.db.models.knowledge import (
    Document,
    DocumentChunk,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeCollection,
)
from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

ACTIVE_LIFECYCLE_STATE = "active"
SOURCE_DELETED_SYNC_STATE = "source_deleted"
_MISSING_SOURCE_IDENTITY = object()


def knowledge_base_operational_predicates() -> tuple[ColumnElement[bool], ...]:
    """Return the canonical SQL scope for a routable Knowledge Base."""

    return (
        KnowledgeBase.lifecycle_state == ACTIVE_LIFECYCLE_STATE,
        KnowledgeBase.sync_state != SOURCE_DELETED_SYNC_STATE,
    )


def knowledge_collection_operational_predicates() -> tuple[
    ColumnElement[bool], ...
]:
    """Return the canonical SQL scope for a routable Knowledge Collection."""

    return (
        KnowledgeCollection.lifecycle_state == ACTIVE_LIFECYCLE_STATE,
        KnowledgeCollection.sync_state != SOURCE_DELETED_SYNC_STATE,
    )


def knowledge_collection_anonymous_public_predicates() -> tuple[
    ColumnElement[bool], ...
]:
    """Return the current fail-closed anonymous public Collection scope."""

    return (
        *knowledge_collection_operational_predicates(),
        KnowledgeCollection.source_identity_id.is_(None),
        KnowledgeCollection.safe_metadata["visibility"].astext == "public",
    )


def retrieval_visible_chunk_exists() -> ColumnElement[bool]:
    """Return a correlated EXISTS predicate for a retrieval-visible chunk."""

    return (
        select(DocumentChunk.id)
        .select_from(DocumentChunk)
        .join(
            Document,
            and_(
                Document.id == DocumentChunk.document_id,
                Document.knowledge_base_id == DocumentChunk.knowledge_base_id,
            ),
        )
        .outerjoin(
            DocumentVersion,
            and_(
                DocumentVersion.id == DocumentChunk.document_version_id,
                DocumentVersion.knowledge_base_id
                == DocumentChunk.knowledge_base_id,
                DocumentVersion.organization_id == KnowledgeBase.organization_id,
            ),
        )
        .where(
            DocumentChunk.knowledge_base_id == KnowledgeBase.id,
            Document.status == "completed",
            or_(
                and_(
                    KnowledgeBase.active_document_version_id.is_(None),
                    DocumentChunk.document_version_id.is_(None),
                ),
                and_(
                    KnowledgeBase.active_document_version_id.is_not(None),
                    DocumentChunk.document_version_id
                    == KnowledgeBase.active_document_version_id,
                    DocumentVersion.status == "ready",
                ),
            ),
        )
        .exists()
    )


def is_operational_knowledge_resource(resource: Any) -> bool:
    """Apply the same lifecycle/sync rule to an already-loaded resource."""

    return (
        getattr(resource, "lifecycle_state", None) == ACTIVE_LIFECYCLE_STATE
        and getattr(resource, "sync_state", None) != SOURCE_DELETED_SYNC_STATE
    )


def is_anonymous_public_knowledge_collection(resource: Any) -> bool:
    """Apply the anonymous public Collection scope to an already-loaded row."""

    safe_metadata = getattr(resource, "safe_metadata", None)
    source_identity_id = getattr(
        resource,
        "source_identity_id",
        _MISSING_SOURCE_IDENTITY,
    )
    return (
        is_operational_knowledge_resource(resource)
        and isinstance(safe_metadata, dict)
        and safe_metadata.get("visibility") == "public"
        and source_identity_id is None
    )


__all__ = [
    "ACTIVE_LIFECYCLE_STATE",
    "SOURCE_DELETED_SYNC_STATE",
    "is_anonymous_public_knowledge_collection",
    "is_operational_knowledge_resource",
    "knowledge_base_operational_predicates",
    "knowledge_collection_anonymous_public_predicates",
    "knowledge_collection_operational_predicates",
    "retrieval_visible_chunk_exists",
]
