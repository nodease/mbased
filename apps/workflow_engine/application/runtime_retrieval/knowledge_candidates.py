"""Application boundary for runtime Knowledge candidate resolution."""

from __future__ import annotations

from typing import Protocol

from apps.shared.domain.knowledge_runtime_candidates import (
    KnowledgeRuntimeCandidateConfigurationError,
    KnowledgeRuntimeCandidateRequest,
    KnowledgeRuntimeCandidateResolution,
    KnowledgeRuntimeCandidateSnapshot,
    resolve_knowledge_runtime_candidates,
)


class KnowledgeRuntimeCandidateSnapshotPort(Protocol):
    """Loads authorized and ready facts within one invocation snapshot."""

    def load_snapshot(
        self,
        request: KnowledgeRuntimeCandidateRequest,
    ) -> KnowledgeRuntimeCandidateSnapshot: ...


class KnowledgeRuntimeCandidateInfrastructureError(RuntimeError):
    """Sanitized retryable failure raised before retrieval/provider effects."""

    def __init__(
        self,
        reason_code: str = "knowledge_candidate_resolver_unavailable",
    ) -> None:
        self.reason_code = reason_code
        self.retryable = True
        super().__init__(reason_code)


class KnowledgeRuntimeCandidateResolver:
    def __init__(
        self,
        *,
        snapshot_port: KnowledgeRuntimeCandidateSnapshotPort,
    ) -> None:
        self._snapshot_port = snapshot_port

    def resolve(
        self,
        request: KnowledgeRuntimeCandidateRequest,
    ) -> KnowledgeRuntimeCandidateResolution:
        if not isinstance(request, KnowledgeRuntimeCandidateRequest):
            raise KnowledgeRuntimeCandidateConfigurationError("request_invalid")

        failed = False
        result: KnowledgeRuntimeCandidateResolution | None = None
        try:
            snapshot = self._snapshot_port.load_snapshot(request)
            result = resolve_knowledge_runtime_candidates(request, snapshot)
        except Exception:
            # Do not retain a raw DB/source exception as public exception context.
            # Concrete observability, if added, must map it to a separate safe code.
            failed = True

        if failed or result is None:
            raise KnowledgeRuntimeCandidateInfrastructureError()
        return result


__all__ = [
    "KnowledgeRuntimeCandidateInfrastructureError",
    "KnowledgeRuntimeCandidateResolver",
    "KnowledgeRuntimeCandidateSnapshotPort",
]
