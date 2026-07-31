from __future__ import annotations

import logging
from typing import Callable, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from apps.gateway.services.audit_records import add_data_change_audit
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.team import TeamKnowledgePermission, UserKnowledgePermission

logger = logging.getLogger(__name__)


class StorageServiceProtocol(Protocol):
    def delete(self, file_path: str) -> None:
        pass


StorageServiceFactory = Callable[[], StorageServiceProtocol]
HardDeletePolicyChecker = Callable[[KnowledgeBase], bool]


class KnowledgeLifecycleNotFound(Exception):
    """Raised when a Knowledge lifecycle operation cannot find a valid KB state."""


class KnowledgeLifecyclePolicyDenied(Exception):
    """Raised when source ownership or retention policy forbids a mutation."""

    def __init__(self, reason_code: str = "source_managed") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _default_storage_service() -> StorageServiceProtocol:
    from apps.gateway.services.storage import get_storage_service

    return get_storage_service()


def _default_hard_delete_policy_checker(_kb: KnowledgeBase) -> bool:
    # Retention/legal-hold policy has no approved production primitive yet.
    return False


class KnowledgeLifecycleService:
    """Coordinates Knowledge Base lifecycle mutations inside the Gateway layer."""

    def __init__(
        self,
        db: Session,
        *,
        storage_service_factory: StorageServiceFactory = _default_storage_service,
        hard_delete_policy_checker: HardDeletePolicyChecker = (
            _default_hard_delete_policy_checker
        ),
    ) -> None:
        self.db = db
        self._storage_service_factory = storage_service_factory
        self._hard_delete_policy_checker = hard_delete_policy_checker

    def archive_knowledge_base(self, kb: KnowledgeBase, *, actor_id: UUID) -> None:
        self._require_manual_kb(kb)
        if kb.lifecycle_state != "active":
            raise KnowledgeLifecycleNotFound
        kb.lifecycle_state = "archived"
        add_data_change_audit(
            self.db,
            "knowledge.archived",
            actor_id,
            "knowledge_base",
            kb.id,
            organization_id=kb.organization_id,
            before={"lifecycle_state": "active"},
            after={"lifecycle_state": "archived"},
        )
        self._commit_or_rollback()

    def restore_knowledge_base(self, kb: KnowledgeBase, *, actor_id: UUID) -> None:
        self._require_manual_kb(kb)
        if kb.lifecycle_state != "archived":
            raise KnowledgeLifecycleNotFound
        kb.lifecycle_state = "active"
        add_data_change_audit(
            self.db,
            "knowledge.restored",
            actor_id,
            "knowledge_base",
            kb.id,
            organization_id=kb.organization_id,
            before={"lifecycle_state": "archived"},
            after={"lifecycle_state": "active"},
        )
        self._commit_or_rollback()

    def hard_delete_knowledge_base(
        self,
        kb: KnowledgeBase,
        *,
        actor_id: UUID,
    ) -> None:
        self._require_manual_kb(kb)
        self._require_hard_delete_policy(kb)
        self._delete_document_files_best_effort(kb)
        try:
            self._delete_direct_permission_rows(kb)
            add_data_change_audit(
                self.db,
                "knowledge.hard_deleted",
                actor_id,
                "knowledge_base",
                kb.id,
                organization_id=kb.organization_id,
                before={"lifecycle_state": kb.lifecycle_state},
                after=None,
                metadata={"acknowledged_hard_delete": True},
            )
            self.db.delete(kb)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _require_manual_kb(self, kb: KnowledgeBase) -> None:
        if getattr(kb, "source_identity_id", None) is not None:
            raise KnowledgeLifecyclePolicyDenied("source_managed")

    def _require_hard_delete_policy(self, kb: KnowledgeBase) -> None:
        try:
            allowed = self._hard_delete_policy_checker(kb)
        except Exception as exc:
            logger.warning(
                "Knowledge hard-delete policy evaluation failed: %s",
                type(exc).__name__,
            )
            allowed = False
        if not allowed:
            raise KnowledgeLifecyclePolicyDenied("retention_policy_unavailable")

    def _commit_or_rollback(self) -> None:
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _delete_document_files_best_effort(self, kb: KnowledgeBase) -> None:
        try:
            storage = self._storage_service_factory()
        except Exception as exc:
            logger.warning(
                "Failed to initialize document storage cleanup: %s",
                type(exc).__name__,
            )
            return

        for doc in kb.documents:
            if not doc.file_path:
                continue
            try:
                storage.delete(doc.file_path)
            except Exception as exc:
                logger.warning(
                    "Failed to delete document file for doc %s: %s",
                    doc.id,
                    type(exc).__name__,
                )

    def _delete_direct_permission_rows(self, kb: KnowledgeBase) -> None:
        self.db.query(UserKnowledgePermission).filter(
            UserKnowledgePermission.knowledge_base_id == kb.id,
        ).delete(synchronize_session=False)
        self.db.query(TeamKnowledgePermission).filter(
            TeamKnowledgePermission.knowledge_base_id == kb.id,
        ).delete(synchronize_session=False)
