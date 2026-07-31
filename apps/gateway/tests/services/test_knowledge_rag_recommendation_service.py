import uuid

import pytest

from apps.gateway.services.knowledge_rag_recommendation_service import (
    GENERIC_KB_LABEL,
    KnowledgeRAGRecommendationService,
)
from apps.shared.schemas.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateCollectionGroup,
    KnowledgeCandidateHierarchyResolution,
    KnowledgeCandidateResolution,
    KnowledgePermissionDecision,
    KnowledgeRAGRecommendationRequest,
)


def _candidate(
    *,
    candidate_id: uuid.UUID | None = None,
    safe_label: str | None = "휴가 규정",
    runtime_availability: str = "unknown",
    safe_metadata: dict | None = None,
) -> KnowledgeCandidate:
    return KnowledgeCandidate(
        candidate_id=candidate_id or uuid.uuid4(),
        candidate_type="knowledge_base",
        permission=KnowledgePermissionDecision(
            allowed=True,
            reason_code="allowed",
            external_reason_code="allowed",
            safe_metadata=safe_metadata or {},
        ),
        runtime_availability=runtime_availability,
        safe_label=safe_label,
        safe_metadata=safe_metadata or {},
    )


class FakeResolver:
    def __init__(self, resolution: KnowledgeCandidateResolution):
        self.resolution = resolution
        self.explicit_calls = []
        self.explicit_collection_calls = []
        self.auto_calls = []

    def resolve_explicit_kbs(self, knowledge_base_ids, *, allow_unready_candidates=False):
        self.explicit_calls.append(list(knowledge_base_ids))
        return self.resolution

    def resolve_auto_collection_candidates(
        self,
        *,
        collection_ids=None,
        max_collections=20,
        max_candidate_kbs=5000,
        allow_unready_candidates=False,
    ):
        self.auto_calls.append(
            {
                "collection_ids": collection_ids,
                "max_collections": max_collections,
                "max_candidate_kbs": max_candidate_kbs,
                "allow_unready_candidates": allow_unready_candidates,
            }
        )
        return self.resolution

    def resolve_builder_hierarchy(self, **kwargs):
        self.auto_calls.append(kwargs)
        return getattr(
            self,
            "hierarchy",
            KnowledgeCandidateHierarchyResolution(
                ungrouped_candidates=self.resolution.candidates,
                hidden_candidate_count_bucket=self.resolution.hidden_candidate_count_bucket,
                unavailable_candidate_count_bucket=(
                    self.resolution.unavailable_candidate_count_bucket
                ),
            ),
        )

    def resolve_explicit_collections(self, collection_ids):
        self.explicit_collection_calls.append(list(collection_ids))
        requested_ids = set(collection_ids)
        return [
            group
            for group in getattr(
                self,
                "hierarchy",
                KnowledgeCandidateHierarchyResolution(),
            ).collections
            if group.collection_id in requested_ids
        ]


class FailingResolver:
    def resolve_explicit_kbs(self, knowledge_base_ids):
        raise RuntimeError("resolver unavailable")

    def resolve_auto_collection_candidates(self, **kwargs):
        raise RuntimeError("resolver unavailable")


def test_builder_recommendation_display_caps_default_to_twenty():
    request = KnowledgeRAGRecommendationRequest(
        workflow_intent="display cap",
        mode="auto",
    )

    assert request.max_recommendations == 20
    assert request.max_collections == 20
    with pytest.raises(ValueError):
        KnowledgeRAGRecommendationRequest(
            workflow_intent="display cap",
            mode="auto",
            max_collections=21,
        )


def _service(resolver: FakeResolver) -> KnowledgeRAGRecommendationService:
    return KnowledgeRAGRecommendationService(
        None,
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        resolver=resolver,
    )


