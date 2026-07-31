from dataclasses import dataclass
from typing import Any

from apps.shared.db.models.knowledge import KnowledgeBase, RAGAnswerRun
from apps.shared.db.models.llm import LLMCredential, LLMModel
from apps.shared.schemas.rag import RAGAgentAnswerRequest


@dataclass
class RAGAnswerResolvedContext:
    """Run 생성 전 visibility/preflight가 통과한 실행 컨텍스트."""

    payload: RAGAgentAnswerRequest
    correlation_id: str
    metadata_filter: Any
    kb: KnowledgeBase
    model: LLMModel
    credential: LLMCredential


@dataclass
class RAGAnswerExecution(RAGAnswerResolvedContext):
    """Run 생성 후 retrieval/generation 단계에서 사용하는 실행 컨텍스트."""

    run: RAGAnswerRun
    embedding_model: LLMModel | None = None
