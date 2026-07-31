from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.gateway.services.knowledge_mutation_locks import (
    lock_fresh_knowledge_base,
)
from apps.shared.db.models.knowledge import (
    Document,
    DocumentVersion,
    KnowledgeBase,
    SourceType,
)


class KnowledgeDocumentRegistrationError(Exception):
    """Base error for initial document registration."""


class KnowledgeDocumentRegistrationHidden(KnowledgeDocumentRegistrationError):
    pass


class KnowledgeDocumentRegistrationPolicyDenied(
    KnowledgeDocumentRegistrationError
):
    pass


class KnowledgeDocumentSlotOccupied(KnowledgeDocumentRegistrationError):
    pass


class KnowledgeDocumentRegistrationUnavailable(
    KnowledgeDocumentRegistrationError
):
    def __init__(self, *, artifact_cleanup_safe: bool = False) -> None:
        super().__init__()
        self.artifact_cleanup_safe = artifact_cleanup_safe


def is_initial_document_registration_eligible(kb: KnowledgeBase) -> bool:
    """Return the KB-level policy result without evaluating caller authority."""

    return _is_initial_document_registration_source_eligible(kb)


def _is_initial_document_registration_source_eligible(kb: KnowledgeBase) -> bool:
    return (
        getattr(kb, "lifecycle_state", None) == "active"
        and getattr(kb, "source_identity_id", None) is None
        and getattr(kb, "sync_state", None) == "manual"
    )


class KnowledgeDocumentRegistrationService:
    """Own the one-independent-source-per-KB registration invariant."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_available(
        self,
        *,
        knowledge_base_id: UUID,
        organization_id: UUID,
    ) -> None:
        """Fast precheck before an external storage write.

        This check intentionally does not lock the KB. The canonical registration
        method repeats the decision while holding the KB row lock.
        """

        try:
            kb = self._load_kb(
                knowledge_base_id=knowledge_base_id,
                organization_id=organization_id,
                lock=False,
            )
            self._ensure_source_policy_allows_registration(kb)
            self._ensure_document_slot_empty(knowledge_base_id)
        except KnowledgeDocumentRegistrationError:
            raise
        except SQLAlchemyError:
            raise KnowledgeDocumentRegistrationUnavailable() from None

    def register_initial_document(
        self,
        *,
        knowledge_base_id: UUID,
        organization_id: UUID,
        filename: str,
        file_path: str | None,
        chunk_size: int,
        chunk_overlap: int,
        source_type: SourceType,
        meta_info: dict[str, Any] | None = None,
    ) -> UUID:
        commit_started = False
        try:
            kb = self._load_kb(
                knowledge_base_id=knowledge_base_id,
                organization_id=organization_id,
                lock=True,
            )
            self._ensure_source_policy_allows_registration(kb)
            self._ensure_document_slot_empty(knowledge_base_id)
            self._release_stale_active_document_pointer(kb)

            document = Document(
                knowledge_base_id=knowledge_base_id,
                filename=filename,
                file_path=file_path,
                status="pending",
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                source_type=source_type,
                meta_info=dict(meta_info or {}),
            )
            self.db.add(document)
            self.db.flush()
            document_id = document.id
            commit_started = True
            self.db.commit()
            return document_id
        except KnowledgeDocumentRegistrationError:
            self.db.rollback()
            raise
        except SQLAlchemyError:
            self.db.rollback()
            raise KnowledgeDocumentRegistrationUnavailable(
                artifact_cleanup_safe=not commit_started,
            ) from None

    def _load_kb(
        self,
        *,
        knowledge_base_id: UUID,
        organization_id: UUID,
        lock: bool,
    ) -> KnowledgeBase:
        if lock:
            kb = lock_fresh_knowledge_base(
                self.db,
                knowledge_base_id=knowledge_base_id,
                organization_id=organization_id,
            )
            if kb is None or getattr(kb, "lifecycle_state", None) != "active":
                raise KnowledgeDocumentRegistrationHidden()
            return kb

        query = self.db.query(KnowledgeBase).filter(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.organization_id == organization_id,
        )
        kb = query.first()
        if kb is None or getattr(kb, "lifecycle_state", None) != "active":
            raise KnowledgeDocumentRegistrationHidden()
        return kb

    @staticmethod
    def _ensure_source_policy_allows_registration(kb: KnowledgeBase) -> None:
        if not _is_initial_document_registration_source_eligible(kb):
            raise KnowledgeDocumentRegistrationPolicyDenied()

    def _release_stale_active_document_pointer(self, kb: KnowledgeBase) -> None:
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
        if (
            active_version is not None
            and (
                active_version.legacy_document_id is not None
                or active_version.source_identity_id is not None
            )
        ):
            raise KnowledgeDocumentRegistrationPolicyDenied()

        kb.active_document_version_id = None
        if active_version is not None and active_version.status != "superseded":
            active_version.status = "superseded"
            active_version.superseded_at = datetime.now(timezone.utc)

    def _ensure_document_slot_empty(self, knowledge_base_id: UUID) -> None:
        existing_document_id = (
            self.db.query(Document.id)
            .filter(Document.knowledge_base_id == knowledge_base_id)
            .first()
        )
        if existing_document_id is not None:
            raise KnowledgeDocumentSlotOccupied()