def test_hierarchical_selection_deduplicates_children_and_uses_stable_handles():
    shared_kb = _candidate(safe_label="공유 인사 KB", runtime_availability="available")
    other_kb = _candidate(safe_label="복지 KB", runtime_availability="available")
    collection_a = uuid.uuid4()
    collection_b = uuid.uuid4()
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[]))
    resolver.hierarchy = KnowledgeCandidateHierarchyResolution(
        collections=[
            KnowledgeCandidateCollectionGroup(
                collection_id=collection_a,
                safe_label="사내 문서",
                safe_metadata={"safe_topics": ["인사", "복지"]},
                candidates=[shared_kb, shared_kb, other_kb],
            ),
            KnowledgeCandidateCollectionGroup(
                collection_id=collection_b,
                safe_label="경영 문서",
                candidates=[shared_kb],
            ),
        ]
    )

    service = _service(resolver)
    request = KnowledgeRAGRecommendationRequest(
        workflow_intent="사내 인사 문서로 답변",
        node_purpose="인사 답변",
        mode="auto",
        max_recommendations=20,
    )
    result = service.recommend_for_builder(request)

    selection = result.knowledge_selection
    assert selection is not None
    internal = next(
        item for item in selection.collections if item.safe_label == "사내 문서"
    )
    assert len(internal.children) == 2
    shared_rows = [
        child
        for collection in selection.collections
        for child in collection.children
        if child.safe_label == "공유 인사 KB"
    ]
    assert len(shared_rows) == 2
    assert len({row.kb_handle for row in shared_rows}) == 1
    assert len({row.selection_key for row in shared_rows}) == 1
    assert {row.shared_collection_count for row in shared_rows} == {2}
    assert all(not row.kb_handle.endswith(str(shared_kb.candidate_id)) for row in shared_rows)
    assert result._issued_kb_resource_ids[shared_rows[0].kb_handle] == (
        shared_kb.candidate_id
    )
    assert result._issued_collection_resource_ids[
        internal.collection_handle
    ] == collection_a
    serialized = str(result.model_dump(mode="json"))
    assert str(shared_kb.candidate_id) not in serialized
    assert str(collection_a) not in serialized
    query_terms, _source = service._ranking_terms(  # noqa: SLF001
        [shared_kb, other_kb], request
    )
    metadata_terms = {"사내", "문서", "인사", "복지"}
    metadata_score = len(metadata_terms.intersection(query_terms)) / len(
        set(query_terms)
    )
    child_scores = [child.score for child in internal.children]
    expected_collection_score = (
        max(child_scores) * 0.60
        + (sum(sorted(child_scores, reverse=True)[:3]) / len(child_scores)) * 0.30
        + metadata_score * 0.10
    )
    assert internal.score == pytest.approx(round(expected_collection_score, 4))
    assert selection.collections == sorted(
        selection.collections,
        key=lambda item: (-item.score, item.safe_label or "", item.collection_handle),
    )


def test_collection_score_uses_all_authorized_children_before_display_cap():
    higher = _candidate(
        safe_label="인사 정책",
        runtime_availability="available",
        safe_metadata={"kb_safe_topics": ["인사 정책"]},
    )
    lower = _candidate(
        safe_label="복지 안내",
        runtime_availability="unknown",
        safe_metadata={"kb_safe_topics": ["복지"]},
    )
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[]))
    resolver.hierarchy = KnowledgeCandidateHierarchyResolution(
        collections=[
            KnowledgeCandidateCollectionGroup(
                collection_id=uuid.uuid4(),
                safe_label="사내 문서",
                candidates=[higher, lower],
            )
        ]
    )
    service = _service(resolver)
    request = KnowledgeRAGRecommendationRequest(
        workflow_intent="인사 정책",
        mode="auto",
        max_recommendations=1,
    )
    ranked = service._rank_candidates([higher, lower], request)  # noqa: SLF001

    result = service.recommend_for_builder(request)

    collection = result.knowledge_selection.collections[0]
    assert len(collection.children) == 1
    all_child_scores = sorted((item[1] for item in ranked), reverse=True)
    expected = all_child_scores[0] * 0.60 + (
        sum(all_child_scores[:3]) / len(all_child_scores[:3])
    ) * 0.30
    assert collection.score == pytest.approx(round(expected, 4))


