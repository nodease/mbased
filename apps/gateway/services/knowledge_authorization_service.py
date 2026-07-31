from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from apps.shared.db.models.knowledge import Document, KnowledgeBase
from apps.shared.permissions import knowledge_base_auth_state_allows
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.permissions import (
    get_effective_knowledge_base_auth_state,
    has_knowledge_domain_permission,
)


@dataclass(frozen=True)
class KnowledgeAuthorizationCapabilities:
    can_read: bool
    can_use: bool
    can_write: bool
    can_read_content: bool
    can_manage: bool


class KnowledgeResourceHidden(Exception):
    pass


class KnowledgePermissionDenied(Exception):
    pass


class KnowledgeAuthorizationService:
    """Active-organization scoped KB object/property authorization boundary."""

    def __init__(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id
        self.permission_helper = KnowledgePermissionHelper(
            db,
            user_id=user_id,
            organization_id=organization_id,
        )

    def load_kb(
        self,
        knowledge_base_id: uuid.UUID,
        action: str,
        *,
        include_archived: bool = False,
        domain_action: str | None = None,
    ) -> KnowledgeBase:
        lifecycle_states = ["active", "archived"] if include_archived else ["active"]
        kb = (
            self.db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.organization_id == self.organization_id,
                KnowledgeBase.lifecycle_state.in_(lifecycle_states),
            )
            .first()
        )
        if kb is None:
            raise KnowledgeResourceHidden()

        decision = self.permission_helper.evaluate_kb_action(
            kb,
            action,
            include_archived=include_archived,
        )
        if decision.allowed:
            return kb
        if domain_action and has_knowledge_domain_permission(
            self.db,
            self.user_id,
            self.organization_id,
            domain_action,
        ):
            return kb
        if decision.resource_visibility in {"resource_hidden", "hidden"}:
            raise KnowledgeResourceHidden()
        raise KnowledgePermissionDenied()

    def load_document(
        self,
        knowledge_base_id: uuid.UUID,
        document_id: uuid.UUID,
        action: str,
        *,
        domain_action: str | None = None,
    ) -> tuple[KnowledgeBase, Document]:
        kb = self.load_kb(
            knowledge_base_id,
            action,
            domain_action=domain_action,
        )
        document = (
            self.db.query(Document)
            .filter(
                Document.id == document_id,
                Document.knowledge_base_id == kb.id,
            )
            .first()
        )
        if document is None:
            raise KnowledgeResourceHidden()
        return kb, document

    def capabilities(
        self,
        kb: KnowledgeBase,
        *,
        include_archived: bool = False,
    ) -> KnowledgeAuthorizationCapabilities:
        auth_state = get_effective_knowledge_base_auth_state(
            self.db,
            self.user_id,
            kb.id,
            organization_id=self.organization_id,
            include_archived=include_archived,
        )
        manual_content = getattr(kb, "source_identity_id", None) is None
        return KnowledgeAuthorizationCapabilities(
            can_read=knowledge_base_auth_state_allows(auth_state, "read"),
            can_use=knowledge_base_auth_state_allows(auth_state, "use"),
            can_write=knowledge_base_auth_state_allows(auth_state, "write"),
            can_read_content=(
                manual_content
                and knowledge_base_auth_state_allows(auth_state, "content_read")
            ),
            can_manage=knowledge_base_auth_state_allows(auth_state, "manage"),
        )
