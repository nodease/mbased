import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from apps.gateway.services.knowledge_candidate_resolver import (
    DEFAULT_MAX_CANDIDATE_KBS,
    KnowledgeCandidateResolver,
    bucket_count,
)
from apps.shared.schemas.knowledge import (
    KnowledgeBaseOptionRef,
    KnowledgeCandidate,
    KnowledgeCandidateHierarchyResolution,
    KnowledgeCandidateResolution,
    KnowledgeSelection,
    KnowledgeSelectionCollection,
    KnowledgeSelectionKBCandidate,
    KnowledgeRAGRecommendedOptions,
    KnowledgeRAGRecommendation,
    KnowledgeRAGRecommendationProvenance,
    KnowledgeRAGRecommendationRequest,
    KnowledgeRAGRecommendationResponse,
    KnowledgeRAGRecommendationSummary,
    KnowledgeSourceCollectionSummary,
)
from apps.shared.services.knowledge_permission_service import KnowledgePermissionHelper
from apps.shared.services.knowledge_safe_text import extract_safe_terms
from apps.shared.services.rag_source_tier import source_tier_priority


RECOMMENDATION_STRATEGY = "structured_kb_relevance_v2"
RECOMMENDATION_HANDLE_NAMESPACE = "metadata_keyword_v1"
GENERIC_KB_LABEL = "Knowledge Base"
SAFE_TEMPLATE_FOR_POLICY = "{query} 관련 정책 근거 절차 기준"
MAX_MATERIALIZED_KBS = 20
_KB_KEYWORD_STRING_METADATA_KEYS = (
    "kb_safe_description",
    "safe_description",
    "safe_display_description",
)
_KB_KEYWORD_LIST_METADATA_KEYS = (
    "kb_safe_topics",
    "safe_topics",
)

_AVAILABILITY_ORDER = {
    "available": 3,
    "warning": 2,
    "unknown": 1,
    "unavailable": 0,
}
_FRESH_SYNC_STATES = {"synced", "fresh", "ready"}


def knowledge_base_recommendation_handle(
    organization_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
) -> str:
    stable_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        (
            f"{RECOMMENDATION_HANDLE_NAMESPACE}:"
            f"{organization_id}:"
            f"{knowledge_base_id}"
        ),
    )
    return f"rec-{stable_id}"


def knowledge_collection_selection_handle(
    organization_id: uuid.UUID,
    collection_id: uuid.UUID,
) -> str:
    stable_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"knowledge-collection-selection:{organization_id}:{collection_id}",
    )
    return f"col-{stable_id}"
_SAFE_QUERY_STOP_TERMS = frozenset(
    {
        "kb",
        "knowledge",
        "base",
        "rag",
        "llm",
        "workflow",
        "webhook",
        "node",
        "input",
        "output",
        "create",
        "build",
        "make",
        "generate",
        "지식베이스",
        "워크플로우",
        "웹훅",
        "챗봇",
        "노드",
        "입력",
        "출력",
        "생성",
        "만들어줘",
        "만들어",
        "받는",
        "받아",
        "답변",
        "검색",
        "찾아",
        "요약",
        "분석",
    }
)
_WHITESPACE_RE = re.compile(r"\s+")


