from uuid import UUID

import pytest

from apps.shared.domain.knowledge_runtime_candidates import (
    AuthenticatedAudience,
    KnowledgeRuntimeCandidateConfigurationError,
    KnowledgeRuntimeCandidateRequest,
    KnowledgeRuntimeCandidateSnapshot,
)
from apps.workflow_engine.application.runtime_retrieval.knowledge_candidates import (
    KnowledgeRuntimeCandidateInfrastructureError,
    KnowledgeRuntimeCandidateResolver,
)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _request() -> KnowledgeRuntimeCandidateRequest:
    return KnowledgeRuntimeCandidateRequest(
        audience=AuthenticatedAudience(
            organization_id=_id(100),
            user_id=_id(101),
        ),
        direct_kb_ids=(_id(1),),
    )


class _SnapshotPort:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot or KnowledgeRuntimeCandidateSnapshot()
        self.error = error
        self.requests = []

    def load_snapshot(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.snapshot


def test_resolver_loads_exactly_one_snapshot_and_applies_pure_policy():
    request = _request()
    port = _SnapshotPort(
        KnowledgeRuntimeCandidateSnapshot(
            eligible_direct_kb_ids=(_id(1),),
        )
    )
    resolver = KnowledgeRuntimeCandidateResolver(snapshot_port=port)

    result = resolver.resolve(request)

    assert port.requests == [request]
    assert result.status == "resolved"
    assert [candidate.knowledge_base_id for candidate in result.candidates] == [
        _id(1)
    ]


def test_resolver_rejects_wrong_request_before_opening_snapshot():
    port = _SnapshotPort()
    resolver = KnowledgeRuntimeCandidateResolver(snapshot_port=port)

    with pytest.raises(KnowledgeRuntimeCandidateConfigurationError) as exc_info:
        resolver.resolve(object())  # type: ignore[arg-type]

    assert exc_info.value.reason_code == "request_invalid"
    assert port.requests == []


def test_port_failure_is_sanitized_and_drops_raw_exception_context():
    raw_marker = "sensitive-source-marker"
    port = _SnapshotPort(error=RuntimeError(raw_marker))
    resolver = KnowledgeRuntimeCandidateResolver(snapshot_port=port)

    with pytest.raises(KnowledgeRuntimeCandidateInfrastructureError) as exc_info:
        resolver.resolve(_request())

    error = exc_info.value
    assert error.reason_code == "knowledge_candidate_resolver_unavailable"
    assert error.retryable is True
    assert str(error) == "knowledge_candidate_resolver_unavailable"
    assert raw_marker not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_invalid_snapshot_projection_is_whole_resolution_failure():
    class _InvalidSnapshotPort:
        def load_snapshot(self, request):
            del request
            return object()

    resolver = KnowledgeRuntimeCandidateResolver(snapshot_port=_InvalidSnapshotPort())

    with pytest.raises(KnowledgeRuntimeCandidateInfrastructureError):
        resolver.resolve(_request())


def test_policy_exclusion_is_safe_no_result_not_infrastructure_failure():
    resolver = KnowledgeRuntimeCandidateResolver(
        snapshot_port=_SnapshotPort(KnowledgeRuntimeCandidateSnapshot())
    )

    result = resolver.resolve(_request())

    assert result.status == "safe_no_result"
    assert result.reason_code == "knowledge_candidates.safe_no_result"
