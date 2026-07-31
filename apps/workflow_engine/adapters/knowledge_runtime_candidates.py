"""PostgreSQL projection for runtime Knowledge candidate facts."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, and_, column, func, select, true, values
from sqlalchemy.orm import Session, load_only

from apps.shared.db.models.knowledge import (
    Document,
    DocumentChunk,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
)
from apps.shared.db.models.organization import Organization
from apps.shared.domain.knowledge_runtime_candidates import (
    AnonymousPublicAudience,
    AuthenticatedAudience,
    KnowledgeCollectionCandidateStream,
    KnowledgeRuntimeCandidateRequest,
    KnowledgeRuntimeCandidateSnapshot,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.knowledge_resource_eligibility import (
    is_anonymous_public_knowledge_collection,
    is_operational_knowledge_resource,
    knowledge_base_operational_predicates,
    knowledge_collection_anonymous_public_predicates,
    knowledge_collection_operational_predicates,
)


SessionFactory = Callable[[], Session]
MembershipFact = tuple[UUID, UUID]


class KnowledgeRuntimeCandidateSnapshotError(RuntimeError):
    """Fixed-code adapter failure that never carries a raw database detail."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class PostgresKnowledgeRuntimeCandidateSnapshotAdapter:
    """Loads one authorized/readiness projection from a fresh DB snapshot."""

    def __init__(self, *, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def load_snapshot(
        self,
        request: KnowledgeRuntimeCandidateRequest,
    ) -> KnowledgeRuntimeCandidateSnapshot:
        if not isinstance(request, KnowledgeRuntimeCandidateRequest):
            raise KnowledgeRuntimeCandidateSnapshotError("snapshot_request_invalid")

        try:
            db = self._session_factory()
        except Exception:
            raise KnowledgeRuntimeCandidateSnapshotError(
                "snapshot_session_unavailable"
            ) from None

        snapshot: KnowledgeRuntimeCandidateSnapshot | None = None
        failure_code: str | None = None
        try:
            if db.in_transaction():
                raise KnowledgeRuntimeCandidateSnapshotError(
                    "snapshot_session_not_fresh"
                )
            bind = db.get_bind()
            if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
                raise KnowledgeRuntimeCandidateSnapshotError(
                    "snapshot_database_unsupported"
                )
            try:
                db.connection(
                    execution_options={
                        "isolation_level": "REPEATABLE READ",
                        "postgresql_readonly": True,
                    }
                )
            except Exception:
                raise KnowledgeRuntimeCandidateSnapshotError(
                    "snapshot_transaction_setup_failed"
                ) from None

            with db.no_autoflush:
                snapshot = self._load_snapshot_in_transaction(db, request)
        except KnowledgeRuntimeCandidateSnapshotError as exc:
            failure_code = exc.reason_code
        except Exception:
            failure_code = "snapshot_read_failed"
        finally:
            cleanup_failed = False
            try:
                db.rollback()
            except Exception:
                cleanup_failed = True
            try:
                db.close()
            except Exception:
                cleanup_failed = True
            if cleanup_failed and failure_code is None:
                failure_code = "snapshot_cleanup_failed"

        if failure_code is not None:
            raise KnowledgeRuntimeCandidateSnapshotError(failure_code) from None
        if snapshot is None:
            raise KnowledgeRuntimeCandidateSnapshotError("snapshot_read_failed")
        return snapshot

    def _load_snapshot_in_transaction(
        self,
        db: Session,
        request: KnowledgeRuntimeCandidateRequest,
    ) -> KnowledgeRuntimeCandidateSnapshot:
        if not self._organization_is_active(db, request.organization_id):
            return KnowledgeRuntimeCandidateSnapshot(
                policy_excluded_count=(
                    len(request.direct_kb_ids) + len(request.collection_ids)
                )
            )

        loaded_collections = self._load_selected_collections(
            db,
            request.organization_id,
            request.collection_ids,
        )
        in_scope_collections = {
            collection_id: collection
            for collection_id, collection in loaded_collections.items()
            if self._collection_is_in_scope(
                collection,
                organization_id=request.organization_id,
                expected_id=collection_id,
            )
        }
        configured_collections = [
            in_scope_collections[collection_id]
            for collection_id in request.collection_ids
            if collection_id in in_scope_collections
        ]

        permission_helper: KnowledgePermissionHelper | None = None
        if isinstance(request.audience, AuthenticatedAudience):
            evaluation_time = self._load_policy_evaluation_time(db)
            permission_helper = self._build_permission_helper(
                db,
                request.audience,
                evaluation_time=evaluation_time,
            )
            route_decisions = (
                permission_helper.bulk_evaluate_collection_action(
                    configured_collections,
                    "route",
                )
                if configured_collections
                else {}
            )
            allowed_collection_ids = tuple(
                collection.id
                for collection in configured_collections
                if bool(getattr(route_decisions.get(collection.id), "allowed", False))
            )
        elif isinstance(request.audience, AnonymousPublicAudience):
            allowed_collection_ids = tuple(
                collection.id
                for collection in configured_collections
                if is_anonymous_public_knowledge_collection(collection)
            )
        else:
            raise KnowledgeRuntimeCandidateSnapshotError("snapshot_audience_invalid")

        membership_facts: tuple[MembershipFact, ...] = ()
        scan_limited = False
        malformed_membership_count = 0
        if allowed_collection_ids:
            raw_memberships, scan_limited = self._load_fair_collection_memberships(
                db,
                request.organization_id,
                allowed_collection_ids,
                request.candidate_scan_cap,
            )
            membership_facts, malformed_membership_count = (
                self._normalize_membership_facts(
                    raw_memberships,
                    allowed_collection_ids=set(allowed_collection_ids),
                )
            )

        candidate_kb_ids = self._deduplicate_ids(
            (*request.direct_kb_ids, *(kb_id for _collection_id, kb_id in membership_facts))
        )
        loaded_kbs = self._load_candidate_kbs(
            db,
            request.organization_id,
            candidate_kb_ids,
        )
        in_scope_kbs = {
            knowledge_base_id: kb
            for knowledge_base_id, kb in loaded_kbs.items()
            if self._kb_is_in_scope(
                kb,
                organization_id=request.organization_id,
                expected_id=knowledge_base_id,
            )
        }
        in_scope_kb_ids = tuple(
            knowledge_base_id
            for knowledge_base_id in candidate_kb_ids
            if knowledge_base_id in in_scope_kbs
        )
        ready_kb_ids = self._load_ready_kb_ids(
            db,
            request.organization_id,
            in_scope_kb_ids,
        )
        normalized_ready_kb_ids = set(in_scope_kb_ids) & set(ready_kb_ids)
        unresolved_kb_ids = tuple(
            knowledge_base_id
            for knowledge_base_id in in_scope_kb_ids
            if knowledge_base_id not in normalized_ready_kb_ids
        )
        if unresolved_kb_ids:
            normalized_ready_kb_ids.update(
                set(unresolved_kb_ids)
                & set(self._load_legacy_ready_kb_ids(db, unresolved_kb_ids))
            )

        if isinstance(request.audience, AuthenticatedAudience):
            assert permission_helper is not None
            eligible_kb_ids = self._authenticated_eligible_kb_ids(
                permission_helper,
                in_scope_kbs,
                in_scope_kb_ids,
                normalized_ready_kb_ids,
            )
        else:
            eligible_kb_ids = self._anonymous_eligible_kb_ids(
                db,
                request,
                in_scope_kbs,
                normalized_ready_kb_ids,
            )

        eligible_direct_kb_ids = tuple(
            knowledge_base_id
            for knowledge_base_id in request.direct_kb_ids
            if knowledge_base_id in eligible_kb_ids
        )
        collection_streams = tuple(
            KnowledgeCollectionCandidateStream(
                collection_id=collection_id,
                eligible_kb_ids=tuple(
                    knowledge_base_id
                    for fact_collection_id, knowledge_base_id in membership_facts
                    if fact_collection_id == collection_id
                    and knowledge_base_id in eligible_kb_ids
                ),
            )
            for collection_id in allowed_collection_ids
        )
        policy_excluded_count = (
            len(request.collection_ids)
            - len(allowed_collection_ids)
            + sum(
                1
                for knowledge_base_id in request.direct_kb_ids
                if knowledge_base_id not in eligible_kb_ids
            )
            + sum(
                1
                for _collection_id, knowledge_base_id in membership_facts
                if knowledge_base_id not in eligible_kb_ids
            )
            + malformed_membership_count
        )
        return KnowledgeRuntimeCandidateSnapshot(
            eligible_direct_kb_ids=eligible_direct_kb_ids,
            collection_streams=collection_streams,
            policy_excluded_count=policy_excluded_count,
            scan_limited=bool(scan_limited),
        )

    def _authenticated_eligible_kb_ids(
        self,
        permission_helper: KnowledgePermissionHelper,
        kbs_by_id: dict[UUID, KnowledgeBase],
        ordered_kb_ids: tuple[UUID, ...],
        ready_kb_ids: set[UUID],
    ) -> set[UUID]:
        ready_kbs = [
            kbs_by_id[knowledge_base_id]
            for knowledge_base_id in ordered_kb_ids
            if knowledge_base_id in ready_kb_ids
        ]
        if not ready_kbs:
            return set()
        decisions = permission_helper.bulk_evaluate_kb_use(ready_kbs)
        return {
            kb.id
            for kb in ready_kbs
            if bool(getattr(decisions.get(kb.id), "allowed", False))
        }

    def _anonymous_eligible_kb_ids(
        self,
        db: Session,
        request: KnowledgeRuntimeCandidateRequest,
        kbs_by_id: dict[UUID, KnowledgeBase],
        ready_kb_ids: set[UUID],
    ) -> set[UUID]:
        manual_ready_kb_ids = {
            knowledge_base_id
            for knowledge_base_id, kb in kbs_by_id.items()
            if knowledge_base_id in ready_kb_ids
            and getattr(kb, "source_identity_id", None) is None
        }
        direct_manual_ready_ids = tuple(
            knowledge_base_id
            for knowledge_base_id in request.direct_kb_ids
            if knowledge_base_id in manual_ready_kb_ids
        )
        public_direct_ids = (
            self._load_public_direct_kb_ids(
                db,
                request.organization_id,
                direct_manual_ready_ids,
            )
            if direct_manual_ready_ids
            else set()
        )
        return manual_ready_kb_ids - set(request.direct_kb_ids) | (
            manual_ready_kb_ids & set(public_direct_ids)
        )

    def _organization_is_active(self, db: Session, organization_id: UUID) -> bool:
        return (
            db.execute(
                select(Organization.id).where(
                    Organization.id == organization_id,
                    Organization.is_active.is_(True),
                )
            ).scalar_one_or_none()
            is not None
        )

    def _load_selected_collections(
        self,
        db: Session,
        organization_id: UUID,
        collection_ids: Iterable[UUID],
    ) -> dict[UUID, KnowledgeCollection]:
        bounded_ids = self._deduplicate_ids(collection_ids)
        if not bounded_ids:
            return {}
        rows = (
            db.execute(
                select(KnowledgeCollection)
                .options(
                    load_only(
                        KnowledgeCollection.id,
                        KnowledgeCollection.organization_id,
                        KnowledgeCollection.lifecycle_state,
                        KnowledgeCollection.sync_state,
                        KnowledgeCollection.source_identity_id,
                        KnowledgeCollection.is_system_managed,
                        KnowledgeCollection.safe_metadata,
                    )
                )
                .where(
                    KnowledgeCollection.id.in_(bounded_ids),
                    KnowledgeCollection.organization_id == organization_id,
                    *knowledge_collection_operational_predicates(),
                )
            )
            .scalars()
            .all()
        )
        return {row.id: row for row in rows}

    def _load_fair_collection_memberships(
        self,
        db: Session,
        organization_id: UUID,
        collection_ids: Iterable[UUID],
        scan_cap: int,
    ) -> tuple[tuple[MembershipFact, ...], bool]:
        bounded_collection_ids = self._deduplicate_ids(collection_ids)
        if not bounded_collection_ids:
            return (), False

        configured_collections = values(
            column(
                "collection_id",
                KnowledgeCollectionItem.collection_id.type,
            ),
            column("configured_order", Integer()),
            name="selected_collections",
        ).data(
            [
                (collection_id, configured_order)
                for configured_order, collection_id in enumerate(
                    bounded_collection_ids
                )
            ]
        )
        bounded_collection_items = (
            select(
                KnowledgeCollectionItem.collection_id.label("collection_id"),
                KnowledgeCollectionItem.knowledge_base_id.label(
                    "knowledge_base_id"
                ),
                KnowledgeCollectionItem.rank.label("item_rank"),
                KnowledgeCollectionItem.created_at.label("item_created_at"),
            )
            .where(
                KnowledgeCollectionItem.organization_id == organization_id,
                KnowledgeCollectionItem.collection_id
                == configured_collections.c.collection_id,
            )
            .order_by(
                KnowledgeCollectionItem.rank.asc(),
                KnowledgeCollectionItem.created_at.asc(),
                KnowledgeCollectionItem.knowledge_base_id.asc(),
            )
            .limit(scan_cap + 1)
            .lateral("bounded_collection_items")
        )
        bounded_memberships = (
            select(
                bounded_collection_items.c.collection_id,
                bounded_collection_items.c.knowledge_base_id,
                bounded_collection_items.c.item_rank,
                bounded_collection_items.c.item_created_at,
                configured_collections.c.configured_order,
            )
            .select_from(configured_collections)
            .join(bounded_collection_items, true())
            .cte("bounded_memberships")
        )
        collection_position = func.row_number().over(
            partition_by=bounded_memberships.c.collection_id,
            order_by=(
                bounded_memberships.c.item_rank.asc(),
                bounded_memberships.c.item_created_at.asc(),
                bounded_memberships.c.knowledge_base_id.asc(),
            ),
        ).label("collection_position")
        ranked_items = (
            select(
                bounded_memberships.c.collection_id,
                bounded_memberships.c.knowledge_base_id,
                bounded_memberships.c.configured_order,
                collection_position,
            )
            .select_from(bounded_memberships)
            .cte("ranked_memberships")
        )
        rows = db.execute(
            select(
                ranked_items.c.collection_id,
                ranked_items.c.knowledge_base_id,
            )
            .order_by(
                ranked_items.c.collection_position.asc(),
                ranked_items.c.configured_order.asc(),
                ranked_items.c.knowledge_base_id.asc(),
            )
            .limit(scan_cap + 1)
        ).all()
        scan_limited = len(rows) > scan_cap
        return (
            tuple(
                (row.collection_id, row.knowledge_base_id)
                for row in rows[:scan_cap]
            ),
            scan_limited,
        )

    def _load_candidate_kbs(
        self,
        db: Session,
        organization_id: UUID,
        knowledge_base_ids: Iterable[UUID],
    ) -> dict[UUID, KnowledgeBase]:
        bounded_ids = self._deduplicate_ids(knowledge_base_ids)
        if not bounded_ids:
            return {}
        rows = (
            db.execute(
                select(KnowledgeBase)
                .options(
                    load_only(
                        KnowledgeBase.id,
                        KnowledgeBase.organization_id,
                        KnowledgeBase.active_document_version_id,
                        KnowledgeBase.source_identity_id,
                        KnowledgeBase.sync_state,
                        KnowledgeBase.lifecycle_state,
                    )
                )
                .where(
                    KnowledgeBase.id.in_(bounded_ids),
                    KnowledgeBase.organization_id == organization_id,
                    *knowledge_base_operational_predicates(),
                )
            )
            .scalars()
            .all()
        )
        return {row.id: row for row in rows}

    def _load_ready_kb_ids(
        self,
        db: Session,
        organization_id: UUID,
        knowledge_base_ids: Iterable[UUID],
    ) -> set[UUID]:
        bounded_ids = self._deduplicate_ids(knowledge_base_ids)
        if not bounded_ids:
            return set()
        return set(
            db.execute(
                select(KnowledgeBase.id)
                .join(
                    DocumentVersion,
                    and_(
                        DocumentVersion.id
                        == KnowledgeBase.active_document_version_id,
                        DocumentVersion.knowledge_base_id == KnowledgeBase.id,
                        DocumentVersion.organization_id
                        == KnowledgeBase.organization_id,
                    ),
                )
                .where(
                    KnowledgeBase.id.in_(bounded_ids),
                    KnowledgeBase.organization_id == organization_id,
                    DocumentVersion.status == "ready",
                )
            )
            .scalars()
            .all()
        )

    def _load_policy_evaluation_time(self, db: Session) -> datetime:
        evaluation_time = db.execute(
            select(func.transaction_timestamp())
        ).scalar_one()
        if (
            not isinstance(evaluation_time, datetime)
            or evaluation_time.tzinfo is None
            or evaluation_time.utcoffset() is None
        ):
            raise KnowledgeRuntimeCandidateSnapshotError(
                "snapshot_evaluation_time_invalid"
            )
        return evaluation_time.astimezone(timezone.utc)

    def _load_legacy_ready_kb_ids(
        self,
        db: Session,
        knowledge_base_ids: Iterable[UUID],
    ) -> set[UUID]:
        bounded_ids = self._deduplicate_ids(knowledge_base_ids)
        if not bounded_ids:
            return set()
        return set(
            db.execute(
                select(DocumentChunk.knowledge_base_id)
                .join(
                    Document,
                    and_(
                        Document.id == DocumentChunk.document_id,
                        Document.knowledge_base_id
                        == DocumentChunk.knowledge_base_id,
                    ),
                )
                .join(
                    KnowledgeBase,
                    KnowledgeBase.id == DocumentChunk.knowledge_base_id,
                )
                .where(
                    DocumentChunk.knowledge_base_id.in_(bounded_ids),
                    KnowledgeBase.active_document_version_id.is_(None),
                    DocumentChunk.document_version_id.is_(None),
                    Document.status == "completed",
                )
                .distinct()
            )
            .scalars()
            .all()
        )

    def _load_public_direct_kb_ids(
        self,
        db: Session,
        organization_id: UUID,
        knowledge_base_ids: Iterable[UUID],
    ) -> set[UUID]:
        bounded_ids = self._deduplicate_ids(knowledge_base_ids)
        if not bounded_ids:
            return set()
        return set(
            db.execute(
                select(KnowledgeCollectionItem.knowledge_base_id)
                .join(
                    KnowledgeCollection,
                    and_(
                        KnowledgeCollection.id
                        == KnowledgeCollectionItem.collection_id,
                        KnowledgeCollection.organization_id
                        == KnowledgeCollectionItem.organization_id,
                    ),
                )
                .where(
                    KnowledgeCollectionItem.organization_id == organization_id,
                    KnowledgeCollectionItem.knowledge_base_id.in_(bounded_ids),
                    KnowledgeCollection.organization_id == organization_id,
                    *knowledge_collection_anonymous_public_predicates(),
                )
                .distinct()
            )
            .scalars()
            .all()
        )

    def _build_permission_helper(
        self,
        db: Session,
        audience: AuthenticatedAudience,
        *,
        evaluation_time: datetime,
    ) -> KnowledgePermissionHelper:
        return KnowledgePermissionHelper(
            db,
            user_id=audience.user_id,
            organization_id=audience.organization_id,
            evaluation_time=evaluation_time,
        )

    @staticmethod
    def _collection_is_in_scope(
        collection: Any,
        *,
        organization_id: UUID,
        expected_id: UUID,
    ) -> bool:
        return (
            collection is not None
            and getattr(collection, "id", None) == expected_id
            and getattr(collection, "organization_id", None) == organization_id
            and is_operational_knowledge_resource(collection)
        )

    @staticmethod
    def _kb_is_in_scope(
        kb: Any,
        *,
        organization_id: UUID,
        expected_id: UUID,
    ) -> bool:
        return (
            kb is not None
            and getattr(kb, "id", None) == expected_id
            and getattr(kb, "organization_id", None) == organization_id
            and is_operational_knowledge_resource(kb)
        )

    @staticmethod
    def _normalize_membership_facts(
        facts: Iterable[MembershipFact],
        *,
        allowed_collection_ids: set[UUID],
    ) -> tuple[tuple[MembershipFact, ...], int]:
        normalized: list[MembershipFact] = []
        malformed_count = 0
        for fact in facts:
            if (
                not isinstance(fact, tuple)
                or len(fact) != 2
                or not isinstance(fact[0], UUID)
                or not isinstance(fact[1], UUID)
                or fact[0] not in allowed_collection_ids
            ):
                malformed_count += 1
                continue
            normalized.append(fact)
        return tuple(normalized), malformed_count

    @staticmethod
    def _deduplicate_ids(values: Iterable[UUID]) -> tuple[UUID, ...]:
        return tuple(dict.fromkeys(values))


__all__ = [
    "KnowledgeRuntimeCandidateSnapshotError",
    "PostgresKnowledgeRuntimeCandidateSnapshotAdapter",
]