def test_collection_limit_is_applied_after_scoring_and_stable_sort():
    low = _candidate(safe_label="가 낮은 Collection KB")
    high = _candidate(safe_label="하 높은 점수 KB")
    middle = _candidate(safe_label="중간 점수 KB")
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[]))
    resolver.hierarchy = KnowledgeCandidateHierarchyResolution(
        collections=[
            KnowledgeCandidateCollectionGroup(
                collection_id=uuid.uuid4(),
                safe_label="가 Collection",
                candidates=[low],
            ),
            KnowledgeCandidateCollectionGroup(
                collection_id=uuid.uuid4(),
                safe_label="하 Collection",
                candidates=[high],
            ),
            KnowledgeCandidateCollectionGroup(
                collection_id=uuid.uuid4(),
                safe_label="중 Collection",
                candidates=[middle],
            ),
        ]
    )
    service = _service(resolver)
    request = KnowledgeRAGRecommendationRequest(
        workflow_intent="collection selection",
        mode="auto",
        max_collections=2,
        max_recommendations=20,
    )
    ranked = [
        (low, 0.10, [], []),
        (high, 0.90, [], []),
        (middle, 0.70, [], []),
    ]

    selection = service._knowledge_selection(  # noqa: SLF001
        resolver.hierarchy,
        ranked,
        request,
    )

    assert [item.safe_label for item in selection.collections] == [
        "하 Collection",
        "중 Collection",
    ]


def test_equal_score_candidates_sort_by_safe_label_before_opaque_handle():
    label_z = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        safe_label="Zulu",
    )
    label_a = _candidate(
        candidate_id=uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        safe_label="Alpha",
    )
    resolver = FakeResolver(
        KnowledgeCandidateResolution(candidates=[label_z, label_a])
    )
    service = _service(resolver)
    request = KnowledgeRAGRecommendationRequest(
        workflow_intent="unmatched query",
        mode="auto",
    )

    ranked = service._rank_candidates([label_z, label_a], request)  # noqa: SLF001

    assert [item[0].safe_label for item in ranked] == ["Alpha", "Zulu"]


def test_issued_handle_materialization_revalidates_only_bound_kb():
    candidate = _candidate(safe_label="Previously issued KB")
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[candidate]))
    resolver.hierarchy = KnowledgeCandidateHierarchyResolution(
        collections=[
            KnowledgeCandidateCollectionGroup(
                collection_id=uuid.uuid4(),
                safe_label="Previously issued Collection",
                candidates=[candidate],
            )
        ]
    )
    service = _service(resolver)
    handle = service._recommendation_id(candidate)  # noqa: SLF001
    request = KnowledgeRAGRecommendationRequest(
        workflow_intent="selection apply",
        mode="auto",
        max_collections=1,
        max_candidate_kbs=1,
    )

    materialized = service.materialize_candidate_handles_for_builder(
        request,
        {handle},
        issued_resource_ids={handle: candidate.candidate_id},
    )

    assert materialized[0]["safe_handle"] == handle
    assert resolver.explicit_calls == [[candidate.candidate_id]]
    assert resolver.auto_calls == []


def test_issued_handle_materialization_rejects_mismatched_resource_binding():
    candidate = _candidate(safe_label="Issued KB")
    resolver = FakeResolver(
        KnowledgeCandidateResolution(candidates=[candidate])
    )
    service = _service(resolver)
    handle = service._recommendation_id(candidate)  # noqa: SLF001

    materialized = service.materialize_candidate_handles_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="selection apply",
            mode="auto",
        ),
        {handle},
        issued_resource_ids={handle: uuid.uuid4()},
    )

    assert materialized == []
    assert resolver.explicit_calls == []


def test_route_authorized_collection_remains_selectable_without_visible_children():
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[]))
    resolver.hierarchy = KnowledgeCandidateHierarchyResolution(
        collections=[
            KnowledgeCandidateCollectionGroup(
                collection_id=uuid.uuid4(),
                safe_label="제한 문서",
                safe_metadata={"safe_topics": ["제한 문서"]},
                candidates=[],
            )
        ]
    )

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="제한 문서로 답변",
            mode="auto",
        )
    )

    assert result.status == "recommended"
    assert result.recommendations == []
    assert len(result.knowledge_selection.collections) == 1
    assert result.knowledge_selection.collections[0].children == []
    assert result.fallback_reason is None


def test_hierarchical_response_does_not_expose_hidden_kb_count_bucket():
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[]))
    resolver.hierarchy = KnowledgeCandidateHierarchyResolution(
        collections=[],
        ungrouped_candidates=[],
        hidden_candidate_count_bucket="2-10",
        unavailable_candidate_count_bucket="1",
    )

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="사내 문서",
            mode="auto",
        )
    )

    assert result.summary.hidden_or_unavailable_count_bucket == "0"


