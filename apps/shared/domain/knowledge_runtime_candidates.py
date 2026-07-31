"""Pure policy for Workflow runtime Knowledge candidate resolution.

This module intentionally has no framework, ORM, queue, or concrete runtime
imports. Outbound adapters may only project already-authorized, ready facts into
the immutable snapshot contract defined here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias
from uuid import UUID

DEFAULT_RUNTIME_CANDIDATE_BUDGET = 20
MAX_RUNTIME_CANDIDATE_BUDGET = 20
MAX_RUNTIME_DIRECT_KB_REFERENCES = 20
MAX_RUNTIME_COLLECTION_REFERENCES = 20
DEFAULT_RUNTIME_CANDIDATE_SCAN_CAP = 5000
MAX_RUNTIME_CANDIDATE_SCAN_CAP = 5000

AudienceKind: TypeAlias = Literal["authenticated", "anonymous_public"]
CandidateProvenanceKind: TypeAlias = Literal["direct", "collection"]
CandidateResolutionStatus: TypeAlias = Literal["resolved", "safe_no_result"]
CandidateRoutingMode: TypeAlias = Literal["direct", "collection", "mixed", "none"]


class KnowledgeRuntimeCandidateConfigurationError(ValueError):
    """A fixed-code, non-retryable server-owned request error."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require_uuid(value: object, *, reason_code: str) -> UUID:
    if not isinstance(value, UUID):
        raise KnowledgeRuntimeCandidateConfigurationError(reason_code)
    return value


def _validated_uuid_tuple(
    values: object,
    *,
    reason_code: str,
    deduplicate: bool,
) -> tuple[UUID, ...]:
    if isinstance(values, (str, bytes)):
        raise KnowledgeRuntimeCandidateConfigurationError(reason_code)
    try:
        raw_values = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise KnowledgeRuntimeCandidateConfigurationError(reason_code) from exc

    result: list[UUID] = []
    seen: set[UUID] = set()
    for raw_value in raw_values:
        value = _require_uuid(raw_value, reason_code=reason_code)
        if deduplicate and value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class AuthenticatedAudience:
    organization_id: UUID
    user_id: UUID
    kind: Literal["authenticated"] = field(default="authenticated", init=False)

    def __post_init__(self) -> None:
        _require_uuid(self.organization_id, reason_code="audience_organization_invalid")
        _require_uuid(self.user_id, reason_code="audience_user_invalid")


@dataclass(frozen=True, slots=True)
class AnonymousPublicAudience:
    organization_id: UUID
    kind: Literal["anonymous_public"] = field(
        default="anonymous_public",
        init=False,
    )

    def __post_init__(self) -> None:
        _require_uuid(self.organization_id, reason_code="audience_organization_invalid")


KnowledgeRuntimeAudience: TypeAlias = AuthenticatedAudience | AnonymousPublicAudience


