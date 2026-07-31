import re
import uuid
from typing import Any

from apps.shared.db.models.llm import LLMModel
from apps.shared.schemas.rag import RAGCitation, RAGRetrievalSummary
from apps.shared.services.tracing.policy import TracePolicyService
from apps.shared.services.tracing.redaction import TraceRedactionService

CONTENT_PREVIEW_LIMIT = 300
CONTEXT_TOKEN_BUDGET = 8000
MAX_OUTPUT_TOKENS = 1000
PROMPT_OVERHEAD_TOKENS = 500

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_API_KEY_RE = re.compile(r"\b(?:sk|pk|rk|api)[-_][A-Za-z0-9_-]{8,}\b")
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b", re.IGNORECASE)


class RAGAgentAnswerBuilder:
    """RAG Agent answer의 응답 summary와 user-facing preview를 구성한다."""

    def build_citations(self, chunks) -> list[RAGCitation]:
        citations: list[RAGCitation] = []
        for index, chunk in enumerate(chunks, start=1):
            citation_id = f"c{index}"
            metadata_summary = dict(chunk.metadata_summary or {})
            citations.append(
                RAGCitation(
                    citation_id=citation_id,
                    document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    rank=chunk.rank or index,
                    score=(
                        chunk.score
                        if chunk.score is not None
                        else chunk.similarity_score
                    ),
                    filename=chunk.filename,
                    heading=metadata_summary.get("heading"),
                    hierarchy_path=chunk.hierarchy_path,
                    metadata_summary=metadata_summary,
                    content_preview=self.content_preview(chunk.content),
                )
            )
        return citations

    @staticmethod
    def build_retrieval_summary(
        knowledge_base_id: uuid.UUID,
        hierarchy_mode: str,
        citations: list[RAGCitation],
        latency_ms: int,
    ) -> RAGRetrievalSummary:
        scores = [c.score for c in citations if c.score is not None]
        score_summary = {}
        if scores:
            score_summary = {
                "min": min(scores),
                "max": max(scores),
                "avg": sum(scores) / len(scores),
            }
        document_ids = list({c.document_id for c in citations})
        return RAGRetrievalSummary(
            knowledge_base_id=knowledge_base_id,
            hierarchy_mode=hierarchy_mode,
            retrieved_chunk_count=len(citations),
            document_ids=document_ids,
            citation_ids=[c.citation_id for c in citations],
            score_summary=score_summary,
            latency_ms=latency_ms,
            raw_content_returned=False,
        )

    @staticmethod
    def contains_pii_evidence(citations: list[RAGCitation]) -> bool:
        for citation in citations:
            classification = citation.metadata_summary.get("classification")
            if str(classification).lower() == "pii":
                return True
        return False

    @staticmethod
    def allowed_policy_result(citations: list[RAGCitation]) -> dict[str, Any]:
        classifications = sorted(
            {
                str(citation.metadata_summary.get("classification")).lower()
                for citation in citations
                if citation.metadata_summary.get("classification")
            }
        )
        result: dict[str, Any] = {"result": "allow"}
        if classifications:
            result["evidence_classifications"] = classifications
        return result

    def context_for_chunks(self, chunks, model: LLMModel | None = None) -> str:
        context_budget = self.context_budget_for_model(model)
        parts: list[str] = []
        total_tokens = 0
        for chunk in chunks:
            content = chunk.content or ""
            token_count = chunk.token_count or max(1, len(content) // 4)
            if total_tokens and total_tokens + token_count > context_budget:
                break
            if token_count > context_budget:
                parts.append(content[: context_budget * 4])
                break
            parts.append(content)
            total_tokens += token_count
        return "\n\n".join(parts)

    def context_budget_for_model(self, model: LLMModel | None) -> int:
        context_window = getattr(model, "context_window", None)
        if not isinstance(context_window, int) or context_window <= 0:
            return CONTEXT_TOKEN_BUDGET

        reserved_output_tokens = self.output_token_budget_for_model(model)
        prompt_overhead_tokens = self.prompt_overhead_for_context_window(
            context_window
        )
        available_tokens = (
            context_window - reserved_output_tokens - prompt_overhead_tokens
        )
        return max(1, min(CONTEXT_TOKEN_BUDGET, available_tokens))

    def output_token_budget_for_model(self, model: LLMModel | None) -> int:
        context_window = getattr(model, "context_window", None)
        if not isinstance(context_window, int) or context_window <= 0:
            return MAX_OUTPUT_TOKENS

        prompt_overhead_tokens = self.prompt_overhead_for_context_window(
            context_window
        )
        max_output_for_model = max(1, context_window - prompt_overhead_tokens - 1)
        preferred_output_tokens = max(256, context_window // 4)
        return max(
            1,
            min(
                MAX_OUTPUT_TOKENS,
                preferred_output_tokens,
                max_output_for_model,
            ),
        )

    @staticmethod
    def prompt_overhead_for_context_window(context_window: int) -> int:
        return min(
            PROMPT_OVERHEAD_TOKENS,
            max(128, context_window // 8),
        )

    @staticmethod
    def content_preview(content: str) -> str:
        normalized = " ".join((content or "").split())
        redaction = TraceRedactionService.redact_payload(
            normalized,
            TracePolicyService.bootstrap_redaction_policy(),
            payload_kind="rag.citation_preview",
        )
        redacted = (
            redaction.redacted_payload
            if isinstance(redaction.redacted_payload, str)
            else normalized
        )
        redacted = _EMAIL_RE.sub("[redacted-email]", redacted)
        redacted = _API_KEY_RE.sub("[redacted-secret]", redacted)
        redacted = _BEARER_RE.sub("Bearer [redacted-secret]", redacted)
        return redacted[:CONTENT_PREVIEW_LIMIT]