def test_issued_collection_handle_revalidates_only_bound_collection():
    kb = _candidate(safe_label="인사 KB", runtime_availability="available")
    collection_id = uuid.uuid4()
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[]))
    resolver.hierarchy = KnowledgeCandidateHierarchyResolution(
        collections=[
            KnowledgeCandidateCollectionGroup(
                collection_id=collection_id,
                safe_label="사내 문서",
                candidates=[kb],
            )
        ]
    )
    service = _service(resolver)
    request = KnowledgeRAGRecommendationRequest(
        workflow_intent="사내 문서로 답변",
        mode="auto",
    )
    result = service.recommend_for_builder(request)
    handle = result.knowledge_selection.collections[0].collection_handle
    recommendation_call_count = len(resolver.auto_calls)

    assert service.materialize_collection_handles_for_builder(
        request,
        {handle},
        issued_resource_ids={handle: collection_id},
    ) == [
        {
            "safe_handle": handle,
            "knowledge_collection_id": str(collection_id),
            "name": "사내 문서",
        }
    ]
    assert resolver.explicit_collection_calls == [[collection_id]]
    assert len(resolver.auto_calls) == recommendation_call_count

    resolver.hierarchy = KnowledgeCandidateHierarchyResolution()

    assert service.materialize_collection_handles_for_builder(
        request,
        {handle},
        issued_resource_ids={handle: collection_id},
    ) == []


def test_recommendation_returns_kb_item_and_collection_summary_only():
    kb_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    resolver = FakeResolver(
        KnowledgeCandidateResolution(
            candidates=[
                _candidate(
                    candidate_id=kb_id,
                    safe_metadata={
                        "collection_id": str(collection_id),
                        "collection_safe_label": "HR 정책",
                        "route_scope_type": "auto_collection",
                        "linked_kb_count_bucket": "2-10",
                        "source_tier": "company_policy",
                        "sync_state": "synced",
                    },
                )
            ],
            hidden_candidate_count_bucket="2-10",
            unavailable_candidate_count_bucket="1",
        )
    )

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="신입 직원 휴가 규정 답변",
            node_purpose="HR policy answer",
            mode="auto",
            collection_ids=[collection_id],
        ),
        include_materialized_refs=True,
    )

    assert resolver.auto_calls
    recommendation = result.recommendations[0]
    assert recommendation.candidate_type == "knowledge_base"
    assert recommendation.candidate_id == recommendation.recommendation_id
    assert recommendation.candidate_handle == recommendation.recommendation_id
    assert recommendation.recommendation_id.startswith("rec-")
    assert str(kb_id) not in recommendation.recommendation_id
    assert recommendation.materialized_knowledge_bases[0].id == kb_id
    assert recommendation.materialized_knowledge_bases[0].name == "휴가 규정"
    assert recommendation.source_collection_summary is not None
    assert recommendation.source_collection_summary.safe_label == "HR 정책"
    assert recommendation.source_collection_summary.linked_kb_count_bucket == "2-10"
    assert "raw_source_url" not in result.model_dump_json()
    assert "exact_denied_count" not in result.model_dump_json()


def test_recommendation_uses_generic_label_without_raw_kb_name():
    kb_id = uuid.uuid4()
    resolver = FakeResolver(
        KnowledgeCandidateResolution(
            candidates=[_candidate(candidate_id=kb_id, safe_label=None)],
        )
    )

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="계약 검토",
            mode="explicit_kb",
            knowledge_base_ids=[kb_id],
        ),
        include_materialized_refs=True,
    )

    recommendation = result.recommendations[0]
    assert recommendation.safe_label is None
    assert recommendation.materialized_knowledge_bases[0].name == GENERIC_KB_LABEL
    assert "safe_label_unavailable" in recommendation.warnings


