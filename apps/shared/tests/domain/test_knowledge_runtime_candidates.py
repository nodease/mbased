from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest
from apps.shared.domain.knowledge_runtime_candidates import (
    AnonymousPublicAudience,
    AuthenticatedAudience,
    KnowledgeCollectionCandidateStream,
    KnowledgeRuntimeCandidateConfigurationError,
    KnowledgeRuntimeCandidateRequest,
    KnowledgeRuntimeCandidateSnapshot,
    bucket_safe_count,
    resolve_knowledge_runtime_candidates,
)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _request(
    *,
    direct: tuple[UUID, ...] = (),
    collections: tuple[UUID, ...] = (),
    budget: int = 20,
    scan_cap: int = 5000,
) -> KnowledgeRuntimeCandidateRequest:
    return KnowledgeRuntimeCandidateRequest(
        audience=AuthenticatedAudience(
            organization_id=_id(9000),
            user_id=_id(9001),
        ),
        direct_kb_ids=direct,
        collection_ids=collections,
        candidate_budget=budget,
        candidate_scan_cap=scan_cap,
    )


def _snapshot(
    *,
    direct: tuple[UUID, ...] = (),
    streams: tuple[KnowledgeCollectionCandidateStream, ...] = (),
    excluded: int = 0,
    scan_limited: bool = False,
) -> KnowledgeRuntimeCandidateSnapshot:
    return KnowledgeRuntimeCandidateSnapshot(
        eligible_direct_kb_ids=direct,
        collection_streams=streams,
        policy_excluded_count=excluded,
        scan_limited=scan_limited,
    )


def _stream(collection: int, *knowledge_bases: int):
    return KnowledgeCollectionCandidateStream(
        collection_id=_id(collection),
        eligible_kb_ids=tuple(_id(value) for value in knowledge_bases),
    )


def test_audience_contract_is_explicit_and_immutable():
    authenticated = AuthenticatedAudience(
        organization_id=_id(1),
        user_id=_id(2),
    )
    anonymous = AnonymousPublicAudience(organization_id=_id(1))

    assert authenticated.kind == "authenticated"
    assert anonymous.kind == "anonymous_public"
    assert not hasattr(anonymous, "user_id")
    with pytest.raises(FrozenInstanceError):
        authenticated.user_id = _id(3)  # type: ignore[misc]


def test_request_deduplicates_references_by_first_configured_position():
    request = _request(
        direct=(_id(3), _id(1), _id(3), _id(2)),
        collections=(_id(12), _id(11), _id(12)),
    )

    assert request.direct_kb_ids == (_id(3), _id(1), _id(2))
    assert request.collection_ids == (_id(12), _id(11))


@pytest.mark.parametrize(
    ("kwargs", "reason_code"),
    [
        ({"budget": 0}, "candidate_budget_invalid"),
        ({"budget": 21}, "candidate_budget_invalid"),
        ({"budget": 20, "scan_cap": 19}, "candidate_scan_cap_invalid"),
        ({"scan_cap": 5001}, "candidate_scan_cap_invalid"),
        (
            {"direct": tuple(_id(value) for value in range(1, 22))},
            "direct_reference_limit_exceeded",
        ),
        (
            {"collections": tuple(_id(value) for value in range(101, 122))},
            "collection_reference_limit_exceeded",
        ),
    ],
)
def test_request_rejects_invalid_server_owned_limits(kwargs, reason_code):
    with pytest.raises(KnowledgeRuntimeCandidateConfigurationError) as exc_info:
        _request(**kwargs)

    assert exc_info.value.reason_code == reason_code
    assert str(exc_info.value) == reason_code


def test_direct_candidates_are_pinned_before_collection_round_robin():
    request = _request(
        direct=(_id(1), _id(2)),
        collections=(_id(101), _id(102)),
    )
    snapshot = _snapshot(
        direct=(_id(2), _id(1)),
        streams=(_stream(102, 4, 6), _stream(101, 3, 5)),
    )

    result = resolve_knowledge_runtime_candidates(request, snapshot)

    assert [candidate.knowledge_base_id for candidate in result.candidates] == [
        _id(1),
        _id(2),
        _id(3),
        _id(4),
        _id(5),
        _id(6),
    ]
    assert [candidate.provenance.kind for candidate in result.candidates] == [
        "direct",
        "direct",
        "collection",
        "collection",
        "collection",
        "collection",
    ]
    assert result.routing_mode == "mixed"


