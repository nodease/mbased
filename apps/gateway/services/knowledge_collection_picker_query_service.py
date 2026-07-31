"""Route-authorized, disclosure-minimal Collection projection for Builder."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from apps.shared.db.models.knowledge import KnowledgeCollection
from apps.shared.schemas.knowledge import (
    KnowledgeCollectionLLMSelectableItem,
    KnowledgeCollectionLLMSelectableResponse,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.knowledge_resource_eligibility import (
    knowledge_collection_operational_predicates,
)
from apps.shared.services.knowledge_safe_text import safe_label_from_text

MAX_LLM_SELECTABLE_COLLECTION_SCAN = 500


class KnowledgeCollectionPickerUnavailable(RuntimeError):
    """Sanitized infrastructure failure for the route-safe picker."""


class KnowledgeCollectionPickerQueryService:
    """List active Collections the current editor may route through.

    This projection intentionally does not reuse Collection management responses:
    it exposes neither raw names/descriptions nor child-derived facts/capabilities.
    """

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

    def list_llm_selectable(self) -> KnowledgeCollectionLLMSelectableResponse:
        try:
            query = (
                self.db.query(KnowledgeCollection)
                .options(selectinload(KnowledgeCollection.source_identity))
                .filter(
                    KnowledgeCollection.organization_id == self.organization_id,
                    *knowledge_collection_operational_predicates(),
                )
            )
            query = self.permission_helper.scope_collection_query_for_action(
                query,
                "route",
            )
            collections = (
                query
                .order_by(
                    KnowledgeCollection.created_at.desc(),
                    KnowledgeCollection.id.asc(),
                )
                .limit(MAX_LLM_SELECTABLE_COLLECTION_SCAN)
                .all()
            )
        except SQLAlchemyError as exc:
            raise KnowledgeCollectionPickerUnavailable(
                "knowledge_collection_picker_unavailable"
            ) from exc

        return KnowledgeCollectionLLMSelectableResponse(
            collections=[
                KnowledgeCollectionLLMSelectableItem(
                    id=collection.id,
                    safe_label=self._approved_safe_label(collection),
                )
                for collection in collections
            ]
        )

    @staticmethod
    def _approved_safe_label(collection: KnowledgeCollection) -> str | None:
        source_identity = getattr(collection, "source_identity", None)
        if getattr(collection, "source_identity_id", None) is not None:
            if (
                source_identity is None
                or getattr(source_identity, "display_policy_state", None) != "approved"
                or not getattr(source_identity, "is_active", False)
            ):
                return None
            return safe_label_from_text(
                getattr(source_identity, "safe_display_name", None)
            )

        safe_metadata = getattr(collection, "safe_metadata", None)
        if not isinstance(safe_metadata, dict):
            return None
        return safe_label_from_text(safe_metadata.get("safe_label"))
