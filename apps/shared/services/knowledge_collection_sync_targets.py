from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from apps.shared.db.models.knowledge import (
    Document,
    KnowledgeBase,
    KnowledgeCollectionItem,
    SourceType,
)
from apps.shared.domain.knowledge_collection_sync import (
    sync_target_membership_revision,
    sync_target_snapshot_revision,
)
from sqlalchemy import case, func
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class CollectionSyncTarget:
    collection_item_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    document_id: uuid.UUID
    item_rank: int
    item_created_at: datetime
    document_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class CollectionSyncTargetScan:
    targets: tuple[CollectionSyncTarget, ...]
    has_source_managed_child: bool = False
    has_api_document: bool = False
    has_multi_document_target: bool = False
    exceeds_limit: bool = False

    @property
    def blocking_reason(self) -> str | None:
        if (
            self.has_source_managed_child
            or self.has_api_document
            or self.has_multi_document_target
        ):
            return "sync.not_supported"
        if self.exceeds_limit:
            return "sync.target_limit_exceeded"
        if not self.targets:
            return "sync.no_eligible_targets"
        return None

    @property
    def is_supported(self) -> bool:
        return self.blocking_reason is None

    def snapshot_revision(self, collection_id: uuid.UUID) -> str:
        return sync_target_snapshot_revision(
            collection_id,
            [
                sync_target_membership_revision(
                    collection_id=collection_id,
                    collection_item_id=target.collection_item_id,
                    knowledge_base_id=target.knowledge_base_id,
                    document_id=target.document_id,
                    item_rank=target.item_rank,
                    item_created_at=target.item_created_at,
                )
                for target in self.targets
            ],
        )


def scan_collection_sync_targets(
    db: Session,
    organization_id: uuid.UUID,
    collection_id: uuid.UUID,
    *,
    limit: int,
) -> CollectionSyncTargetScan:
    """Return the one canonical child-source eligibility and target projection.

    A DB document is executable only while its legacy KB is already a
    document-level atom. A KB containing the DB document plus any additional
    document would make the KB-level active version pointer hide sibling
    content, so that shape is rejected before a job can be created or claimed.
    """

    if limit < 1:
        raise ValueError("sync target scan limit must be positive")

    base_filters = (
        KnowledgeCollectionItem.organization_id == organization_id,
        KnowledgeCollectionItem.collection_id == collection_id,
        KnowledgeBase.organization_id == organization_id,
        KnowledgeBase.lifecycle_state == "active",
        KnowledgeBase.sync_state != "source_deleted",
    )
    source_managed_query = (
        db.query(KnowledgeCollectionItem.id)
        .join(KnowledgeBase, KnowledgeBase.id == KnowledgeCollectionItem.knowledge_base_id)
        .filter(*base_filters, KnowledgeBase.source_identity_id.is_not(None))
    )
    api_query = (
        db.query(Document.id)
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .join(
            KnowledgeCollectionItem,
            KnowledgeCollectionItem.knowledge_base_id == KnowledgeBase.id,
        )
        .filter(
            *base_filters,
            KnowledgeBase.source_identity_id.is_(None),
            Document.source_type == SourceType.API,
        )
    )
    multi_document_target_query = (
        db.query(Document.knowledge_base_id)
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .join(
            KnowledgeCollectionItem,
            KnowledgeCollectionItem.knowledge_base_id == KnowledgeBase.id,
        )
        .filter(*base_filters, KnowledgeBase.source_identity_id.is_(None))
        .group_by(Document.knowledge_base_id)
        .having(
            func.count(Document.id) > 1,
            func.sum(
                case((Document.source_type == SourceType.DB, 1), else_=0)
            )
            > 0,
        )
    )
    (
        has_source_managed,
        has_api,
        has_multi_document_target,
    ) = db.query(
        source_managed_query.exists(),
        api_query.exists(),
        multi_document_target_query.exists(),
    ).one()
    rows = (
        db.query(
            KnowledgeCollectionItem.id,
            KnowledgeBase.id,
            Document.id,
            KnowledgeCollectionItem.rank,
            KnowledgeCollectionItem.created_at,
            Document.updated_at,
        )
        .join(KnowledgeBase, KnowledgeBase.id == KnowledgeCollectionItem.knowledge_base_id)
        .join(Document, Document.knowledge_base_id == KnowledgeBase.id)
        .filter(
            *base_filters,
            KnowledgeBase.source_identity_id.is_(None),
            Document.source_type == SourceType.DB,
        )
        .order_by(
            KnowledgeCollectionItem.rank.asc(),
            KnowledgeCollectionItem.created_at.asc(),
            KnowledgeBase.id.asc(),
            Document.id.asc(),
        )
        .limit(limit)
        .all()
    )
    targets = tuple(
        CollectionSyncTarget(
            collection_item_id=collection_item_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            item_rank=item_rank,
            item_created_at=item_created_at,
            document_updated_at=document_updated_at,
        )
        for (
            collection_item_id,
            knowledge_base_id,
            document_id,
            item_rank,
            item_created_at,
            document_updated_at,
        ) in rows
    )
    return CollectionSyncTargetScan(
        targets=targets,
        has_source_managed_child=has_source_managed,
        has_api_document=has_api,
        has_multi_document_target=has_multi_document_target,
        exceeds_limit=len(rows) >= limit,
    )