def test_safe_intent_candidate_context_is_bounded_and_excludes_raw_identity():
    raw_kb_id = uuid.uuid4()
    resolver = FakeResolver(
        KnowledgeCandidateResolution(
            candidates=[
                _candidate(
                    candidate_id=raw_kb_id,
                    safe_label="사내 인사 문서",
                    runtime_availability="available",
                    safe_metadata={
                        "kb_safe_topics": ["인사", "온보딩"],
                        "kb_safe_description": "사내 인사 정책",
                        "raw_source_path": "/secret/hr.md",
                        "collection_id": str(uuid.uuid4()),
                    },
                )
            ]
        )
    )

    context = _service(resolver).safe_intent_candidates_for_builder(
        "사내 인사 문서 챗봇을 만들어줘",
        max_candidates=20,
    )

    assert context == [
        {
            "candidate_handle": context[0]["candidate_handle"],
            "safe_label": "사내 인사 문서",
            "safe_topics": ["인사", "온보딩"],
            "safe_description": "사내 인사 정책",
            "runtime_availability": "available",
            "relevance_score": context[0]["relevance_score"],
        }
    ]
    serialized = str(context)
    assert context[0]["candidate_handle"].startswith("rec-")
    assert str(raw_kb_id) not in serialized
    assert "/secret/hr.md" not in serialized
    assert "collection_id" not in serialized


def test_safe_intent_candidates_exclude_zero_relevance_items():
    resolver = FakeResolver(
        KnowledgeCandidateResolution(
            candidates=[
                _candidate(
                    safe_label="Finance policy",
                    runtime_availability="available",
                    safe_metadata={
                        "kb_safe_topics": ["finance", "policy"],
                        "kb_safe_description": "Approved finance policy",
                    },
                )
            ]
        )
    )

    context = _service(resolver).safe_intent_candidates_for_builder(
        "llm workflow",
        max_candidates=20,
    )

    assert context == []


def test_high_risk_domain_only_changes_recommended_options():
    resolver = FakeResolver(
        KnowledgeCandidateResolution(candidates=[_candidate(runtime_availability="available")])
    )

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="계약 위반 기준 확인",
            high_risk_domain="legal",
        )
    )

    recommendation = result.recommendations[0]
    assert recommendation.recommended_options.scoreThreshold == 0.3
    assert recommendation.recommended_options.topK == 5
    assert recommendation.recommended_options.evidenceSufficiencyPolicy == "strict_citation"
    assert recommendation.recommended_options.queryRewriteMode == "template"
    assert recommendation.safe_reason_code == "high_risk_domain_requires_citation"
    assert recommendation.runtime_availability == "available"


def test_standard_domain_recommends_default_rag_options():
    resolver = FakeResolver(
        KnowledgeCandidateResolution(candidates=[_candidate(runtime_availability="available")])
    )

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(workflow_intent="휴가 규정 확인")
    )

    options = result.recommendations[0].recommended_options
    assert options.scoreThreshold == 0.3
    assert options.topK == 5


def test_threshold_result_uses_documented_values():
    resolver = FakeResolver(
        KnowledgeCandidateResolution(
            candidates=[
                _candidate(
                    candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
                    safe_label="unmatched",
                ),
                _candidate(
                    candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000012"),
                    safe_label="contract review",
                ),
            ]
        )
    )

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="contract review",
            max_recommendations=2,
        )
    )

    values = {item.threshold_result for item in result.recommendations}
    assert values <= {"high_confidence", "close_score", "below_threshold"}
    assert "medium_confidence" not in values
    assert "low_confidence" not in values


def test_korean_tokenizer_extracts_builder_intent_terms():
    service = _service(FakeResolver(KnowledgeCandidateResolution(candidates=[])))

    terms = service._terms(  # noqa: SLF001
        "사내문서1 KB와 웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘",
        "사내 문서 챗봇",
    )

    assert "사내문서1" in terms
    assert "kb" in terms
    assert "웹훅" in terms
    assert "사내" in terms
    assert "문서" in terms
    assert "챗봇" in terms


def test_kb_ranking_ignores_collection_metadata_for_relevance_score():
    collection_only = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        safe_label=None,
        runtime_availability="available",
        safe_metadata={
            "collection_safe_label": "사내문서1 묶음",
            "collection_safe_topics": ["사내문서1", "사내 문서"],
            "kb_safe_topics": ["재무", "정산"],
        },
    )
    kb_match = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        safe_label=None,
        runtime_availability="available",
        safe_metadata={
            "collection_safe_label": "일반 묶음",
            "collection_safe_topics": ["일반"],
            "kb_safe_topics": ["사내문서1", "사내 문서", "온보딩"],
        },
    )
    resolver = FakeResolver(
        KnowledgeCandidateResolution(candidates=[collection_only, kb_match])
    )

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="사내문서1 KB와 웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘",
            node_purpose="사내 문서 챗봇",
            max_recommendations=2,
        ),
        include_materialized_refs=True,
    )

    assert result.recommendations[0].materialized_knowledge_bases[0].id == kb_match.candidate_id
    assert result.recommendations[0].provenance.matched_safe_terms


