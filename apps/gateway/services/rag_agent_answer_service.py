import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from apps.gateway.services.rag_agent_answer_audit import RAGAgentAnswerAuditRecorder
from apps.gateway.services.rag_agent_answer_builder import RAGAgentAnswerBuilder
from apps.gateway.services.rag_agent_answer_constants import (
    MAX_TOP_K,
    MIN_TOP_K,
    RETRIEVAL_TIMEOUT_SECONDS,
)
from apps.gateway.services.rag_agent_answer_generation import (
    RAGAgentAnswerGenerationRunner,
)
from apps.gateway.services.rag_agent_answer_lifecycle import RAGAgentAnswerLifecycle
from apps.gateway.services.rag_agent_answer_preflight import (
    RAGAgentAnswerPreflightResolver,
)
from apps.gateway.services.rag_agent_answer_types import (
    RAGAnswerExecution,
    RAGAnswerResolvedContext,
)
from apps.gateway.services.retrieval import RetrievalService
from apps.gateway.utils.api_errors import error_detail, raise_api_error
from apps.shared.db.models.knowledge import KnowledgeBase, RAGAnswerRun
from apps.shared.db.models.llm import LLMCredential, LLMModel
from apps.shared.db.models.user import User
from apps.shared.schemas.rag import (
    RAGAgentAnswerRequest,
    RAGAgentAnswerResponse,
    RAGAnswerSummary,
    RAGCitation,
    RAGRetrievalSummary,
    RAGUsageSummary,
)
from apps.shared.services.rag_evidence_policy import RAGEvidencePolicy

__all__ = [
    "MAX_TOP_K",
    "MIN_TOP_K",
    "RAGAgentAnswerService",
    "RAGAnswerExecution",
    "RAGAnswerResolvedContext",
]

logger = logging.getLogger(__name__)


