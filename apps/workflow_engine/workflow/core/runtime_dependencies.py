"""In-memory dependencies composed for one Workflow Engine instance."""

from __future__ import annotations

from dataclasses import dataclass

from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionRuntime,
)
from apps.workflow_engine.application.provider_usage import ProviderUsageRecorder
from apps.workflow_engine.application.query_embedding_execution import (
    QueryEmbeddingExecutionRuntime,
)
from apps.workflow_engine.application.runtime_retrieval.knowledge_candidates import (
    KnowledgeRuntimeCandidateResolver,
)
from apps.workflow_engine.application.remote_file import RemoteFileFetcher


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeDependencies:
    """Dependencies that must never be copied into a queued execution context."""

    knowledge_runtime_candidate_resolver: KnowledgeRuntimeCandidateResolver | None = (
        None
    )
    provider_execution_runtime: ProviderExecutionRuntime | None = None
    provider_usage_recorder: ProviderUsageRecorder | None = None
    query_embedding_runtime: QueryEmbeddingExecutionRuntime | None = None
    remote_file_fetcher: RemoteFileFetcher | None = None


__all__ = ["WorkflowRuntimeDependencies"]