def test_duplicate_keeps_first_provenance_and_does_not_consume_round():
    request = _request(
        direct=(_id(1),),
        collections=(_id(101), _id(102)),
    )
    snapshot = _snapshot(
        direct=(_id(1),),
        streams=(_stream(101, 1, 2, 3), _stream(102, 2, 4)),
    )

    result = resolve_knowledge_runtime_candidates(request, snapshot)

    assert [candidate.knowledge_base_id for candidate in result.candidates] == [
        _id(1),
        _id(2),
        _id(4),
        _id(3),
    ]
    assert result.candidates[0].provenance.kind == "direct"
    assert result.candidates[1].provenance.collection_id == _id(101)
    assert result.candidates[2].provenance.collection_id == _id(102)


def test_duplicate_candidate_records_all_direct_and_collection_provenance_once():
    request = _request(
        direct=(_id(1),),
        collections=(_id(101), _id(102)),
    )
    snapshot = _snapshot(
        direct=(_id(1),),
        streams=(_stream(101, 1), _stream(102, 1)),
    )

    result = resolve_knowledge_runtime_candidates(request, snapshot)

    assert len(result.candidates) == 1
    assert [item.kind for item in result.candidates[0].provenances] == [
        "direct",
        "collection",
        "collection",
    ]
    assert [item.collection_id for item in result.candidates[0].provenances] == [
        None,
        _id(101),
        _id(102),
    ]
    assert result.routing_mode == "mixed"


def test_unselected_collection_and_unconfigured_direct_fact_cannot_widen_scope():
    request = _request(
        direct=(_id(1), _id(2)),
        collections=(_id(101),),
    )
    snapshot = _snapshot(
        direct=(_id(99), _id(2)),
        streams=(_stream(999, 7), _stream(101, 3)),
    )

    result = resolve_knowledge_runtime_candidates(request, snapshot)

    assert [candidate.knowledge_base_id for candidate in result.candidates] == [
        _id(2),
        _id(3),
    ]


def test_budget_is_deterministic_success_with_safe_warning():
    request = _request(
        direct=tuple(_id(value) for value in range(1, 20)),
        collections=(_id(101),),
        budget=20,
    )
    snapshot = _snapshot(
        direct=request.direct_kb_ids,
        streams=(_stream(101, 20, 21),),
    )

    result = resolve_knowledge_runtime_candidates(request, snapshot)

    assert result.status == "resolved"
    assert len(result.candidates) == 20
    assert result.candidates[-1].knowledge_base_id == _id(20)
    assert result.budget_limited is True
    assert result.warning_codes == ("candidate_budget_limited",)
    assert result.eligible_candidate_count_bucket == "11-100"


def test_scan_limit_is_separate_safe_warning():
    request = _request(collections=(_id(101),))
    snapshot = _snapshot(
        streams=(_stream(101, 1),),
        scan_limited=True,
    )

    result = resolve_knowledge_runtime_candidates(request, snapshot)

    assert result.budget_limited is False
    assert result.scan_limited is True
    assert result.warning_codes == ("candidate_scan_limited",)


def test_zero_candidates_returns_safe_no_result_without_identity_summary():
    request = _request(direct=(_id(1),), collections=(_id(101),))

    result = resolve_knowledge_runtime_candidates(
        request,
        _snapshot(excluded=17),
    )

    assert result.status == "safe_no_result"
    assert result.candidates == ()
    assert result.routing_mode == "none"
    assert result.reason_code == "knowledge_candidates.safe_no_result"
    assert result.configured_direct_count_bucket == "1"
    assert result.configured_collection_count_bucket == "1"
    assert result.policy_excluded_count_bucket == "11-100"
    assert not hasattr(result, "excluded_candidate_ids")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (1, "1"),
        (2, "2-10"),
        (10, "2-10"),
        (11, "11-100"),
        (100, "11-100"),
        (101, "100+"),
    ],
)
def test_safe_count_buckets(value, expected):
    assert bucket_safe_count(value) == expected


def test_safe_count_bucket_rejects_negative_internal_count():
    with pytest.raises(ValueError, match="safe_count_invalid"):
        bucket_safe_count(-1)