@dataclass(frozen=True, slots=True)
class KnowledgeRuntimeCandidateRequest:
    audience: KnowledgeRuntimeAudience
    direct_kb_ids: tuple[UUID, ...] = ()
    collection_ids: tuple[UUID, ...] = ()
    candidate_budget: int = DEFAULT_RUNTIME_CANDIDATE_BUDGET
    candidate_scan_cap: int = DEFAULT_RUNTIME_CANDIDATE_SCAN_CAP

    def __post_init__(self) -> None:
        if not isinstance(
            self.audience,
            (AuthenticatedAudience, AnonymousPublicAudience),
        ):
            raise KnowledgeRuntimeCandidateConfigurationError("audience_invalid")

        direct_kb_ids = _validated_uuid_tuple(
            self.direct_kb_ids,
            reason_code="direct_reference_invalid",
            deduplicate=True,
        )
        collection_ids = _validated_uuid_tuple(
            self.collection_ids,
            reason_code="collection_reference_invalid",
            deduplicate=True,
        )
        if len(direct_kb_ids) > MAX_RUNTIME_DIRECT_KB_REFERENCES:
            raise KnowledgeRuntimeCandidateConfigurationError(
                "direct_reference_limit_exceeded"
            )
        if len(collection_ids) > MAX_RUNTIME_COLLECTION_REFERENCES:
            raise KnowledgeRuntimeCandidateConfigurationError(
                "collection_reference_limit_exceeded"
            )
        if (
            not isinstance(self.candidate_budget, int)
            or isinstance(self.candidate_budget, bool)
            or not 1
            <= self.candidate_budget
            <= MAX_RUNTIME_CANDIDATE_BUDGET
        ):
            raise KnowledgeRuntimeCandidateConfigurationError(
                "candidate_budget_invalid"
            )
        if (
            not isinstance(self.candidate_scan_cap, int)
            or isinstance(self.candidate_scan_cap, bool)
            or not self.candidate_budget
            <= self.candidate_scan_cap
            <= MAX_RUNTIME_CANDIDATE_SCAN_CAP
        ):
            raise KnowledgeRuntimeCandidateConfigurationError(
                "candidate_scan_cap_invalid"
            )

        object.__setattr__(self, "direct_kb_ids", direct_kb_ids)
        object.__setattr__(self, "collection_ids", collection_ids)

    @property
    def organization_id(self) -> UUID:
        return self.audience.organization_id


@dataclass(frozen=True, slots=True)
class KnowledgeCollectionCandidateStream:
    collection_id: UUID
    eligible_kb_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        _require_uuid(
            self.collection_id,
            reason_code="snapshot_collection_reference_invalid",
        )
        object.__setattr__(
            self,
            "eligible_kb_ids",
            _validated_uuid_tuple(
                self.eligible_kb_ids,
                reason_code="snapshot_kb_reference_invalid",
                deduplicate=False,
            ),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeRuntimeCandidateSnapshot:
    """Authorized and ready facts loaded in one invocation snapshot."""

    eligible_direct_kb_ids: tuple[UUID, ...] = ()
    collection_streams: tuple[KnowledgeCollectionCandidateStream, ...] = ()
    policy_excluded_count: int = 0
    scan_limited: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "eligible_direct_kb_ids",
            _validated_uuid_tuple(
                self.eligible_direct_kb_ids,
                reason_code="snapshot_kb_reference_invalid",
                deduplicate=False,
            ),
        )
        if isinstance(self.collection_streams, (str, bytes)):
            raise ValueError("snapshot_collection_streams_invalid")
        try:
            streams = tuple(self.collection_streams)
        except TypeError as exc:
            raise ValueError("snapshot_collection_streams_invalid") from exc
        if not all(
            isinstance(stream, KnowledgeCollectionCandidateStream)
            for stream in streams
        ):
            raise ValueError("snapshot_collection_streams_invalid")
        if (
            not isinstance(self.policy_excluded_count, int)
            or isinstance(self.policy_excluded_count, bool)
            or self.policy_excluded_count < 0
        ):
            raise ValueError("snapshot_policy_excluded_count_invalid")
        if not isinstance(self.scan_limited, bool):
            raise ValueError("snapshot_scan_limited_invalid")
        object.__setattr__(self, "collection_streams", streams)


@dataclass(frozen=True, slots=True)
class KnowledgeRuntimeCandidateProvenance:
    kind: CandidateProvenanceKind
    collection_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.kind == "direct":
            if self.collection_id is not None:
                raise ValueError("direct_candidate_collection_invalid")
            return
        if self.kind == "collection":
            _require_uuid(
                self.collection_id,
                reason_code="candidate_collection_reference_invalid",
            )
            return
        raise ValueError("candidate_provenance_invalid")


