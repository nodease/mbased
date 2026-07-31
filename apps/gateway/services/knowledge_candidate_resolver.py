import uuid
from collections import Counter
from collections.abc import Iterable

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

from apps.shared.db.models.knowledge import (
    Document,
    DocumentChunk,
    KnowledgeBase,
    KnowledgeCollection,
    KnowledgeCollectionItem,
)
from apps.shared.schemas.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateCollectionGroup,
    KnowledgeCandidateHierarchyResolution,
    KnowledgeCandidateResolution,
    KnowledgePermissionDecision,
)
from apps.shared.services.knowledge_resource_eligibility import (
    knowledge_collection_operational_predicates,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.knowledge_safe_text import (
    safe_label_from_text,
    safe_topics_from_texts,
    sanitize_kb_safe_metadata,
    sanitize_safe_text,
)


DEFAULT_MAX_COLLECTIONS = 20
DEFAULT_MAX_CANDIDATE_KBS = 5000
DEFAULT_DIRECT_KB_CANDIDATE_RESERVE = 20


def bucket_count(value: int) -> str:
    if value <= 0:
        return "0"
    if value == 1:
        return "1"
    if value <= 10:
        return "2-10"
    if value <= 100:
        return "11-100"
    return "100+"


class KnowledgeCandidateResolver:
    """Builds safe Knowledge candidates for Builder/deployment preflight.

    The resolver never exposes denied resource identifiers. It consumes
    KnowledgePermissionHelper decisions and returns only allowed candidates plus
    bucketed summary counts.
    """

    def __init__(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        permission_helper: KnowledgePermissionHelper | None = None,
        runtime_permission_helper: KnowledgePermissionHelper | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id
        self.permission_helper = permission_helper or KnowledgePermissionHelper(
            db,
            user_id=user_id,
            organization_id=organization_id,
        )
        self.runtime_permission_helper = runtime_permission_helper

    def resolve_explicit_kbs(
        self,
        knowledge_base_ids: Iterable[uuid.UUID],
        *,
        allow_unready_candidates: bool = False,
    ) -> KnowledgeCandidateResolution:
        # Explicit KB mode는 collection route 권한을 요구하지 않는다.
        # 다만 KB use/source ACL/final evidence gate는 helper를 통해 그대로 적용한다.
        requested_ids = list(dict.fromkeys(knowledge_base_ids))
        kbs_by_id = self._knowledge_bases_by_id(requested_ids)

        hidden_count = len(requested_ids) - len(kbs_by_id)
        unavailable_count = 0
        candidates: list[KnowledgeCandidate] = []
        allowed_pairs: list[tuple[KnowledgeBase, KnowledgePermissionDecision]] = []

        kb_decisions = self.permission_helper.bulk_evaluate_kb_use(kbs_by_id.values())
        for kb_id in requested_ids:
            kb = kbs_by_id.get(kb_id)
            if kb is None:
                continue
            if self._kb_candidate_exclusion_reason(
                kb,
                allow_unready_candidates=allow_unready_candidates,
            ):
                unavailable_count += 1
                continue
            decision = kb_decisions[kb.id]
            if decision.allowed:
                allowed_pairs.append((kb, decision))
            elif decision.external_reason_code == "resource.hidden":
                hidden_count += 1
            else:
                unavailable_count += 1
        runtime_decisions = self._bulk_runtime_kb_decisions(
            [kb for kb, _decision in allowed_pairs]
        )
        candidates = [
            self._kb_candidate(
                kb,
                decision,
                runtime_decision=runtime_decisions.get(kb.id),
            )
            for kb, decision in allowed_pairs
        ]

        return KnowledgeCandidateResolution(
            candidates=candidates,
            hidden_candidate_count_bucket=bucket_count(hidden_count),
            unavailable_candidate_count_bucket=bucket_count(unavailable_count),
            reason_code=None if candidates else "resource.hidden",
        )

    def resolve_explicit_collections(
        self,
        collection_ids: Iterable[uuid.UUID],
    ) -> list[KnowledgeCandidateCollectionGroup]:
        """Return only operational Collections with current route permission."""

        requested_ids = self._dedupe_ids(collection_ids)
        collections = self._collections(requested_ids, None)
        route_decisions = self.permission_helper.bulk_evaluate_collection_action(
            collections,
            "route",
        )
        return [
            KnowledgeCandidateCollectionGroup(
                collection_id=collection.id,
                safe_label=self._collection_safe_label(collection),
                safe_metadata={
                    "safe_topics": self._collection_safe_topics(collection),
                },
                candidates=[],
            )
            for collection in collections
            if route_decisions[collection.id].allowed
        ]

    def resolve_auto_collection_candidates(
        self,
        *,
        collection_ids: Iterable[uuid.UUID] | None = None,
        max_collections: int = DEFAULT_MAX_COLLECTIONS,
        max_candidate_kbs: int = DEFAULT_MAX_CANDIDATE_KBS,
        allow_unready_candidates: bool = False,
    ) -> KnowledgeCandidateResolution:
        # Auto collection mode는 route-allowed collection scope 안에서만 KB 후보를 만든다.
        # 권한 없는 collection/KB는 식별자를 노출하지 않고 bucketed count로만 요약한다.
        requested_collection_ids = (
            self._dedupe_ids(collection_ids) if collection_ids is not None else None
        )
        collections = self._collections(requested_collection_ids, None)
        route_decisions = self.permission_helper.bulk_evaluate_collection_action(
            collections,
            "route",
        )
        route_allowed_collection_ids = [
            collection.id
            for collection in collections
            if route_decisions[collection.id].allowed
        ]
        hidden_count = (
            len(requested_collection_ids or []) - len(collections)
            if requested_collection_ids is not None
            else 0
        )
        hidden_count += sum(
            1
            for collection in collections
            if route_decisions[collection.id].external_reason_code == "resource.hidden"
        )
        unavailable_count = sum(
            1
            for collection in collections
            if (
                not route_decisions[collection.id].allowed
                and route_decisions[collection.id].external_reason_code
                != "resource.hidden"
            )
        )

        route_allowed_collection_ids = route_allowed_collection_ids[:max_collections]

        items = self._collection_items(route_allowed_collection_ids, None)
        route_allowed_collection_id_set = set(route_allowed_collection_ids)
        collections_by_id = {
            collection.id: collection
            for collection in collections
            if collection.id in route_allowed_collection_id_set
        }
        kb_ids = self._dedupe_ids([item.knowledge_base_id for item in items])
        kbs_by_id = self._knowledge_bases_by_id(kb_ids)
        hidden_count += len(kb_ids) - len(kbs_by_id)

        candidates: list[KnowledgeCandidate] = []
        allowed_pairs: list[tuple[KnowledgeBase, KnowledgePermissionDecision]] = []
        kb_decisions = self.permission_helper.bulk_evaluate_kb_use(kbs_by_id.values())
        for kb_id in kb_ids:
            kb = kbs_by_id.get(kb_id)
            if kb is None:
                continue
            if self._kb_candidate_exclusion_reason(
                kb,
                allow_unready_candidates=allow_unready_candidates,
            ):
                unavailable_count += 1
                continue
            decision = kb_decisions[kb.id]
            if decision.allowed:
                allowed_pairs.append((kb, decision))
            elif decision.external_reason_code == "resource.hidden":
                hidden_count += 1
            else:
                unavailable_count += 1
        allowed_pairs = allowed_pairs[:max_candidate_kbs]
        collection_context_by_kb_id = self._collection_context_by_kb_id(
            items,
            collections_by_id,
            allowed_kb_ids={kb.id for kb, _decision in allowed_pairs},
        )
        if requested_collection_ids is None and not allowed_pairs:
            direct_allowed_pairs, direct_unavailable_count, direct_hidden_count = (
                self._direct_authorized_kb_pairs(
                    max_candidate_kbs,
                    allow_unready_candidates=allow_unready_candidates,
                )
            )
            allowed_pairs = direct_allowed_pairs
            unavailable_count += direct_unavailable_count
            hidden_count += direct_hidden_count
        runtime_decisions = self._bulk_runtime_kb_decisions(
            [kb for kb, _decision in allowed_pairs]
        )
        candidates = [
            self._kb_candidate(
                kb,
                decision,
                runtime_decision=runtime_decisions.get(kb.id),
                extra_safe_metadata=collection_context_by_kb_id.get(kb.id),
            )
            for kb, decision in allowed_pairs
        ]

        return KnowledgeCandidateResolution(
            candidates=candidates,
            hidden_candidate_count_bucket=bucket_count(hidden_count),
            unavailable_candidate_count_bucket=bucket_count(unavailable_count),
            reason_code=None if candidates else "resource.hidden",
        )

    def resolve_builder_hierarchy(
        self,
        *,
        collection_ids: Iterable[uuid.UUID] | None = None,
        max_collections: int = DEFAULT_MAX_COLLECTIONS,
        max_candidate_kbs: int = DEFAULT_MAX_CANDIDATE_KBS,
        allow_unready_candidates: bool = False,
        apply_collection_limit: bool = True,
        apply_candidate_limit: bool = True,
        enforce_internal_candidate_limit: bool = True,
    ) -> KnowledgeCandidateHierarchyResolution:
        """Return route-authorized Collections with independently authorized KBs."""

        requested_ids = (
            self._dedupe_ids(collection_ids) if collection_ids is not None else None
        )
        collections = self._collections(requested_ids, None)
        route_decisions = self.permission_helper.bulk_evaluate_collection_action(
            collections,
            "route",
        )
        visible_collections = [
            collection
            for collection in collections
            if route_decisions[collection.id].allowed
        ]
        if apply_collection_limit:
            visible_collections = visible_collections[:max_collections]
        visible_collection_ids = [collection.id for collection in visible_collections]
        internal_candidate_limit = (
            max_candidate_kbs if enforce_internal_candidate_limit else None
        )
        direct_candidate_limit = None
        if internal_candidate_limit is not None:
            if visible_collection_ids and internal_candidate_limit > 0:
                direct_candidate_limit = min(
                    DEFAULT_DIRECT_KB_CANDIDATE_RESERVE,
                    max(1, internal_candidate_limit // 2),
                )
            else:
                direct_candidate_limit = internal_candidate_limit

        direct_pairs: list[tuple[KnowledgeBase, KnowledgePermissionDecision]] = []
        direct_unavailable = 0
        direct_hidden = 0
        if direct_candidate_limit is None or direct_candidate_limit > 0:
            direct_pairs, direct_unavailable, direct_hidden = (
                self._direct_authorized_kb_pairs(
                    direct_candidate_limit,
                    max_evaluated_kbs=internal_candidate_limit,
                    allow_unready_candidates=allow_unready_candidates,
                    excluded_collection_ids=set(visible_collection_ids),
                )
            )
        direct_evaluated_count = (
            len(direct_pairs) + direct_unavailable + direct_hidden
        )
        linked_candidate_limit = (
            max(0, internal_candidate_limit - direct_evaluated_count)
            if internal_candidate_limit is not None
            else None
        )
        items = self._collection_items(
            visible_collection_ids,
            linked_candidate_limit,
        )

        candidates_by_id: dict[uuid.UUID, KnowledgeCandidate] = {}
        item_kb_ids = self._dedupe_ids(item.knowledge_base_id for item in items)
        unavailable_count = direct_unavailable
        hidden_count = direct_hidden
        if item_kb_ids:
            linked_kbs = self._knowledge_bases_by_id(item_kb_ids)
            linked_decisions = self.permission_helper.bulk_evaluate_kb_use(
                linked_kbs.values()
            )
            linked_runtime = self._bulk_runtime_kb_decisions(list(linked_kbs.values()))
            for kb_id in item_kb_ids:
                kb = linked_kbs.get(kb_id)
                if kb is None:
                    hidden_count += 1
                    continue
                if self._kb_candidate_exclusion_reason(
                    kb,
                    allow_unready_candidates=allow_unready_candidates,
                ):
                    unavailable_count += 1
                    continue
                decision = linked_decisions[kb.id]
                if not decision.allowed:
                    if decision.external_reason_code == "resource.hidden":
                        hidden_count += 1
                    else:
                        unavailable_count += 1
                    continue
                candidates_by_id[kb.id] = self._kb_candidate(
                    kb,
                    decision,
                    runtime_decision=linked_runtime.get(kb.id),
                )

        runtime_decisions = self._bulk_runtime_kb_decisions(
            [kb for kb, _decision in direct_pairs]
        )
        for kb, decision in direct_pairs:
            candidates_by_id[kb.id] = self._kb_candidate(
                kb,
                decision,
                runtime_decision=runtime_decisions.get(kb.id),
            )

        item_ids_by_collection: dict[uuid.UUID, list[uuid.UUID]] = {}
        for item in items:
            values = item_ids_by_collection.setdefault(item.collection_id, [])
            if item.knowledge_base_id not in values:
                values.append(item.knowledge_base_id)

        grouped_kb_ids: set[uuid.UUID] = set()
        groups: list[KnowledgeCandidateCollectionGroup] = []
        for collection in visible_collections:
            child_candidates = [
                candidates_by_id[kb_id]
                for kb_id in item_ids_by_collection.get(collection.id, [])
                if kb_id in candidates_by_id
            ]
            grouped_kb_ids.update(item.candidate_id for item in child_candidates)
            groups.append(
                KnowledgeCandidateCollectionGroup(
                    collection_id=collection.id,
                    safe_label=self._collection_safe_label(collection),
                    safe_metadata={
                        "safe_topics": self._collection_safe_topics(collection),
                    },
                    candidates=child_candidates,
                )
            )

        if requested_ids is not None:
            found_ids = {collection.id for collection in collections}
            hidden_count += len(set(requested_ids) - found_ids)
        hidden_count += sum(
            1
            for collection in collections
            if not route_decisions[collection.id].allowed
            and route_decisions[collection.id].external_reason_code == "resource.hidden"
        )
        unavailable_count += sum(
            1
            for collection in collections
            if not route_decisions[collection.id].allowed
            and route_decisions[collection.id].external_reason_code != "resource.hidden"
        )
        ungrouped = [
            candidate
            for candidate_id, candidate in candidates_by_id.items()
            if candidate_id not in grouped_kb_ids
        ]
        if apply_candidate_limit:
            ungrouped = ungrouped[:max_candidate_kbs]
        return KnowledgeCandidateHierarchyResolution(
            collections=groups,
            ungrouped_candidates=ungrouped,
            hidden_candidate_count_bucket=bucket_count(hidden_count),
            unavailable_candidate_count_bucket=bucket_count(unavailable_count),
            reason_code=None if groups or ungrouped else "resource.hidden",
        )

    def _collections(
        self,
        collection_ids: Iterable[uuid.UUID] | None,
        max_collections: int | None,
    ) -> list[KnowledgeCollection]:
        query = self.db.query(KnowledgeCollection).filter(
            KnowledgeCollection.organization_id == self.organization_id,
            *knowledge_collection_operational_predicates(),
        )
        if collection_ids is not None:
            requested_ids = self._dedupe_ids(collection_ids)
            if not requested_ids:
                return []
            query = query.filter(KnowledgeCollection.id.in_(requested_ids))
            rows = query.all()
            rows_by_id = {row.id: row for row in rows}
            ordered = [
                rows_by_id[collection_id]
                for collection_id in requested_ids
                if collection_id in rows_by_id
            ]
            return ordered if max_collections is None else ordered[:max_collections]
        query = query.order_by(
            KnowledgeCollection.name.asc(),
            KnowledgeCollection.id.asc(),
        )
        if max_collections is not None:
            query = query.limit(max_collections)
        return query.all()

    def _collection_items(
        self,
        collection_ids: Iterable[uuid.UUID],
        max_candidate_kbs: int | None,
    ) -> list[KnowledgeCollectionItem]:
        collection_id_list = self._dedupe_ids(collection_ids)
        if not collection_id_list:
            return []
        collection_position = case(
            {
                collection_id: position
                for position, collection_id in enumerate(collection_id_list)
            },
            value=KnowledgeCollectionItem.collection_id,
            else_=len(collection_id_list),
        )
        selected_kb_ids = None
        if max_candidate_kbs is not None:
            ranked_items = (
                self.db.query(
                    KnowledgeCollectionItem.knowledge_base_id.label(
                        "knowledge_base_id"
                    ),
                    collection_position.label("collection_position"),
                    KnowledgeCollectionItem.rank.label("item_rank"),
                    func.row_number()
                    .over(
                        partition_by=KnowledgeCollectionItem.knowledge_base_id,
                        order_by=(
                            collection_position.asc(),
                            KnowledgeCollectionItem.rank.asc(),
                            KnowledgeCollectionItem.knowledge_base_id.asc(),
                        ),
                    )
                    .label("kb_occurrence"),
                )
                .filter(
                    KnowledgeCollectionItem.organization_id
                    == self.organization_id,
                    KnowledgeCollectionItem.collection_id.in_(collection_id_list),
                )
                .subquery()
            )
            selected_kb_ids = (
                select(
                    ranked_items.c.knowledge_base_id,
                    ranked_items.c.collection_position,
                    ranked_items.c.item_rank,
                )
                .where(ranked_items.c.kb_occurrence == 1)
                .order_by(
                    ranked_items.c.collection_position.asc(),
                    ranked_items.c.item_rank.asc(),
                    ranked_items.c.knowledge_base_id.asc(),
                )
                .limit(max_candidate_kbs)
                .subquery()
            )
        query = (
            self.db.query(KnowledgeCollectionItem)
            .filter(
                KnowledgeCollectionItem.organization_id == self.organization_id,
                KnowledgeCollectionItem.collection_id.in_(collection_id_list),
            )
            .order_by(
                collection_position.asc(),
                KnowledgeCollectionItem.rank.asc(),
                KnowledgeCollectionItem.knowledge_base_id.asc(),
            )
        )
        if selected_kb_ids is not None:
            query = query.filter(
                KnowledgeCollectionItem.knowledge_base_id.in_(
                    select(selected_kb_ids.c.knowledge_base_id)
                )
            )
        return query.all()

    def _knowledge_bases_by_id(
        self,
        knowledge_base_ids: Iterable[uuid.UUID],
    ) -> dict[uuid.UUID, KnowledgeBase]:
        ids = self._dedupe_ids(knowledge_base_ids)
        if not ids:
            return {}
        rows = (
            self.db.query(KnowledgeBase)
            .options(
                joinedload(KnowledgeBase.source_identity),
                joinedload(KnowledgeBase.active_document_version),
            )
            .filter(
                KnowledgeBase.id.in_(ids),
                KnowledgeBase.organization_id == self.organization_id,
                KnowledgeBase.lifecycle_state == "active",
            )
            .all()
        )
        return {row.id: row for row in rows}

    def _direct_knowledge_bases(
        self,
        max_candidate_kbs: int | None,
        *,
        offset: int = 0,
        excluded_kb_ids: set[uuid.UUID] | None = None,
        excluded_collection_ids: set[uuid.UUID] | None = None,
    ) -> list[KnowledgeBase]:
        if self.db is None:
            return []
        query = (
            self.db.query(KnowledgeBase)
            .options(
                joinedload(KnowledgeBase.source_identity),
                joinedload(KnowledgeBase.active_document_version),
            )
            .filter(
                KnowledgeBase.organization_id == self.organization_id,
                KnowledgeBase.lifecycle_state == "active",
            )
            .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.asc())
        )
        if excluded_kb_ids:
            query = query.filter(KnowledgeBase.id.notin_(excluded_kb_ids))
        if excluded_collection_ids:
            linked_membership = (
                select(KnowledgeCollectionItem.id)
                .where(
                    KnowledgeCollectionItem.organization_id
                    == self.organization_id,
                    KnowledgeCollectionItem.collection_id.in_(
                        excluded_collection_ids
                    ),
                    KnowledgeCollectionItem.knowledge_base_id == KnowledgeBase.id,
                )
                .exists()
            )
            query = query.filter(~linked_membership)
        if offset > 0:
            query = query.offset(offset)
        if max_candidate_kbs is not None:
            query = query.limit(max_candidate_kbs)
        return query.all()

    def _direct_authorized_kb_pairs(
        self,
        max_candidate_kbs: int | None,
        *,
        max_evaluated_kbs: int | None = None,
        allow_unready_candidates: bool = False,
        excluded_kb_ids: set[uuid.UUID] | None = None,
        excluded_collection_ids: set[uuid.UUID] | None = None,
    ) -> tuple[list[tuple[KnowledgeBase, KnowledgePermissionDecision]], int, int]:
        evaluation_limit = (
            max_candidate_kbs
            if max_evaluated_kbs is None
            else max_evaluated_kbs
        )
        if max_candidate_kbs is not None and max_candidate_kbs <= 0:
            return [], 0, 0
        if evaluation_limit is not None and evaluation_limit <= 0:
            return [], 0, 0

        unavailable_count = 0
        hidden_count = 0
        allowed_pairs: list[tuple[KnowledgeBase, KnowledgePermissionDecision]] = []
        evaluated_count = 0
        offset = 0
        while True:
            remaining_results = (
                None
                if max_candidate_kbs is None
                else max_candidate_kbs - len(allowed_pairs)
            )
            remaining_evaluations = (
                None
                if evaluation_limit is None
                else evaluation_limit - evaluated_count
            )
            if remaining_results is not None and remaining_results <= 0:
                break
            if remaining_evaluations is not None and remaining_evaluations <= 0:
                break

            page_limit = remaining_results
            if page_limit is None:
                page_limit = remaining_evaluations
            elif remaining_evaluations is not None:
                page_limit = min(page_limit, remaining_evaluations)

            kbs = self._direct_knowledge_bases(
                page_limit,
                offset=offset,
                excluded_kb_ids=excluded_kb_ids,
                excluded_collection_ids=excluded_collection_ids,
            )
            if not kbs:
                break
            offset += len(kbs)
            evaluated_count += len(kbs)

            kb_decisions = self.permission_helper.bulk_evaluate_kb_use(kbs)
            permission_allowed_pairs: list[
                tuple[KnowledgeBase, KnowledgePermissionDecision]
            ] = []
            for kb in kbs:
                decision = kb_decisions[kb.id]
                if decision.allowed:
                    permission_allowed_pairs.append((kb, decision))
                elif decision.external_reason_code == "resource.hidden":
                    hidden_count += 1
                else:
                    unavailable_count += 1

            legacy_lookup_ids = [
                kb.id
                for kb, _decision in permission_allowed_pairs
                if not allow_unready_candidates
                and str(getattr(kb, "sync_state", "") or "").lower()
                != "source_deleted"
                and (
                    getattr(kb, "active_document_version", None) is None
                    or getattr(kb.active_document_version, "status", None) != "ready"
                )
            ]
            legacy_visible_kb_ids = self._legacy_retrieval_visible_kb_ids(
                legacy_lookup_ids
            )
            for kb, decision in permission_allowed_pairs:
                if self._kb_candidate_exclusion_reason(
                    kb,
                    allow_unready_candidates=allow_unready_candidates,
                    legacy_retrieval_visible_kb_ids=legacy_visible_kb_ids,
                ):
                    unavailable_count += 1
                    continue
                allowed_pairs.append((kb, decision))

            if page_limit is None or len(kbs) < page_limit:
                break
        return allowed_pairs, unavailable_count, hidden_count

    def _kb_candidate(
        self,
        kb: KnowledgeBase,
        permission: KnowledgePermissionDecision,
        *,
        runtime_decision: KnowledgePermissionDecision | None = None,
        extra_safe_metadata: dict | None = None,
    ) -> KnowledgeCandidate:
        runtime_availability = "unknown"
        runtime_reason_code = None
        if self.runtime_permission_helper is not None or runtime_decision is not None:
            runtime_decision = runtime_decision or (
                self.runtime_permission_helper.evaluate_kb_use(kb)
            )
            if runtime_decision.allowed:
                runtime_availability = "available"
            else:
                runtime_availability = "unavailable"
                runtime_reason_code = runtime_decision.external_reason_code

        safe_metadata = dict(permission.safe_metadata)
        safe_metadata.update(self._active_version_safe_metadata(kb))
        safe_metadata.update(self._kb_safe_metadata(kb))
        if extra_safe_metadata:
            safe_metadata.update(extra_safe_metadata)
        if runtime_reason_code:
            safe_metadata["runtime_reason_code"] = runtime_reason_code

        return KnowledgeCandidate(
            candidate_id=kb.id,
            candidate_type="knowledge_base",
            permission=permission,
            runtime_availability=runtime_availability,
            safe_label=self._kb_safe_label(kb),
            safe_metadata=safe_metadata,
        )

    def _bulk_runtime_kb_decisions(
        self,
        kbs: list[KnowledgeBase],
    ) -> dict[uuid.UUID, KnowledgePermissionDecision]:
        if self.runtime_permission_helper is None or not kbs:
            return {}
        return self.runtime_permission_helper.bulk_evaluate_kb_use(kbs)

    def _kb_safe_label(self, kb: KnowledgeBase) -> str | None:
        source_identity = getattr(kb, "source_identity", None)
        if source_identity is not None:
            if getattr(source_identity, "display_policy_state", None) == "approved":
                return safe_label_from_text(
                    getattr(source_identity, "safe_display_name", None)
                )
            return None
        safe_metadata = sanitize_kb_safe_metadata(getattr(kb, "safe_metadata", None))
        safe_label = safe_metadata.get("safe_label")
        if isinstance(safe_label, str) and safe_label:
            return safe_label
        return safe_label_from_text(getattr(kb, "name", None))

    def _kb_safe_metadata(self, kb: KnowledgeBase) -> dict:
        source_identity = getattr(kb, "source_identity", None)
        if source_identity is not None:
            if getattr(source_identity, "display_policy_state", None) != "approved":
                return {}
            return self._source_identity_safe_metadata(source_identity)

        metadata: dict[str, object] = {}
        stored_metadata = sanitize_kb_safe_metadata(getattr(kb, "safe_metadata", None))
        safe_description = stored_metadata.get("kb_safe_description")
        if not isinstance(safe_description, str) or not safe_description:
            safe_description = sanitize_safe_text(getattr(kb, "description", None))
        if safe_description:
            metadata["kb_safe_description"] = safe_description
        topics = stored_metadata.get("kb_safe_topics")
        if not isinstance(topics, list) or not topics:
            topics = safe_topics_from_texts(
                (
                    getattr(kb, "name", None),
                    getattr(kb, "description", None),
                )
            )
        if topics:
            metadata["kb_safe_topics"] = topics
        return metadata

    def _source_identity_safe_metadata(self, source_identity) -> dict:
        metadata: dict[str, object] = {}
        safe_description = sanitize_safe_text(
            getattr(source_identity, "safe_display_description", None)
        )
        if safe_description:
            metadata["kb_safe_description"] = safe_description
        source_metadata = getattr(source_identity, "safe_metadata", None) or {}
        raw_topics = (
            source_metadata.get("kb_safe_topics")
            or source_metadata.get("safe_topics")
            or source_metadata.get("topics")
        )
        if isinstance(raw_topics, (list, tuple, set)):
            topics = []
            for value in raw_topics:
                safe_topic = sanitize_safe_text(value)
                if safe_topic and safe_topic not in topics:
                    topics.append(safe_topic)
                if len(topics) >= 10:
                    break
            if topics:
                metadata["kb_safe_topics"] = topics
        return metadata

    def _active_version_safe_metadata(self, kb: KnowledgeBase) -> dict:
        # Source tier는 권한이 통과된 KB의 active ready version에서만 safe ranking hint로 전달한다.
        # Adapter가 DocumentVersion을 직접 조회하지 않게 하여 permission/candidate 경계를 유지한다.
        version = getattr(kb, "active_document_version", None)
        if version is None or getattr(version, "status", None) != "ready":
            return {"active_document_version_status": "missing"}
        source_tier = getattr(version, "source_tier", None)
        metadata = {"active_document_version_status": "ready"}
        if not isinstance(source_tier, str) or not source_tier.strip():
            return metadata
        return {**metadata, "source_tier": source_tier.strip()}

    def _kb_candidate_exclusion_reason(
        self,
        kb: KnowledgeBase,
        *,
        allow_unready_candidates: bool = False,
        legacy_retrieval_visible_kb_ids: set[uuid.UUID] | None = None,
    ) -> str | None:
        sync_state = str(getattr(kb, "sync_state", "") or "").lower()
        if sync_state == "source_deleted":
            return "source_deleted"
        version = getattr(kb, "active_document_version", None)
        if version is None or getattr(version, "status", None) != "ready":
            if allow_unready_candidates:
                return None
            if legacy_retrieval_visible_kb_ids is not None:
                if kb.id in legacy_retrieval_visible_kb_ids:
                    return None
            elif self._has_legacy_retrieval_visible_chunks(kb):
                return None
            return "no_active_ready_version"
        return None

    def _legacy_retrieval_visible_kb_ids(
        self,
        knowledge_base_ids: Iterable[uuid.UUID],
    ) -> set[uuid.UUID]:
        requested_ids = list(dict.fromkeys(knowledge_base_ids))
        if self.db is None or not requested_ids:
            return set()
        rows = (
            self.db.query(DocumentChunk.knowledge_base_id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .filter(
                DocumentChunk.knowledge_base_id.in_(requested_ids),
                DocumentChunk.document_version_id.is_(None),
                Document.status == "completed",
            )
            .distinct()
            .all()
        )
        return {row[0] for row in rows}

    def _has_legacy_retrieval_visible_chunks(self, kb: KnowledgeBase) -> bool:
        if self.db is None:
            return False
        return (
            self.db.query(DocumentChunk.id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .filter(
                DocumentChunk.knowledge_base_id == kb.id,
                DocumentChunk.document_version_id.is_(None),
                Document.status == "completed",
            )
            .limit(1)
            .first()
            is not None
        )

    def _collection_context_by_kb_id(
        self,
        items: list[KnowledgeCollectionItem],
        collections_by_id: dict[uuid.UUID, KnowledgeCollection],
        *,
        allowed_kb_ids: set[uuid.UUID],
    ) -> dict[uuid.UUID, dict]:
        allowed_items = [
            item for item in items if item.knowledge_base_id in allowed_kb_ids
        ]
        linked_count_by_collection_id = Counter(
            item.collection_id for item in allowed_items
        )
        context_by_kb_id: dict[uuid.UUID, dict] = {}
        for item in allowed_items:
            if item.knowledge_base_id in context_by_kb_id:
                continue
            collection = collections_by_id.get(item.collection_id)
            if collection is None:
                continue
            metadata = {
                "collection_id": str(collection.id),
                "route_scope_type": "auto_collection",
                "linked_kb_count_bucket": bucket_count(
                    linked_count_by_collection_id[collection.id]
                ),
            }
            safe_label = self._collection_safe_label(collection)
            if safe_label:
                metadata["collection_safe_label"] = safe_label
            safe_topics = self._collection_safe_topics(collection)
            if safe_topics:
                metadata["collection_safe_topics"] = safe_topics
            context_by_kb_id[item.knowledge_base_id] = metadata
        return context_by_kb_id

    def _collection_safe_label(self, collection: KnowledgeCollection) -> str | None:
        # Collection 이름은 source-derived metadata일 수 있으므로 raw name을 fallback으로 쓰지 않는다.
        # 승인된 safe_metadata label만 Builder recommendation summary에 전달한다.
        metadata = getattr(collection, "safe_metadata", None) or {}
        for key in ("collection_safe_label", "safe_label", "safe_display_name"):
            value = safe_label_from_text(metadata.get(key))
            if value:
                return value
        return None

    def _collection_safe_topics(self, collection: KnowledgeCollection) -> list[str]:
        metadata = getattr(collection, "safe_metadata", None) or {}
        raw_topics = metadata.get("topics") or metadata.get("safe_topics")
        if not isinstance(raw_topics, (list, tuple, set)):
            return []
        topics = []
        for value in raw_topics:
            if isinstance(value, str) and value.strip():
                topics.append(value.strip())
        return topics[:10]

    def _dedupe_ids(self, values: Iterable[uuid.UUID]) -> list[uuid.UUID]:
        result: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result
