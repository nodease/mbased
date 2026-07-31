from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.gateway.services.knowledge_mutation_locks import (
    lock_fresh_document,
    lock_fresh_knowledge_base,
)
from apps.shared.db.models.knowledge import Document, DocumentVersion, KnowledgeBase
from apps.shared.services.ingestion.vector_store_service import (
    acquire_document_write_lock,
)


class KnowledgeDocumentLifecycleError(Exception):
    """Base error for document lifecycle mutations."""


class KnowledgeDocumentLifecycleHidden(KnowledgeDocumentLifecycleError):
    pass


class KnowledgeDocumentLifecycleUnavailable(KnowledgeDocumentLifecycleError):
    pass


@dataclass(frozen=True)
class DeletedKnowledgeDocument:
    file_path: str | None


class KnowledgeDocumentLifecycleService:
    """Own document deletion and the one-document KB slot transition."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def delete_document(
        self,
        *,
        knowledge_base_id: UUID,
        organization_id: UUID,
        document_id: UUID,
    ) -> DeletedKnowledgeDocument:
        try:
            # Ingestion and Collection sync use the same transaction lock before
            # taking document/KB row locks. Deletion must join that ordering.
            acquire_document_write_lock(self.db, document_id)
            kb = lock_fresh_knowledge_base(
                self.db,
                knowledge_base_id=knowledge_base_id,
                organization_id=organization_id,
            )
            document = lock_fresh_document(
                self.db,
                document_id=document_id,
                knowledge_base_id=knowledge_base_id,
            )
            if (
                kb is None
                or getattr(kb, "lifecycle_state", None) != "active"
                or document is None
            ):
                raise KnowledgeDocumentLifecycleHidden()

            self._release_active_version_if_owned(kb, document)
            deleted = DeletedKnowledgeDocument(file_path=document.file_path)
            self.db.delete(document)
            self.db.commit()
            return deleted
        except KnowledgeDocumentLifecycleError:
            self.db.rollback()
            raise
        except SQLAlchemyError:
            self.db.rollback()
            raise KnowledgeDocumentLifecycleUnavailable() from None

    def _release_active_version_if_owned(
        self,
        kb: KnowledgeBase,
        document: Document,
    ) -> None:
        active_version_id = getattr(kb, "active_document_version_id", None)
        if active_version_id is None:
            return

        active_version = (
            self.db.query(DocumentVersion)
            .filter(
                DocumentVersion.id == active_version_id,
                DocumentVersion.knowledge_base_id == kb.id,
            )
            .populate_existing()
            .with_for_update()
            .first()
        )
        other_document_exists = (
            self.db.query(Document.id)
            .filter(
                Document.knowledge_base_id == kb.id,
                Document.id != document.id,
            )
            .first()
            is not None
        )
        active_legacy_document_id = getattr(
            active_version,
            "legacy_document_id",
            None,
        )
        should_release = (
            active_version is None
            or active_legacy_document_id == document.id
            or (active_legacy_document_id is None and not other_document_exists)
        )
        if not should_release:
            return

        kb.active_document_version_id = None
        if active_version is not None and active_version.status != "superseded":
            active_version.status = "superseded"
            active_version.superseded_at = datetime.now(timezone.utc)
