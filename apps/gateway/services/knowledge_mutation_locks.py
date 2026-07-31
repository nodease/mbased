from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from apps.shared.db.models.knowledge import Document, KnowledgeBase


def lock_fresh_knowledge_base(
    db: Session,
    *,
    knowledge_base_id: UUID,
    organization_id: UUID,
) -> KnowledgeBase | None:
    """Lock a KB and overwrite any stale identity-map state from the database."""

    return (
        db.query(KnowledgeBase)
        .filter(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.organization_id == organization_id,
        )
        .populate_existing()
        .with_for_update()
        .first()
    )


def lock_fresh_document(
    db: Session,
    *,
    document_id: UUID,
    knowledge_base_id: UUID,
) -> Document | None:
    """Lock a document and overwrite any stale identity-map state from the database."""

    return (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.knowledge_base_id == knowledge_base_id,
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