def test_structured_query_topics_drive_kb_relevance_and_ignore_workflow_noise():
    kb_match = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000021"),
        safe_label="사내문서 1",
        runtime_availability="unknown",
        safe_metadata={
            "kb_safe_topics": ["사내 문서"],
            "sync_state": "synced",
        },
    )
    workflow_noise = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000022"),
        safe_label="웹훅 챗봇 워크플로우",
        runtime_availability="available",
        safe_metadata={"sync_state": "synced"},
    )
    resolver = FakeResolver(
        KnowledgeCandidateResolution(candidates=[workflow_noise, kb_match])
    )

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="웹훅으로 받는 사내 문서 챗봇 워크플로우를 만들어줘",
            node_purpose="사내 문서 챗봇",
            safe_query_topics=["사내 문서", "내부 문서", "문서 질의"],
            max_recommendations=2,
        ),
        include_materialized_refs=True,
    )

    recommendation = result.recommendations[0]
    assert recommendation.materialized_knowledge_bases[0].id == kb_match.candidate_id
    assert recommendation.score >= 0.70
    assert recommendation.safe_reason_code == "structured_intent_matches_safe_metadata"
    assert "structured_safe_query" in recommendation.provenance.used_signals
    assert "kb_relevance_match" in recommendation.provenance.used_signals
    assert recommendation.provenance.matched_safe_terms == ["사내 문서"]


def test_structured_topics_fall_back_to_safe_intent_terms_only_when_none_match():
    matching_kb = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000023"),
        safe_label="Employee handbook",
        runtime_availability="available",
        safe_metadata={
            "kb_safe_topics": ["employee handbook", "leave policy"],
            "source_tier": "company_policy",
            "sync_state": "synced",
        },
    )
    unrelated_kb = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000024"),
        safe_label="Finance controls",
        runtime_availability="available",
        safe_metadata={
            "kb_safe_topics": ["expense policy"],
            "source_tier": "company_policy",
            "sync_state": "synced",
        },
    )

    result = _service(
        FakeResolver(
            KnowledgeCandidateResolution(candidates=[unrelated_kb, matching_kb])
        )
    ).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="Create an employee handbook question workflow",
            node_purpose="Answer employee handbook questions",
            safe_query_topics=["general internal assistance"],
            max_recommendations=2,
        )
    )

    recommendation = result.recommendations[0]
    assert recommendation.safe_label == "Employee handbook"
    assert recommendation.score >= 0.70
    assert "fallback_safe_query" in recommendation.provenance.used_signals
    assert "structured_safe_query" not in recommendation.provenance.used_signals


def test_unmatched_kb_does_not_receive_an_operational_score_without_relevance():
    result = _service(
        FakeResolver(
            KnowledgeCandidateResolution(
                candidates=[
                    _candidate(
                        safe_label="Employee handbook",
                        runtime_availability="available",
                        safe_metadata={
                            "kb_safe_topics": ["employee handbook"],
                            "source_tier": "company_policy",
                            "sync_state": "synced",
                        },
                    )
                ]
            )
        )
    ).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="Create a tax filing workflow",
            node_purpose="Tax filing",
            safe_query_topics=["tax filing"],
        )
    )

    recommendation = result.recommendations[0]
    assert recommendation.score == 0.0
    assert recommendation.threshold_result == "below_threshold"
    assert "kb_relevance_match" not in recommendation.provenance.used_signals