@dataclass(frozen=True, slots=True)
class KnowledgeRuntimeCandidate:
    knowledge_base_id: UUID
    provenance: KnowledgeRuntimeCandidateProvenance
    provenances: tuple[KnowledgeRuntimeCandidateProvenance, ...] = ()

    def __post_init__(self) -> None:
        _require_uuid(
            self.knowledge_base_id,
            reason_code="candidate_kb_reference_invalid",
        )
        provenances = self.provenances or (self.provenance,)
        if not all(
            isinstance(item, KnowledgeRuntimeCandidateProvenance)
            for item in provenances
        ):
            raise ValueError("candidate_provenance_invalid")
        object.__setattr__(self, "provenances", tuple(dict.fromkeys(provenances)))


@dataclass(frozen=True, slots=True)
class KnowledgeRuntimeCandidateResolution:
    status: CandidateResolutionStatus
    candidates: tuple[KnowledgeRuntimeCandidate, ...]
    routing_mode: CandidateRoutingMode
    configured_direct_count_bucket: str
    configured_collection_count_bucket: str
    eligible_candidate_count_bucket: str
    selected_candidate_count_bucket: str
    policy_excluded_count_bucket: str
    budget_limited: bool
    scan_limited: bool
    warning_codes: tuple[str, ...]
    reason_code: str | None = None


def bucket_safe_count(value: int) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("safe_count_invalid")
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    if value <= 10:
        return "2-10"
    if value <= 100:
        return "11-100"
    return "100+"


def _ordered_collection_streams(
    request: KnowledgeRuntimeCandidateRequest,
    snapshot: KnowledgeRuntimeCandidateSnapshot,
) -> tuple[KnowledgeCollectionCandidateStream, ...]:
    selected_collection_ids = set(request.collection_ids)
    first_stream_by_collection: dict[UUID, KnowledgeCollectionCandidateStream] = {}
    for stream in snapshot.collection_streams:
        if stream.collection_id not in selected_collection_ids:
            continue
        first_stream_by_collection.setdefault(stream.collection_id, stream)
    return tuple(
        first_stream_by_collection[collection_id]
        for collection_id in request.collection_ids
        if collection_id in first_stream_by_collection
    )


def _all_ordered_unique_candidates(
    request: KnowledgeRuntimeCandidateRequest,
    snapshot: KnowledgeRuntimeCandidateSnapshot,
) -> list[KnowledgeRuntimeCandidate]:
    ordered: list[KnowledgeRuntimeCandidate] = []
    seen: set[UUID] = set()
    provenance_by_kb_id: dict[
        UUID, list[KnowledgeRuntimeCandidateProvenance]
    ] = {}

    def record(
        knowledge_base_id: UUID,
        provenance: KnowledgeRuntimeCandidateProvenance,
    ) -> None:
        values = provenance_by_kb_id.setdefault(knowledge_base_id, [])
        if provenance not in values:
            values.append(provenance)

    eligible_direct_ids = set(snapshot.eligible_direct_kb_ids)
    for knowledge_base_id in request.direct_kb_ids:
        if knowledge_base_id not in eligible_direct_ids or knowledge_base_id in seen:
            if knowledge_base_id in eligible_direct_ids:
                record(
                    knowledge_base_id,
                    KnowledgeRuntimeCandidateProvenance(kind="direct"),
                )
            continue
        direct_provenance = KnowledgeRuntimeCandidateProvenance(kind="direct")
        record(knowledge_base_id, direct_provenance)
        seen.add(knowledge_base_id)
        ordered.append(
            KnowledgeRuntimeCandidate(
                knowledge_base_id=knowledge_base_id,
                provenance=direct_provenance,
            )
        )

    streams = _ordered_collection_streams(request, snapshot)
    stream_indexes = [0 for _stream in streams]
    while streams:
        added_in_round = False
        remaining = False
        for stream_index, stream in enumerate(streams):
            item_index = stream_indexes[stream_index]
            while item_index < len(stream.eligible_kb_ids):
                remaining = True
                knowledge_base_id = stream.eligible_kb_ids[item_index]
                item_index += 1
                stream_indexes[stream_index] = item_index
                collection_provenance = KnowledgeRuntimeCandidateProvenance(
                    kind="collection",
                    collection_id=stream.collection_id,
                )
                record(knowledge_base_id, collection_provenance)
                if knowledge_base_id in seen:
                    continue
                seen.add(knowledge_base_id)
                ordered.append(
                    KnowledgeRuntimeCandidate(
                        knowledge_base_id=knowledge_base_id,
                        provenance=collection_provenance,
                    )
                )
                added_in_round = True
                break
        if not remaining or not added_in_round:
            break
    return [
        KnowledgeRuntimeCandidate(
            knowledge_base_id=candidate.knowledge_base_id,
            provenance=candidate.provenance,
            provenances=tuple(provenance_by_kb_id[candidate.knowledge_base_id]),
        )
        for candidate in ordered
    ]


