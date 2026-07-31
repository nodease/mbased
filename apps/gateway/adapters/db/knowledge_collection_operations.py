from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.orm import Session

from apps.gateway.application.knowledge_administration.collection_operations import (
    CollectionItemOrderSnapshot,
    CollectionItemRank,
    CollectionSnapshot,
)
from apps.shared.db.models.knowledge import (
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.permissions import (
    get_effective_knowledge_domain_actions,
    has_organization_manager_permission,
)


class SqlAlchemyKnowledgeCollectionOperationAuthorization:
    def __init__(self, db: Session) -> None:
        self.db = db

    def is_organization_manager(
        self, actor_id: uuid.UUID, organization_id: uuid.UUID
    ) -> bool:
        return has_organization_manager_permission(
            self.db, actor_id, organization_id
        )

    def has_domain_action(
        self,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
        action: str,
    ) -> bool:
        return action in get_effective_knowledge_domain_actions(
            self.db, actor_id, organization_id
        )

    def has_collection_action(
        self,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        action: str,
    ) -> bool:
        collection = (
            self.db.query(KnowledgeCollection)
            .filter(
                KnowledgeCollection.id == collection_id,
                KnowledgeCollection.organization_id == organization_id,
            )
            .first()
        )
        if collection is None:
            return False
        helper = KnowledgePermissionHelper(
            self.db,
            user_id=actor_id,
            organization_id=organization_id,
        )
        return helper.evaluate_collection_action(
            collection,
            action,
            include_archived=True,
        ).allowed


class SqlAlchemyKnowledgeCollectionOperationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._locked_collections: dict[uuid.UUID, KnowledgeCollection] = {}
        self._locked_items: dict[uuid.UUID, KnowledgeCollectionItem] = {}

    def lock_collection(
        self, organization_id: uuid.UUID, collection_id: uuid.UUID
    ) -> CollectionSnapshot | None:
        collection = (
            self.db.query(KnowledgeCollection)
            .filter(
                KnowledgeCollection.id == collection_id,
                KnowledgeCollection.organization_id == organization_id,
            )
            .with_for_update()
            .first()
        )
        if collection is None:
            return None
        self._locked_collections[collection.id] = collection
        metadata = dict(collection.safe_metadata or {})
        return CollectionSnapshot(
            collection_id=collection.id,
            lifecycle_state=collection.lifecycle_state,
            sync_state=collection.sync_state,
            is_system_managed=collection.is_system_managed,
            is_source_managed=(
                collection.source_identity_id is not None
                or bool(collection.source_connector_ref)
            ),
            visibility=(
                "public" if metadata.get("visibility") == "public" else "private"
            ),
        )

    def set_lifecycle_state(
        self,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        lifecycle_state: str,
    ) -> None:
        collection = self._locked_collection(organization_id, collection_id)
        collection.lifecycle_state = lifecycle_state
        collection.updated_at = datetime.now(timezone.utc)

    def lock_item_order(
        self, organization_id: uuid.UUID, collection_id: uuid.UUID
    ) -> list[CollectionItemOrderSnapshot]:
        rows = (
            self.db.query(KnowledgeCollectionItem)
            .filter(
                KnowledgeCollectionItem.organization_id == organization_id,
                KnowledgeCollectionItem.collection_id == collection_id,
            )
            .order_by(
                KnowledgeCollectionItem.rank.asc(),
                KnowledgeCollectionItem.created_at.asc(),
                KnowledgeCollectionItem.id.asc(),
            )
            .with_for_update()
            .all()
        )
        self._locked_items = {row.id: row for row in rows}
        return [
            CollectionItemOrderSnapshot(
                item_id=row.id,
                rank=row.rank,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def set_item_ranks(
        self,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        ranks: Sequence[CollectionItemRank],
    ) -> None:
        expected_ids = {row.item_id for row in ranks}
        if expected_ids != set(self._locked_items):
            raise RuntimeError("collection item lock set changed")
        for rank in ranks:
            self._locked_items[rank.item_id].rank = rank.rank
        collection = self._locked_collection(organization_id, collection_id)
        collection.updated_at = datetime.now(timezone.utc)

    def has_source_managed_items(
        self,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
    ) -> bool:
        return (
            self.db.query(KnowledgeCollectionItem.id)
            .join(
                KnowledgeBase,
                KnowledgeBase.id == KnowledgeCollectionItem.knowledge_base_id,
            )
            .filter(
                KnowledgeCollectionItem.organization_id == organization_id,
                KnowledgeCollectionItem.collection_id == collection_id,
                KnowledgeBase.organization_id == organization_id,
                KnowledgeBase.source_identity_id.is_not(None),
            )
            .first()
            is not None
        )

    def _locked_collection(
        self, organization_id: uuid.UUID, collection_id: uuid.UUID
    ) -> KnowledgeCollection:
        collection = self._locked_collections.get(collection_id)
        if collection is None or collection.organization_id != organization_id:
            raise RuntimeError("collection row must be locked before mutation")
        return collection