def test_threshold_result_can_emit_all_documented_buckets():
    service = _service(FakeResolver(KnowledgeCandidateResolution(candidates=[])))
    request = KnowledgeRAGRecommendationRequest(workflow_intent="policy")
    candidate = _candidate(runtime_availability="available")

    values = {
        service._recommendation(candidate, 0.8, ["policy"], ["metadata"], request).threshold_result,  # noqa: SLF001
        service._recommendation(candidate, 0.5, ["policy"], ["metadata"], request).threshold_result,  # noqa: SLF001
        service._recommendation(candidate, 0.1, [], ["metadata"], request).threshold_result,  # noqa: SLF001
    }

    assert values == {"high_confidence", "close_score", "below_threshold"}


def test_recommendation_returns_unavailable_when_resolver_fails():
    result = _service(FailingResolver()).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            intent_summary="휴가 정책",
            node_purpose_summary="정책 요약",
            pending_resolution_ref="res-kb-1",
            knowledge_requirement={"requirement_id": "kr-1"},
        )
    )

    assert result.status == "unavailable"
    assert result.resolution_id == "res-kb-1"
    assert result.requirement_id == "kr-1"
    assert result.fallback_reason == "adapter_unavailable"
    assert result.recommendations == []
    assert result.user_safe_warning


def test_recommendation_falls_back_to_clarification_when_ranker_fails_with_candidates():
    resolver = FakeResolver(
        KnowledgeCandidateResolution(candidates=[_candidate(safe_label="휴가 규정")])
    )
    service = _service(resolver)

    def fail_rank(*_args, **_kwargs):
        raise RuntimeError("ranker unavailable")

    service._rank_candidates = fail_rank  # type: ignore[method-assign]  # noqa: SLF001

    result = service.recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="휴가 정책",
            pending_resolution_ref="res-kb-1",
            knowledge_requirement={"requirement_id": "kr-1"},
        )
    )

    assert result.status == "clarification_required"
    assert result.resolution_id == "res-kb-1"
    assert result.requirement_id == "kr-1"
    assert result.fallback_reason == "adapter_unavailable"
    assert result.recommendations == []
    assert result.clarification_options
    option = result.clarification_options[0]
    assert option["candidate_id"].startswith("rec-")
    assert option["confidence"] == "low"
    assert option["score"] == 0.0
    assert option["threshold_result"] == "adapter_unavailable"
    assert result.user_safe_warning


def test_materialize_candidate_handles_does_not_depend_on_top_n_ranking():
    lower = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        safe_label="복지 안내",
        runtime_availability="unknown",
    )
    higher = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        safe_label="휴가 정책",
        runtime_availability="available",
        safe_metadata={"source_tier": "company_policy"},
    )
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[lower, higher]))
    service = _service(resolver)
    request = KnowledgeRAGRecommendationRequest(
        workflow_intent="휴가 정책",
        max_recommendations=1,
    )
    recommended = service.recommend_for_builder(request)
    lower_handle = service._recommendation_id(lower)  # noqa: SLF001

    assert recommended.recommendations[0].candidate_handle != lower_handle
    materialized = service.materialize_candidate_handles_for_builder(
        request,
        {lower_handle},
        issued_resource_ids={lower_handle: lower.candidate_id},
    )

    assert materialized == [
        {
            "safe_handle": lower_handle,
            "knowledge_base_id": str(lower.candidate_id),
            "name": "복지 안내",
        }
    ]


def test_materialize_ungrouped_handle_when_collection_candidates_exist():
    collection_child = _candidate(safe_label="Collection child")
    ungrouped = _candidate(safe_label="Directly authorized KB")
    resolver = FakeResolver(
        KnowledgeCandidateResolution(candidates=[collection_child, ungrouped])
    )
    resolver.hierarchy = KnowledgeCandidateHierarchyResolution(
        collections=[
            KnowledgeCandidateCollectionGroup(
                collection_id=uuid.uuid4(),
                safe_label="Collection",
                candidates=[collection_child],
            )
        ],
        ungrouped_candidates=[ungrouped],
    )
    service = _service(resolver)
    request = KnowledgeRAGRecommendationRequest(
        workflow_intent="Use the directly authorized knowledge base",
        mode="auto",
    )
    ungrouped_handle = service._recommendation_id(ungrouped)  # noqa: SLF001

    materialized = service.materialize_candidate_handles_for_builder(
        request,
        {ungrouped_handle},
        issued_resource_ids={ungrouped_handle: ungrouped.candidate_id},
    )

    assert materialized == [
        {
            "safe_handle": ungrouped_handle,
            "knowledge_base_id": str(ungrouped.candidate_id),
            "name": "Directly authorized KB",
        }
    ]