def _routing_mode(
    candidates: tuple[KnowledgeRuntimeCandidate, ...],
) -> CandidateRoutingMode:
    kinds = {
        provenance.kind
        for candidate in candidates
        for provenance in candidate.provenances
    }
    if kinds == {"direct"}:
        return "direct"
    if kinds == {"collection"}:
        return "collection"
    if kinds == {"direct", "collection"}:
        return "mixed"
    return "none"


def resolve_knowledge_runtime_candidates(
    request: KnowledgeRuntimeCandidateRequest,
    snapshot: KnowledgeRuntimeCandidateSnapshot,
) -> KnowledgeRuntimeCandidateResolution:
    if not isinstance(request, KnowledgeRuntimeCandidateRequest):
        raise KnowledgeRuntimeCandidateConfigurationError("request_invalid")
    if not isinstance(snapshot, KnowledgeRuntimeCandidateSnapshot):
        raise ValueError("snapshot_invalid")

    ordered_candidates = _all_ordered_unique_candidates(request, snapshot)
    candidates = tuple(ordered_candidates[: request.candidate_budget])
    budget_limited = len(ordered_candidates) > request.candidate_budget
    warning_codes: list[str] = []
    if budget_limited:
        warning_codes.append("candidate_budget_limited")
    if snapshot.scan_limited:
        warning_codes.append("candidate_scan_limited")

    status: CandidateResolutionStatus = "resolved" if candidates else "safe_no_result"
    return KnowledgeRuntimeCandidateResolution(
        status=status,
        candidates=candidates,
        routing_mode=_routing_mode(candidates),
        configured_direct_count_bucket=bucket_safe_count(len(request.direct_kb_ids)),
        configured_collection_count_bucket=bucket_safe_count(
            len(request.collection_ids)
        ),
        eligible_candidate_count_bucket=bucket_safe_count(len(ordered_candidates)),
        selected_candidate_count_bucket=bucket_safe_count(len(candidates)),
        policy_excluded_count_bucket=bucket_safe_count(
            snapshot.policy_excluded_count
        ),
        budget_limited=budget_limited,
        scan_limited=snapshot.scan_limited,
        warning_codes=tuple(warning_codes),
        reason_code=(
            None if candidates else "knowledge_candidates.safe_no_result"
        ),
    )


__all__ = [
    "AnonymousPublicAudience",
    "AuthenticatedAudience",
    "DEFAULT_RUNTIME_CANDIDATE_BUDGET",
    "DEFAULT_RUNTIME_CANDIDATE_SCAN_CAP",
    "KnowledgeCollectionCandidateStream",
    "KnowledgeRuntimeAudience",
    "KnowledgeRuntimeCandidate",
    "KnowledgeRuntimeCandidateConfigurationError",
    "KnowledgeRuntimeCandidateProvenance",
    "KnowledgeRuntimeCandidateRequest",
    "KnowledgeRuntimeCandidateResolution",
    "KnowledgeRuntimeCandidateSnapshot",
    "MAX_RUNTIME_CANDIDATE_BUDGET",
    "MAX_RUNTIME_CANDIDATE_SCAN_CAP",
    "MAX_RUNTIME_COLLECTION_REFERENCES",
    "MAX_RUNTIME_DIRECT_KB_REFERENCES",
    "bucket_safe_count",
    "resolve_knowledge_runtime_candidates",
]
