"""Composition boundary for Workflow runtime retrieval services."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from apps.workflow_engine.adapters.knowledge_runtime_candidates import (
    PostgresKnowledgeRuntimeCandidateSnapshotAdapter,
)
from apps.workflow_engine.application.runtime_retrieval.knowledge_candidates import (
    KnowledgeRuntimeCandidateResolver,
)


def build_knowledge_runtime_candidate_resolver(
    *,
    session_factory: Callable[[], Session] | None = None,
) -> KnowledgeRuntimeCandidateResolver:
    if session_factory is None:
        from apps.shared.db.session import SessionLocal

        session_factory = SessionLocal

    return KnowledgeRuntimeCandidateResolver(
        snapshot_port=PostgresKnowledgeRuntimeCandidateSnapshotAdapter(
            session_factory=session_factory
        )
    )


__all__ = ["build_knowledge_runtime_candidate_resolver"]