def test_intended_subject_absence_keeps_runtime_availability_unknown_warning():
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[_candidate()]))

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(workflow_intent="복지 안내")
    )

    recommendation = result.recommendations[0]
    assert recommendation.runtime_availability == "unknown"
    assert "runtime_availability_unknown" in recommendation.warnings


def test_recommendation_keeps_stale_or_failed_sync_candidates_with_safe_warning():
    resolver = FakeResolver(
        KnowledgeCandidateResolution(
            candidates=[
                _candidate(
                    safe_label="휴가 규정",
                    runtime_availability="available",
                    safe_metadata={"sync_state": "stale"},
                ),
                _candidate(
                    safe_label="인사 규정",
                    runtime_availability="available",
                    safe_metadata={"sync_state": "failed"},
                ),
            ]
        )
    )

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="휴가 인사 규정",
            max_recommendations=2,
        )
    )

    warnings = {warning for item in result.recommendations for warning in item.warnings}
    assert result.status == "recommended"
    assert "kb_sync_state_stale" in warnings
    assert "kb_sync_state_failed" in warnings


def test_request_normalizes_control_characters_and_rejects_blank_text():
    request = KnowledgeRAGRecommendationRequest(
        workflow_intent="휴가\x00\x01 규정\n답변",
        node_purpose=" HR\tpolicy ",
    )

    assert request.workflow_intent == "휴가 규정 답변"
    assert request.node_purpose == "HR policy"

    try:
        KnowledgeRAGRecommendationRequest(workflow_intent="\x00\n\t")
    except ValueError as exc:
        assert "text must not be empty" in str(exc)
    else:  # pragma: no cover - pydantic must reject blank raw input
        raise AssertionError("blank workflow intent was accepted")


def test_recommendation_cap_and_stable_ranking():
    lower = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        safe_label="복지 안내",
        runtime_availability="unknown",
    )
    higher = _candidate(
        candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        safe_label="휴가 정책",
        runtime_availability="available",
        safe_metadata={"source_tier": "company_policy"},
    )
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[lower, higher]))

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="휴가 정책",
            max_recommendations=1,
        ),
        include_materialized_refs=True,
    )

    assert len(result.recommendations) == 1
    assert result.recommendations[0].candidate_id == (
        result.recommendations[0].recommendation_id
    )
    assert result.recommendations[0].materialized_knowledge_bases[0].id == (
        higher.candidate_id
    )
    assert result.summary.recommendation_count_bucket == "1"


def test_recommendation_exposes_up_to_twenty_clarification_options():
    candidates = [
        _candidate(
            candidate_id=uuid.UUID(f"00000000-0000-0000-0000-{index:012d}"),
            safe_label=f"policy kb {index}",
            runtime_availability="available",
            safe_metadata={"source_tier": "company_policy", "sync_state": "synced"},
        )
        for index in range(1, 26)
    ]
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=candidates))

    result = _service(resolver).recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="policy",
            safe_query_topics=["policy"],
            max_recommendations=20,
        )
    )

    assert len(result.recommendations) == 20
    assert len(result.clarification_options) == 20
    assert result.clarification_options[0]["type"] == "knowledge_base"
    assert result.clarification_options[0]["candidate_id"].startswith("rec-")
    assert result.clarification_options[0]["score"] is not None
    assert result.summary.recommendation_count_bucket == "11-100"


def test_auto_collection_omitted_scope_and_explicit_empty_scope_are_distinct():
    resolver = FakeResolver(KnowledgeCandidateResolution(candidates=[]))
    service = _service(resolver)

    service.recommend_for_builder(
        KnowledgeRAGRecommendationRequest(workflow_intent="휴가 정책")
    )
    service.recommend_for_builder(
        KnowledgeRAGRecommendationRequest(
            workflow_intent="휴가 정책",
            mode="auto_collection",
            collection_ids=[],
            max_collections=10,
        )
    )

    assert resolver.auto_calls[0]["collection_ids"] is None
    assert resolver.auto_calls[0]["max_collections"] == 20
    assert resolver.auto_calls[1]["collection_ids"] == []
    assert resolver.auto_calls[1]["max_collections"] == 10