class RAGAgentAnswerService:
    """RAG Agent answer 실행을 조립하는 얇은 orchestration 계층."""

    def __init__(
        self,
        db: Session,
        current_user: User,
        request: Request,
        organization_id: uuid.UUID,
    ) -> None:
        self.db = db
        self.current_user = current_user
        self.request = request
        self.organization_id = organization_id
        self.builder = RAGAgentAnswerBuilder()
        self.audit = RAGAgentAnswerAuditRecorder(
            db,
            user_id=current_user.id,
            organization_id=organization_id,
        )
        self.lifecycle = RAGAgentAnswerLifecycle(
            db,
            current_user=current_user,
            organization_id=organization_id,
            audit=self.audit,
        )
        self.preflight = RAGAgentAnswerPreflightResolver(
            db,
            current_user=current_user,
            request=request,
            organization_id=organization_id,
            lifecycle=self.lifecycle,
        )
        self.generation = RAGAgentAnswerGenerationRunner(
            db,
            builder=self.builder,
            audit=self.audit,
        )
        self.evidence_policy = RAGEvidencePolicy()

    async def answer(self, payload: RAGAgentAnswerRequest) -> RAGAgentAnswerResponse:
        execution = self.prepare_execution(payload)
        run = execution.run
        timeout_stage: str | None = None

        try:
            timeout_stage = "retrieval"
            chunks, citations, retrieval_summary = await self._retrieve_and_summarize(
                execution
            )
            timeout_stage = None
            self._raise_policy_block_if_needed(run, retrieval_summary, citations)

            if self._can_generate_answer(chunks, retrieval_summary):
                timeout_stage = "generation"
                answer, usage_summary = await self.generation.generate(
                    execution.payload,
                    chunks,
                    execution.model,
                    execution.credential,
                    run,
                )
                timeout_stage = None
            else:
                answer = self._insufficient_evidence_answer(retrieval_summary)
                usage_summary = RAGUsageSummary(
                    model_name=execution.model.name,
                    provider=execution.model.provider_name,
                )

            _answer_summary, policy_result = self._complete_success(
                run,
                answer=answer,
                citations=citations,
                retrieval_summary=retrieval_summary,
                usage_summary=usage_summary,
                model=execution.model,
                credential=execution.credential,
            )
            return RAGAgentAnswerResponse(
                answer_run_id=run.id,
                correlation_id=run.correlation_id,
                status=run.status,
                answer=answer,
                citations=citations,
                retrieval_summary=retrieval_summary,
                usage_summary=usage_summary,
                policy_result=policy_result,
            )
        except asyncio.CancelledError:
            self.lifecycle.cancel_if_open(run)
            raise
        except asyncio.TimeoutError:
            if timeout_stage == "generation":
                logger.warning("RAG Agent answer provider timeout")
                self.lifecycle.fail(run, "provider.timeout")
                raise_api_error(
                    self.request,
                    504,
                    "provider.timeout",
                    "RAG answer provider timeout.",
                    {
                        "answer_run_id": str(run.id),
                        "correlation_id": run.correlation_id,
                    },
                )
            logger.warning("RAG Agent answer retrieval timeout")
            self.lifecycle.fail(run, "generation.failed")
            raise_api_error(
                self.request,
                500,
                "generation.failed",
                "RAG answer generation failed.",
                {"answer_run_id": str(run.id), "correlation_id": run.correlation_id},
            )
        except ValueError as exc:
            if str(exc) == "hierarchy_unavailable":
                self.lifecycle.fail(run, "hierarchy_unavailable")
                raise_api_error(
                    self.request,
                    422,
                    "hierarchy_unavailable",
                    "Hierarchical retrieval data is not available for this Knowledge Base.",
                    {
                        "answer_run_id": str(run.id),
                        "correlation_id": run.correlation_id,
                    },
                )
            self.lifecycle.fail(run, "generation.failed")
            raise_api_error(
                self.request,
                500,
                "generation.failed",
                "RAG answer generation failed.",
                {"answer_run_id": str(run.id), "correlation_id": run.correlation_id},
            )
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - service boundary에서 상태와 audit을 닫는다
            logger.exception("RAG Agent answer failed")
            self.lifecycle.fail(run, "generation.failed")
            raise_api_error(
                self.request,
                500,
                "generation.failed",
                "RAG answer generation failed.",
                {"answer_run_id": str(run.id), "correlation_id": run.correlation_id},
            )

    def prepare_execution(self, payload: RAGAgentAnswerRequest) -> RAGAnswerExecution:
        resolved = self.preflight.resolve_visible_context(payload)
        run = self.lifecycle.create_requested(resolved)
        try:
            embedding_model = self.preflight.enforce_post_create_preflight(
                run, resolved
            )
            self.lifecycle.mark_running(run)
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - run 생성 후 preflight는 상태를 닫는다
            logger.exception("RAG Agent answer preflight failed")
            self.lifecycle.mark_preflight_failed(run)
            raise_api_error(
                self.request,
                500,
                "generation.failed",
                "RAG answer preflight failed.",
                {"answer_run_id": str(run.id), "correlation_id": run.correlation_id},
            )
        return RAGAnswerExecution(
            payload=resolved.payload,
            correlation_id=resolved.correlation_id,
            metadata_filter=resolved.metadata_filter,
            kb=resolved.kb,
            model=resolved.model,
            credential=resolved.credential,
            run=run,
            embedding_model=embedding_model,
        )

    async def stream_events(
        self, execution: RAGAnswerExecution
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        run = execution.run
        timeout_stage: str | None = None
        try:
            yield (
                "retrieval.started",
                {
                    "answer_run_id": str(run.id),
                    "correlation_id": run.correlation_id,
                    "status": "running",
                    "knowledge_base_id": str(execution.kb.id),
                    "hierarchy_mode": execution.payload.hierarchy_mode,
                },
            )

            timeout_stage = "retrieval"
            chunks, citations, retrieval_summary = await self._retrieve_and_summarize(
                execution
            )
            timeout_stage = None
            try:
                self._raise_policy_block_if_needed(run, retrieval_summary, citations)
            except HTTPException:
                yield self._error_event(run, "pii_policy_blocked", retryable=False)
                return

            yield (
                "retrieval.completed",
                {
                    "answer_run_id": str(run.id),
                    "correlation_id": run.correlation_id,
                    "retrieval_summary": retrieval_summary.model_dump(mode="json"),
                    "citations": [c.model_dump(mode="json") for c in citations],
                },
            )

            if self._can_generate_answer(chunks, retrieval_summary):
                timeout_stage = "generation"
                answer, usage_summary = await self.generation.generate(
                    execution.payload,
                    chunks,
                    execution.model,
                    execution.credential,
                    run,
                )
                timeout_stage = None
            else:
                answer = self._insufficient_evidence_answer(retrieval_summary)
                usage_summary = RAGUsageSummary(
                    model_name=execution.model.name,
                    provider=execution.model.provider_name,
                )

            answer_summary, policy_result = self._complete_success(
                run,
                answer=answer,
                citations=citations,
                retrieval_summary=retrieval_summary,
                usage_summary=usage_summary,
                model=execution.model,
                credential=execution.credential,
            )

            if answer:
                yield (
                    "answer.delta",
                    {
                        "answer_run_id": str(run.id),
                        "correlation_id": run.correlation_id,
                        "delta": answer,
                        "index": 0,
                    },
                )
            yield (
                "usage",
                {
                    "answer_run_id": str(run.id),
                    "correlation_id": run.correlation_id,
                    "usage_summary": usage_summary.model_dump(mode="json"),
                },
            )
            yield (
                "summary",
                {
                    "answer_run_id": str(run.id),
                    "correlation_id": run.correlation_id,
                    "status": run.status,
                    "retrieval_summary": retrieval_summary.model_dump(mode="json"),
                    "citation_summary": [
                        c.model_dump(mode="json", exclude={"content_preview"})
                        for c in citations
                    ],
                    "answer_summary": answer_summary.model_dump(mode="json"),
                    "policy_result": policy_result,
                },
            )
            yield (
                "answer.completed",
                {
                    "answer_run_id": str(run.id),
                    "correlation_id": run.correlation_id,
                    "status": run.status,
                },
            )
        except asyncio.CancelledError:
            self.lifecycle.cancel_if_open(run)
            raise
        except asyncio.TimeoutError:
            error_code = (
                "provider.timeout"
                if timeout_stage == "generation"
                else "stream.timeout"
            )
            self.lifecycle.fail(run, error_code)
            yield self._error_event(run, error_code, retryable=True)
        except ValueError as exc:
            error_code = (
                "hierarchy_unavailable"
                if str(exc) == "hierarchy_unavailable"
                else "generation.failed"
            )
            self.lifecycle.fail(run, error_code)
            yield self._error_event(
                run, error_code, retryable=error_code != "hierarchy_unavailable"
            )
        except HTTPException as exc:
            reason_code = self._reason_code_from_http_exception(exc)
            yield self._error_event(run, reason_code, retryable=False)
        except Exception:
            logger.exception("RAG Agent answer stream failed")
            self.lifecycle.fail(run, "generation.failed")
            yield self._error_event(run, "generation.failed", retryable=True)
        finally:
            self.lifecycle.cancel_if_open(run)

    async def _retrieve_and_summarize(
        self, execution: RAGAnswerExecution
    ) -> tuple[list[Any], list[RAGCitation], RAGRetrievalSummary]:
        retrieval_start = time.perf_counter()
        retrieval_service = RetrievalService(
            self.db,
            user_id=self.current_user.id,
            organization_id=self.organization_id,
        )
        chunks = await self._retrieve_chunks(
            retrieval_service,
            execution.payload,
            execution.kb,
            execution.metadata_filter,
            execution.embedding_model,
        )
        retrieval_latency_ms = int((time.perf_counter() - retrieval_start) * 1000)
        citations = self.builder.build_citations(chunks)
        retrieval_summary = self.builder.build_retrieval_summary(
            execution.kb.id,
            execution.payload.hierarchy_mode,
            citations,
            retrieval_latency_ms,
        )
        evidence_decision = self.evidence_policy.evaluate(
            citations,
            policy=execution.payload.evidence_sufficiency_policy,
        )
        retrieval_summary.evidence_sufficient = evidence_decision.evidence_sufficient
        retrieval_summary.insufficiency_reason = (
            evidence_decision.insufficiency_reason
        )
        retrieval_summary.source_tier_used = evidence_decision.source_tier_used
        self.audit.record_retrieval(
            execution.run,
            execution.metadata_filter,
            len(citations),
            execution.payload.hierarchy_mode,
        )
        return chunks, citations, retrieval_summary

    async def _retrieve_chunks(
        self,
        retrieval_service: RetrievalService,
        payload: RAGAgentAnswerRequest,
        kb: KnowledgeBase,
        metadata_filter,
        embedding_model: LLMModel | None,
    ):
        return await asyncio.wait_for(
            retrieval_service.search_documents(
                payload.query,
                knowledge_base_id=str(kb.id),
                top_k=payload.top_k,
                metadata_filter=metadata_filter,
                hierarchy_mode=payload.hierarchy_mode,
                embedding_model=embedding_model,
            ),
            timeout=RETRIEVAL_TIMEOUT_SECONDS,
        )

    def _complete_success(
        self,
        run: RAGAnswerRun,
        *,
        answer: str,
        citations: list[RAGCitation],
        retrieval_summary: RAGRetrievalSummary,
        usage_summary: RAGUsageSummary,
        model: LLMModel,
        credential: LLMCredential,
    ) -> tuple[RAGAnswerSummary, dict[str, Any]]:
        policy_result = self.builder.allowed_policy_result(citations)
        answer_summary = RAGAnswerSummary(
            answer_length=len(answer),
            cited_document_count=len({str(c.document_id) for c in citations}),
            citation_ids=[c.citation_id for c in citations],
            policy_result=policy_result,
            completion_status=self._completion_status(retrieval_summary),
        )
        self.lifecycle.complete(
            run,
            retrieval_summary=retrieval_summary,
            citations=citations,
            answer_summary=answer_summary,
            policy_result=policy_result,
            durable_usage_summary=self._durable_usage_summary(
                usage_summary, model, credential
            ),
        )
        return answer_summary, policy_result

    @staticmethod
    def _can_generate_answer(chunks: list[Any], retrieval_summary: RAGRetrievalSummary) -> bool:
        return bool(chunks) and retrieval_summary.evidence_sufficient

    @staticmethod
    def _completion_status(retrieval_summary: RAGRetrievalSummary) -> str:
        if retrieval_summary.evidence_sufficient:
            return "completed"
        if retrieval_summary.insufficiency_reason == "no_evidence":
            return "no_result"
        return "insufficient_evidence"

    @staticmethod
    def _insufficient_evidence_answer(
        retrieval_summary: RAGRetrievalSummary,
    ) -> str:
        if retrieval_summary.insufficiency_reason == "no_evidence":
            return "해당 질문에 답변할 수 있는 문서를 찾지 못했습니다."
        return "확인된 문서 기준으로는 답변 근거가 부족합니다."

    def _raise_policy_block_if_needed(
        self,
        run: RAGAnswerRun,
        retrieval_summary: RAGRetrievalSummary,
        citations: list[RAGCitation],
    ) -> None:
        if not self.builder.contains_pii_evidence(citations):
            return
        reason_code = "pii_policy_blocked"
        policy_result = {"result": "block", "reason_code": reason_code}
        self.lifecycle.block_policy(
            run,
            retrieval_summary=retrieval_summary,
            citations=citations,
            policy_result=policy_result,
        )
        exc = HTTPException(
            status_code=403,
            detail=error_detail(
                self.request,
                "policy.blocked",
                "RAG answer blocked by policy.",
                self._blocked_details(run, reason_code),
            ),
        )
        setattr(exc, "audit_recorded", True)
        raise exc

    @staticmethod
    def _blocked_details(run: RAGAnswerRun, reason_code: str) -> dict[str, str]:
        return {
            "answer_run_id": str(run.id),
            "correlation_id": run.correlation_id,
            "status": "blocked",
            "reason_code": reason_code,
        }

    @staticmethod
    def _error_event(
        run: RAGAnswerRun, reason_code: str, *, retryable: bool
    ) -> tuple[str, dict[str, Any]]:
        return (
            "error",
            {
                "answer_run_id": str(run.id),
                "correlation_id": run.correlation_id,
                "status": run.status,
                "reason_code": reason_code,
                "retryable": retryable,
            },
        )

    @staticmethod
    def _reason_code_from_http_exception(exc: HTTPException) -> str:
        detail = exc.detail
        if isinstance(detail, dict):
            error = detail.get("error")
            if isinstance(error, dict):
                details = error.get("details")
                if isinstance(details, dict) and details.get("reason_code"):
                    return str(details["reason_code"])
                if error.get("code"):
                    return str(error["code"])
        return "generation.failed"

    @staticmethod
    def _durable_usage_summary(
        usage_summary: RAGUsageSummary,
        model: LLMModel,
        credential: LLMCredential,
    ) -> dict[str, Any]:
        durable = usage_summary.model_dump(mode="json")
        durable["model_id"] = str(model.id)
        durable["credential_id"] = str(credential.id)
        return durable