class KnowledgeRAGRecommendationService:
    """Workflow Builder용 RAG option recommendation service.

    권한 판단은 반드시 KnowledgeCandidateResolver/PermissionHelper 경계에서 끝낸다.
    이 service는 safe candidate metadata만 받아 ranking과 LLM node option
    materialization을 수행한다.
    """

    def __init__(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        resolver: KnowledgeCandidateResolver | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id
        self.resolver = resolver

    def recommend_for_builder(
        self,
        request: KnowledgeRAGRecommendationRequest,
        *,
        include_materialized_refs: bool = False,
        allow_unready_candidates: bool = False,
    ) -> KnowledgeRAGRecommendationResponse:
        resolver = self.resolver or self._resolver_for_request(request)
        recommendation_mode = self._resolved_mode(request)
        hierarchy: KnowledgeCandidateHierarchyResolution | None = None
        try:
            if recommendation_mode == "auto_collection" and hasattr(
                resolver, "resolve_builder_hierarchy"
            ):
                hierarchy = resolver.resolve_builder_hierarchy(
                    collection_ids=self._collection_scope(request),
                    max_collections=request.max_collections,
                    max_candidate_kbs=min(
                        request.max_candidate_kbs,
                        DEFAULT_MAX_CANDIDATE_KBS,
                    ),
                    allow_unready_candidates=allow_unready_candidates,
                    apply_collection_limit=False,
                    apply_candidate_limit=False,
                )
                unique_candidates: dict[uuid.UUID, KnowledgeCandidate] = {}
                for group in hierarchy.collections:
                    for candidate in group.candidates:
                        unique_candidates.setdefault(candidate.candidate_id, candidate)
                for candidate in hierarchy.ungrouped_candidates:
                    unique_candidates.setdefault(candidate.candidate_id, candidate)
                resolution = KnowledgeCandidateResolution(
                    candidates=list(unique_candidates.values()),
                    # Hierarchical builder responses must not disclose how many
                    # child KBs were hidden by permission filtering.
                    hidden_candidate_count_bucket="0",
                    unavailable_candidate_count_bucket="0",
                    reason_code=hierarchy.reason_code,
                )
            else:
                resolution = self._resolve_candidates(
                    resolver,
                    request,
                    recommendation_mode,
                    allow_unready_candidates=allow_unready_candidates,
                )
        except Exception:
            return self._adapter_unavailable_response(request)
        try:
            ranked = self._rank_candidates(resolution.candidates, request)
        except Exception:
            return self._adapter_unavailable_response(request, resolution.candidates)

        limited = ranked[: request.max_recommendations]
        recommendations = [
            self._recommendation(candidate, score, matched_terms, used_signals, request)
            for candidate, score, matched_terms, used_signals in limited
        ]
        if not include_materialized_refs:
            for item in recommendations:
                item.materialized_knowledge_bases = []
        clarification_options = self._clarification_options_from_recommendations(
            recommendations
        )
        knowledge_selection = (
            self._knowledge_selection(hierarchy, ranked, request)
            if hierarchy is not None
            else None
        )
        has_selectable_hierarchy = bool(
            knowledge_selection
            and (
                knowledge_selection.collections
                or knowledge_selection.ungrouped_kbs
            )
        )

        warning_count = sum(1 for item in recommendations if item.warnings)
        response = KnowledgeRAGRecommendationResponse(
            status=(
                "recommended"
                if recommendations or has_selectable_hierarchy
                else "no_candidate"
            ),
            resolution_id=request.pending_resolution_ref,
            requirement_id=(request.knowledge_requirement or {}).get("requirement_id")
            if request.knowledge_requirement
            else None,
            recommendations=recommendations,
            clarification_options=clarification_options,
            knowledge_selection=knowledge_selection,
            fallback_reason=(
                None
                if recommendations or has_selectable_hierarchy
                else "no_candidate"
            ),
            summary=KnowledgeRAGRecommendationSummary(
                candidate_count_bucket=bucket_count(len(resolution.candidates)),
                recommendation_count_bucket=bucket_count(len(recommendations)),
                hidden_or_unavailable_count_bucket=self._merge_buckets(
                    resolution.hidden_candidate_count_bucket,
                    resolution.unavailable_candidate_count_bucket,
                ),
                recommendation_strategy=RECOMMENDATION_STRATEGY,
                warning_count_bucket=bucket_count(warning_count),
            ),
            reason_code=(
                None
                if recommendations or has_selectable_hierarchy
                else resolution.reason_code or "no_candidate"
            ),
        )
        issued_kb_handles = {
            self._recommendation_id(candidate): candidate.candidate_id
            for candidate, _score, _matched_terms, _used_signals in limited
        }
        if knowledge_selection is not None and hierarchy is not None:
            visible_kb_handles = {
                child.kb_handle
                for collection in knowledge_selection.collections
                for child in collection.children
            } | {item.kb_handle for item in knowledge_selection.ungrouped_kbs}
            for group in hierarchy.collections:
                for candidate in group.candidates:
                    handle = self._recommendation_id(candidate)
                    if handle in visible_kb_handles:
                        issued_kb_handles[handle] = candidate.candidate_id
            for candidate in hierarchy.ungrouped_candidates:
                handle = self._recommendation_id(candidate)
                if handle in visible_kb_handles:
                    issued_kb_handles[handle] = candidate.candidate_id
            visible_collection_handles = {
                item.collection_handle for item in knowledge_selection.collections
            }
            response._issued_collection_resource_ids = {
                self._collection_handle(group.collection_id): group.collection_id
                for group in hierarchy.collections
                if self._collection_handle(group.collection_id)
                in visible_collection_handles
            }
        response._issued_kb_resource_ids = issued_kb_handles
        return response

    def safe_intent_candidates_for_builder(
        self,
        workflow_intent: str,
        *,
        max_candidates: int = 20,
    ) -> list[dict[str, Any]]:
        """Return a bounded, permission-filtered KB projection for intent planning."""
        limit = max(1, min(int(max_candidates), 20))
        request = KnowledgeRAGRecommendationRequest(
            workflow_intent=workflow_intent,
            node_purpose=workflow_intent,
            intended_execution_subject_id=self.user_id,
            mode="auto",
            max_recommendations=limit,
        )
        resolver = self.resolver or self._resolver_for_request(request)
        try:
            resolution = self._resolve_candidates(
                resolver,
                request,
                "auto_collection",
                allow_unready_candidates=True,
            )
            ranked = [
                item
                for item in self._rank_candidates(resolution.candidates, request)
                if item[1] > 0
            ][:limit]
        except Exception:
            return []

        result: list[dict[str, Any]] = []
        for candidate, score, _matched_terms, _used_signals in ranked:
            metadata = candidate.safe_metadata or {}
            raw_topics = metadata.get("kb_safe_topics")
            safe_topics = []
            if isinstance(raw_topics, (list, tuple, set)):
                for value in raw_topics:
                    if not isinstance(value, str):
                        continue
                    topic = value.strip()[:128]
                    if topic and topic not in safe_topics:
                        safe_topics.append(topic)
                    if len(safe_topics) >= 10:
                        break
            safe_description = metadata.get("kb_safe_description")
            result.append(
                {
                    "candidate_handle": self._recommendation_id(candidate),
                    "safe_label": (
                        candidate.safe_label.strip()[:255]
                        if isinstance(candidate.safe_label, str)
                        and candidate.safe_label.strip()
                        else None
                    ),
                    "safe_topics": safe_topics,
                    "safe_description": (
                        safe_description.strip()[:500]
                        if isinstance(safe_description, str)
                        and safe_description.strip()
                        else None
                    ),
                    "runtime_availability": candidate.runtime_availability,
                    "relevance_score": round(max(0.0, min(score, 0.99)), 4),
                }
            )
        return result

    def materialize_candidate_handles_for_builder(
        self,
        request: KnowledgeRAGRecommendationRequest,
        candidate_handles: set[str],
        *,
        issued_resource_ids: dict[str, uuid.UUID],
    ) -> list[dict[str, str]]:
        """Resolve previously issued safe handles to authorized runtime KB refs.

        This path is for apply/save materialization. It does not depend on
        top-N ranking; a still-authorized handle can be materialized even if
        current recommendation ordering changed.
        """
        if not candidate_handles:
            return []
        resolver = self.resolver or self._resolver_for_request(request)
        bound_ids = {
            handle: resource_id
            for handle, resource_id in issued_resource_ids.items()
            if handle in candidate_handles
            and self._recommendation_handle_for_id(resource_id) == handle
        }
        if set(bound_ids) != candidate_handles:
            return []
        try:
            resolution = resolver.resolve_explicit_kbs(
                bound_ids.values(),
                allow_unready_candidates=True,
            )
        except Exception:
            return []
        candidates_by_id = {
            candidate.candidate_id: candidate
            for candidate in resolution.candidates
        }
        return [
            {
                "safe_handle": handle,
                "knowledge_base_id": str(resource_id),
                "name": candidates_by_id[resource_id].safe_label
                or GENERIC_KB_LABEL,
            }
            for handle, resource_id in sorted(bound_ids.items())
            if resource_id in candidates_by_id
        ]

    def materialize_legacy_candidate_handles_for_builder(
        self,
        request: KnowledgeRAGRecommendationRequest,
        candidate_handles: set[str],
    ) -> list[dict[str, str]]:
        """Bounded compatibility lookup for superseded Preview drafts."""

        if not candidate_handles:
            return []
        resolver = self.resolver or self._resolver_for_request(request)
        try:
            hierarchy = resolver.resolve_builder_hierarchy(
                collection_ids=self._collection_scope(request),
                max_collections=request.max_collections,
                max_candidate_kbs=DEFAULT_MAX_CANDIDATE_KBS,
                allow_unready_candidates=True,
                apply_collection_limit=False,
                apply_candidate_limit=False,
                enforce_internal_candidate_limit=True,
            )
        except Exception:
            return []
        candidates_by_id = {
            candidate.candidate_id: candidate
            for group in hierarchy.collections
            for candidate in group.candidates
        }
        candidates_by_id.update(
            {
                candidate.candidate_id: candidate
                for candidate in hierarchy.ungrouped_candidates
            }
        )
        return [
            {
                "safe_handle": handle,
                "knowledge_base_id": str(candidate.candidate_id),
                "name": candidate.safe_label or GENERIC_KB_LABEL,
            }
            for candidate in candidates_by_id.values()
            if (handle := self._recommendation_id(candidate)) in candidate_handles
        ]

    def materialize_collection_handles_for_builder(
        self,
        request: KnowledgeRAGRecommendationRequest,
        collection_handles: set[str],
        *,
        issued_resource_ids: dict[str, uuid.UUID],
    ) -> list[dict[str, str]]:
        """Revalidate opaque Collection handles without exposing child identities."""

        if not collection_handles:
            return []
        resolver = self.resolver or self._resolver_for_request(request)
        bound_ids = {
            handle: resource_id
            for handle, resource_id in issued_resource_ids.items()
            if handle in collection_handles
            and self._collection_handle(resource_id) == handle
        }
        if set(bound_ids) != collection_handles:
            return []
        try:
            groups = resolver.resolve_explicit_collections(bound_ids.values())
        except Exception:
            return []
        groups_by_id = {group.collection_id: group for group in groups}
        return [
            {
                "safe_handle": handle,
                "knowledge_collection_id": str(resource_id),
                "name": groups_by_id[resource_id].safe_label
                or "Knowledge Collection",
            }
            for handle, resource_id in sorted(bound_ids.items())
            if resource_id in groups_by_id
        ]

    def _adapter_unavailable_response(
        self,
        request: KnowledgeRAGRecommendationRequest,
        candidates: list[KnowledgeCandidate] | None = None,
    ) -> KnowledgeRAGRecommendationResponse:
        safe_options = [
            {
                "candidate_id": self._recommendation_id(candidate),
                "safe_label": candidate.safe_label or GENERIC_KB_LABEL,
                "candidate_type": candidate.candidate_type,
                "runtime_availability": candidate.runtime_availability,
                "confidence": "low",
                "score": 0.0,
                "reason_category": "adapter_unavailable",
                "threshold_result": "adapter_unavailable",
            }
            for candidate in (candidates or [])[: request.max_recommendations]
        ]
        return KnowledgeRAGRecommendationResponse(
            status="clarification_required" if safe_options else "unavailable",
            resolution_id=request.pending_resolution_ref,
            requirement_id=(request.knowledge_requirement or {}).get("requirement_id")
            if request.knowledge_requirement
            else None,
            recommendations=[],
            clarification_options=safe_options,
            fallback_reason="adapter_unavailable",
            reason_code="adapter_unavailable",
            user_safe_warning="Knowledge Base 추천을 사용할 수 없어 사용자 확인이 필요합니다.",
        )

    def _resolver_for_request(
        self,
        request: KnowledgeRAGRecommendationRequest,
    ) -> KnowledgeCandidateResolver:
        runtime_permission_helper = None
        if request.intended_execution_subject_id:
            runtime_permission_helper = KnowledgePermissionHelper(
                self.db,
                user_id=request.intended_execution_subject_id,
                organization_id=self.organization_id,
            )
        return KnowledgeCandidateResolver(
            self.db,
            user_id=self.user_id,
            organization_id=self.organization_id,
            runtime_permission_helper=runtime_permission_helper,
        )

    def _resolve_candidates(
        self,
        resolver: KnowledgeCandidateResolver,
        request: KnowledgeRAGRecommendationRequest,
        recommendation_mode: str,
        *,
        allow_unready_candidates: bool = False,
    ):
        if recommendation_mode == "explicit_kb":
            return resolver.resolve_explicit_kbs(
                request.knowledge_base_ids,
                allow_unready_candidates=allow_unready_candidates,
            )
        return resolver.resolve_auto_collection_candidates(
            collection_ids=self._collection_scope(request),
            max_collections=request.max_collections,
            max_candidate_kbs=min(request.max_candidate_kbs, DEFAULT_MAX_CANDIDATE_KBS),
            allow_unready_candidates=allow_unready_candidates,
        )

    def _collection_scope(
        self,
        request: KnowledgeRAGRecommendationRequest,
    ) -> list[uuid.UUID] | None:
        # collection_ids를 생략한 경우만 "route 가능한 전체 scope"로 해석한다.
        # 명시적으로 []를 보낸 경우는 사용자가 scope를 비운 것이므로 후보 없음으로 유지한다.
        if "collection_ids" in request.model_fields_set:
            return request.collection_ids
        return None

    def _resolved_mode(self, request: KnowledgeRAGRecommendationRequest) -> str:
        if request.mode in {"explicit_kb", "auto_collection"}:
            return request.mode
        if request.knowledge_base_ids:
            return "explicit_kb"
        return "auto_collection"

    def _rank_candidates(
        self,
        candidates: list[KnowledgeCandidate],
        request: KnowledgeRAGRecommendationRequest,
    ) -> list[tuple[KnowledgeCandidate, float, list[str], list[str]]]:
        terms, query_source = self._ranking_terms(candidates, request)
        ranked: list[tuple[KnowledgeCandidate, float, list[str], list[str]]] = []
        for candidate in candidates:
            matched_terms = self._matched_terms(candidate, terms)
            source_priority = self._source_tier_priority(candidate)
            availability = _AVAILABILITY_ORDER.get(candidate.runtime_availability, 1)
            freshness = self._sync_freshness_score(candidate)
            kb_relevance = self._kb_relevance(candidate, terms, matched_terms)
            score = 0.0
            if kb_relevance > 0:
                score = (
                    kb_relevance * 0.70
                    + (source_priority / 100) * 0.10
                    + (availability / 3) * 0.10
                    + freshness * 0.10
                )
            used_signals = self._used_signals(
                matched_terms=matched_terms,
                source_priority=source_priority,
                availability=candidate.runtime_availability,
                freshness=freshness,
                structured_query=query_source == "structured",
                fallback_query=query_source == "fallback",
            )
            ranked.append(
                (
                    candidate,
                    max(0.0, min(score, 0.99)),
                    matched_terms,
                    used_signals,
                )
            )

        return sorted(
            ranked,
            key=lambda item: (
                -item[1],
                0 if item[0].safe_label else 1,
                item[0].safe_label.casefold() if item[0].safe_label else "",
                self._recommendation_id(item[0]),
            ),
        )

    def _recommendation(
        self,
        candidate: KnowledgeCandidate,
        score: float,
        matched_terms: list[str],
        used_signals: list[str],
        request: KnowledgeRAGRecommendationRequest,
    ) -> KnowledgeRAGRecommendation:
        safe_label = candidate.safe_label
        kb_label = safe_label or GENERIC_KB_LABEL
        options = self._recommended_options(request)
        warnings = self._warnings(candidate, safe_label)
        materialized_refs = [
            KnowledgeBaseOptionRef(
                id=candidate.candidate_id,
                name=kb_label,
            )
        ][:MAX_MATERIALIZED_KBS]
        safe_reason_code = self._safe_reason_code(
            matched_terms,
            request,
            structured_query="structured_safe_query" in used_signals,
        )
        recommendation_id = self._recommendation_id(candidate)
        threshold_result = (
            "high_confidence" if score >= 0.65 else "close_score" if score >= 0.45 else "below_threshold"
        )
        confidence_label = (
            "high" if score >= 0.65 else "medium" if score >= 0.45 else "low"
        )
        return KnowledgeRAGRecommendation(
            recommendation_id=recommendation_id,
            recommendation_mode=self._resolved_mode(request),
            candidate_id=recommendation_id,
            candidate_handle=recommendation_id,
            safe_label=safe_label,
            confidence=confidence_label,
            confidence_label=confidence_label,
            score=round(score, 4),
            reason_category=safe_reason_code,
            threshold_result=threshold_result,
            safe_reason_code=safe_reason_code,
            recommended_options=options,
            materialized_knowledge_bases=materialized_refs,
            source_collection_summary=self._source_collection_summary(candidate),
            provenance=KnowledgeRAGRecommendationProvenance(
                recommendation_strategy=RECOMMENDATION_STRATEGY,
                safe_reason_code=safe_reason_code,
                used_signals=used_signals,
                matched_safe_terms=matched_terms[:10],
                candidate_count_bucket="1",
                warning_count_bucket=bucket_count(len(warnings)),
            ),
            runtime_availability=candidate.runtime_availability,
            warnings=warnings,
        )

    def _recommendation_id(self, candidate: KnowledgeCandidate) -> str:
        # Agent Builder-facing IDs must be opaque safe handles; the runtime KB
        # UUID remains available only in materialized_knowledge_bases.
        # Keep the handle namespace stable so existing preview drafts can still
        # materialize after ranking strategy changes.
        return knowledge_base_recommendation_handle(
            self.organization_id,
            candidate.candidate_id,
        )

    def _recommendation_handle_for_id(self, knowledge_base_id: uuid.UUID) -> str:
        return knowledge_base_recommendation_handle(
            self.organization_id,
            knowledge_base_id,
        )

    def _collection_handle(self, collection_id: uuid.UUID) -> str:
        return knowledge_collection_selection_handle(
            self.organization_id,
            collection_id,
        )

    def _selection_key(self, candidate: KnowledgeCandidate) -> str:
        stable_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"knowledge-kb-selection:{self.organization_id}:{candidate.candidate_id}",
        )
        return f"kbsel-{stable_id}"

    def _knowledge_selection(
        self,
        hierarchy: KnowledgeCandidateHierarchyResolution,
        ranked: list[tuple[KnowledgeCandidate, float, list[str], list[str]]],
        request: KnowledgeRAGRecommendationRequest,
    ) -> KnowledgeSelection:
        ranked_by_id = {item[0].candidate_id: item for item in ranked}
        visible_ids = {
            candidate.candidate_id
            for candidate, _score, _matched, _signals in ranked[
                : request.max_recommendations
            ]
        }
        shared_count: dict[uuid.UUID, int] = {}
        for group in hierarchy.collections:
            for candidate_id in {item.candidate_id for item in group.candidates}:
                shared_count[candidate_id] = shared_count.get(candidate_id, 0) + 1

        def project(candidate: KnowledgeCandidate) -> KnowledgeSelectionKBCandidate:
            score = ranked_by_id.get(candidate.candidate_id, (candidate, 0.0, [], []))[1]
            return KnowledgeSelectionKBCandidate(
                kb_handle=self._recommendation_id(candidate),
                selection_key=self._selection_key(candidate),
                safe_label=candidate.safe_label,
                score=round(score, 4),
                shared_collection_count=shared_count.get(candidate.candidate_id, 0),
            )

        query_terms, _source = self._ranking_terms(
            [item[0] for item in ranked],
            request,
        )
        collections: list[KnowledgeSelectionCollection] = []
        for group in hierarchy.collections:
            unique_children: dict[uuid.UUID, KnowledgeCandidate] = {}
            for candidate in group.candidates:
                unique_children.setdefault(candidate.candidate_id, candidate)
            children = [
                project(candidate)
                for candidate in unique_children.values()
                if candidate.candidate_id in visible_ids
            ]
            children.sort(
                key=lambda item: (
                    -item.score,
                    0 if item.safe_label else 1,
                    item.safe_label.casefold() if item.safe_label else "",
                    item.kb_handle,
                )
            )
            child_scores = sorted(
                (
                    ranked_by_id.get(
                        candidate.candidate_id,
                        (candidate, 0.0, [], []),
                    )[1]
                    for candidate in unique_children.values()
                ),
                reverse=True,
            )
            top_score = child_scores[0] if child_scores else 0.0
            top_three = child_scores[:3]
            top_three_average = (
                sum(top_three) / len(top_three) if top_three else 0.0
            )
            metadata_terms = set(
                extract_safe_terms(
                    group.safe_label,
                    *((group.safe_metadata or {}).get("safe_topics") or []),
                )
            )
            collection_metadata_score = (
                len(metadata_terms.intersection(query_terms)) / len(set(query_terms))
                if query_terms
                else 0.0
            )
            collection_score = min(
                1.0,
                top_score * 0.60
                + top_three_average * 0.30
                + collection_metadata_score * 0.10,
            )
            collections.append(
                KnowledgeSelectionCollection(
                    collection_handle=self._collection_handle(group.collection_id),
                    safe_label=group.safe_label,
                    score=round(collection_score, 4),
                    children=children,
                )
            )
        collections.sort(
            key=lambda item: (
                -item.score,
                0 if item.safe_label else 1,
                item.safe_label.casefold() if item.safe_label else "",
                item.collection_handle,
            )
        )
        collections = collections[: request.max_collections]
        ungrouped = [
            project(candidate)
            for candidate in hierarchy.ungrouped_candidates
            if candidate.candidate_id in visible_ids
        ]
        ungrouped.sort(
            key=lambda item: (
                -item.score,
                0 if item.safe_label else 1,
                item.safe_label.casefold() if item.safe_label else "",
                item.kb_handle,
            )
        )
        return KnowledgeSelection(
            collections=collections,
            ungrouped_kbs=ungrouped,
        )

    def _clarification_options_from_recommendations(
        self,
        recommendations: list[KnowledgeRAGRecommendation],
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "knowledge_base",
                "candidate_id": item.candidate_handle or item.recommendation_id,
                "label": item.safe_label or GENERIC_KB_LABEL,
                "safe_label": item.safe_label,
                "confidence": item.confidence,
                "score": item.score,
                "reason_category": item.reason_category or item.safe_reason_code,
                "threshold_result": item.threshold_result,
                "runtime_availability": item.runtime_availability,
            }
            for item in recommendations
        ]

    def _recommended_options(
        self,
        request: KnowledgeRAGRecommendationRequest,
    ) -> KnowledgeRAGRecommendedOptions:
        high_risk = request.high_risk_domain != "none"
        rewrite_mode = "template" if high_risk and request.allow_query_rewrite else "off"
        return KnowledgeRAGRecommendedOptions(
            queryRewriteMode=rewrite_mode,
            queryRewriteTemplate=SAFE_TEMPLATE_FOR_POLICY
            if rewrite_mode == "template"
            else None,
            evidenceSufficiencyPolicy="strict_citation"
            if high_risk
            else "minimum_evidence",
            ragFailurePolicy="safe_no_result",
            sourceTierPolicy="tie_break",
            scoreThreshold=0.3,
            topK=5,
        )

    def _warnings(
        self,
        candidate: KnowledgeCandidate,
        safe_label: str | None,
    ) -> list[str]:
        warnings: list[str] = []
        if candidate.runtime_availability == "unknown":
            warnings.append("runtime_availability_unknown")
        elif candidate.runtime_availability != "available":
            warnings.append("runtime_availability_not_available")
        sync_state = str((candidate.safe_metadata or {}).get("sync_state") or "").lower()
        if sync_state in {"stale", "failed"}:
            warnings.append(f"kb_sync_state_{sync_state}")
        if safe_label is None:
            warnings.append("safe_label_unavailable")
        return warnings

    def _safe_reason_code(
        self,
        matched_terms: list[str],
        request: KnowledgeRAGRecommendationRequest,
        *,
        structured_query: bool,
    ) -> str:
        if request.high_risk_domain != "none":
            return "high_risk_domain_requires_citation"
        if matched_terms:
            if structured_query:
                return "structured_intent_matches_safe_metadata"
            return "intent_matches_safe_metadata"
        return "safe_candidate_available"

    def _source_collection_summary(
        self,
        candidate: KnowledgeCandidate,
    ) -> KnowledgeSourceCollectionSummary | None:
        metadata = candidate.safe_metadata or {}
        raw_collection_id = metadata.get("collection_id")
        collection_id = self._uuid_or_none(raw_collection_id)
        safe_label = self._string_or_none(metadata.get("collection_safe_label"))
        route_scope_type = self._string_or_none(metadata.get("route_scope_type"))
        count_bucket = self._string_or_none(metadata.get("linked_kb_count_bucket"))
        if not any((collection_id, safe_label, route_scope_type, count_bucket)):
            return None
        return KnowledgeSourceCollectionSummary(
            collection_id=collection_id,
            safe_label=safe_label,
            route_scope_type=route_scope_type,
            linked_kb_count_bucket=count_bucket,
        )

    def _matched_terms(
        self,
        candidate: KnowledgeCandidate,
        terms: list[str],
    ) -> list[str]:
        if not terms:
            return []
        haystack, compact_haystack = self._candidate_texts(candidate)
        matched: list[str] = []
        for term in terms:
            if self._term_matches_text(term, haystack, compact_haystack):
                matched.append(term)
            if len(matched) >= 10:
                break
        return matched

    def _kb_relevance(
        self,
        candidate: KnowledgeCandidate,
        terms: list[str],
        matched_terms: list[str],
    ) -> float:
        if not terms:
            return 0.0
        match_ratio = len(matched_terms) / len(terms)
        if self._has_primary_structured_match(candidate, terms):
            return min(1.0, max(0.80, 0.75 + match_ratio * 0.25))
        return min(1.0, match_ratio)

    def _has_primary_structured_match(
        self,
        candidate: KnowledgeCandidate,
        terms: list[str],
    ) -> bool:
        primary_values: list[str] = []
        if candidate.safe_label:
            primary_values.append(candidate.safe_label)
        metadata = candidate.safe_metadata or {}
        for key in _KB_KEYWORD_LIST_METADATA_KEYS:
            value = metadata.get(key)
            if isinstance(value, (list, tuple, set)):
                primary_values.extend(str(item) for item in value if isinstance(item, str))
        haystack = self._normalize_match_text(" ".join(primary_values))
        compact_haystack = self._compact_match_text(haystack)
        return any(
            self._term_matches_text(term, haystack, compact_haystack)
            for term in terms
        )

    def _candidate_text(self, candidate: KnowledgeCandidate) -> str:
        return self._candidate_texts(candidate)[0]

    def _candidate_texts(self, candidate: KnowledgeCandidate) -> tuple[str, str]:
        values: list[str] = []
        if candidate.safe_label:
            values.append(candidate.safe_label)
        metadata = candidate.safe_metadata or {}
        for key in _KB_KEYWORD_STRING_METADATA_KEYS:
            value = metadata.get(key)
            if isinstance(value, str):
                values.append(value)
        for key in _KB_KEYWORD_LIST_METADATA_KEYS:
            value = metadata.get(key)
            if isinstance(value, (list, tuple, set)):
                values.extend(str(item) for item in value if isinstance(item, str))
        haystack = self._normalize_match_text(" ".join(values))
        return haystack, self._compact_match_text(haystack)

    def _query_terms(self, request: KnowledgeRAGRecommendationRequest) -> list[str]:
        if request.safe_query_topics:
            return self._structured_terms(request.safe_query_topics)
        return self._filtered_terms(request.workflow_intent, request.node_purpose)

    def _ranking_terms(
        self,
        candidates: list[KnowledgeCandidate],
        request: KnowledgeRAGRecommendationRequest,
    ) -> tuple[list[str], str]:
        structured_terms = self._query_terms(request)
        if not request.safe_query_topics:
            return structured_terms, "intent"
        if any(self._matched_terms(candidate, structured_terms) for candidate in candidates):
            return structured_terms, "structured"

        fallback_terms = self._filtered_terms(
            request.workflow_intent,
            request.node_purpose,
        )
        if fallback_terms:
            return fallback_terms, "fallback"
        return structured_terms, "structured"

    def _structured_terms(self, values: list[str]) -> list[str]:
        terms: list[str] = []
        for value in values:
            normalized = self._normalize_match_text(value)
            if not normalized or self._is_stop_term(normalized):
                continue
            if normalized not in terms:
                terms.append(normalized)
            if len(terms) >= 20:
                break
        return terms

    def _filtered_terms(self, *values: str | None) -> list[str]:
        return [
            term
            for term in self._terms(*values)
            if not self._is_stop_term(term)
        ]

    def _term_matches_text(
        self,
        term: str,
        haystack: str,
        compact_haystack: str,
    ) -> bool:
        normalized = self._normalize_match_text(term)
        if not normalized or self._is_stop_term(normalized):
            return False
        compact = self._compact_match_text(normalized)
        if normalized in haystack or (compact and compact in compact_haystack):
            return True
        tokens = [
            token
            for token in extract_safe_terms(normalized, limit=10)
            if not self._is_stop_term(token)
        ]
        return bool(tokens) and all(
            token in haystack or self._compact_match_text(token) in compact_haystack
            for token in tokens
        )

    def _normalize_match_text(self, value: object) -> str:
        return _WHITESPACE_RE.sub(" ", str(value or "").lower()).strip()

    def _compact_match_text(self, value: str) -> str:
        return _WHITESPACE_RE.sub("", value)

    def _is_stop_term(self, value: str) -> bool:
        normalized = self._normalize_match_text(value)
        compact = self._compact_match_text(normalized)
        return normalized in _SAFE_QUERY_STOP_TERMS or compact in _SAFE_QUERY_STOP_TERMS

    def _terms(self, *values: str | None) -> list[str]:
        return extract_safe_terms(*values, limit=50)

    def _source_tier_priority(self, candidate: KnowledgeCandidate) -> int:
        metadata = candidate.safe_metadata or {}
        return source_tier_priority(metadata.get("source_tier"))

    def _sync_freshness_score(self, candidate: KnowledgeCandidate) -> float:
        metadata = candidate.safe_metadata or {}
        sync_state = str(metadata.get("sync_state") or "").lower()
        if sync_state in {"stale", "failed"}:
            return 0.0
        source_acl_state = candidate.permission.source_acl_state
        if sync_state in _FRESH_SYNC_STATES or source_acl_state in {
            "fresh",
            "not_source_managed",
        }:
            return 1.0
        return 0.0

    def _freshness_bonus(self, candidate: KnowledgeCandidate) -> float:
        return self._sync_freshness_score(candidate)

    def _sync_state_penalty(self, candidate: KnowledgeCandidate) -> float:
        sync_state = str((candidate.safe_metadata or {}).get("sync_state") or "").lower()
        if sync_state in {"stale", "failed"}:
            return 0.1
        return 0.0

    def _used_signals(
        self,
        *,
        matched_terms: list[str],
        source_priority: int,
        availability: str,
        freshness: float,
        structured_query: bool = False,
        fallback_query: bool = False,
    ) -> list[str]:
        signals: list[str] = []
        if structured_query:
            signals.append("structured_safe_query")
        elif fallback_query:
            signals.append("fallback_safe_query")
        if matched_terms:
            signals.append("kb_relevance_match")
        if source_priority:
            signals.append("source_tier")
        if availability != "unknown":
            signals.append("runtime_availability")
        if freshness:
            signals.append("freshness")
        return signals or ["safe_candidate"]

    def _merge_buckets(self, *buckets: str) -> str:
        values = [self._bucket_floor(bucket) for bucket in buckets]
        return bucket_count(sum(values))

    def _bucket_floor(self, bucket: str | None) -> int:
        if bucket in {None, "0"}:
            return 0
        if bucket == "1":
            return 1
        if bucket == "2-10":
            return 2
        if bucket == "11-100":
            return 11
        return 101

    def _uuid_or_none(self, value: Any) -> uuid.UUID | None:
        if value is None:
            return None
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None
