import asyncio
import time

from sqlalchemy.orm import Session

from apps.gateway.services.llm_service import LLMService
from apps.gateway.services.rag_agent_answer_audit import RAGAgentAnswerAuditRecorder
from apps.gateway.services.rag_agent_answer_builder import RAGAgentAnswerBuilder
from apps.gateway.services.rag_agent_answer_constants import PROVIDER_TIMEOUT_SECONDS
from apps.shared.db.models.knowledge import RAGAnswerRun
from apps.shared.db.models.llm import LLMCredential, LLMModel
from apps.shared.schemas.rag import RAGAgentAnswerRequest, RAGUsageSummary
from apps.shared.services.llm_client import get_llm_client
from apps.shared.services.llm_credential_config import (
    materialize_llm_client_credentials,
)
from apps.shared.utils.prompt_injection_guard import (
    PLATFORM_UNTRUSTED_CONTEXT_GUARDRAIL_PROMPT,
    build_untrusted_context_block,
)


class RAGAgentAnswerGenerationRunner:
    """Provider 호출, 사용량 계산, LLM audit/usage row 기록을 담당한다."""

    def __init__(
        self,
        db: Session,
        *,
        builder: RAGAgentAnswerBuilder,
        audit: RAGAgentAnswerAuditRecorder,
        provider_timeout_seconds: int = PROVIDER_TIMEOUT_SECONDS,
    ) -> None:
        self.db = db
        self.builder = builder
        self.audit = audit
        self.provider_timeout_seconds = provider_timeout_seconds

    async def generate(
        self,
        payload: RAGAgentAnswerRequest,
        chunks,
        model: LLMModel,
        credential: LLMCredential,
        run: RAGAnswerRun,
    ) -> tuple[str, RAGUsageSummary]:
        client = self._client_for(model, credential)
        context_text = self.builder.context_for_chunks(chunks, model)
        system_prompt = "\n".join(
            [
                PLATFORM_UNTRUSTED_CONTEXT_GUARDRAIL_PROMPT,
                "Answer using only the provided knowledge evidence.",
                "If the answer is not supported by the evidence, say you don't know.",
            ]
        )
        messages = [
            {"role": "system", "content": system_prompt},
        ]
        knowledge_block = build_untrusted_context_block(context_text, label="KNOWLEDGE")
        if knowledge_block:
            messages.append({"role": "user", "content": knowledge_block})
        messages.append({"role": "user", "content": payload.query})
        llm_start = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                client.invoke(
                    messages,
                    max_tokens=self.builder.output_token_budget_for_model(model),
                ),
                timeout=self.provider_timeout_seconds,
            )
        except Exception:
            self.audit.record_llm_call(run, model, credential, status="failure")
            raise

        latency_ms = int((time.perf_counter() - llm_start) * 1000)
        usage = dict(result.get("usage") or {})
        usage["latency_ms"] = latency_ms
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(
            usage.get("total_tokens") or prompt_tokens + completion_tokens
        )
        total_cost = LLMService.calculate_cost(
            self.db,
            model.model_id_for_api_call,
            prompt_tokens,
            completion_tokens,
            usage=usage,
        )
        answer = (
            result.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        self.audit.record_llm_call(
            run,
            model,
            credential,
            status="success",
            metadata={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "latency_ms": latency_ms,
            },
        )
        self.audit.record_usage_log(
            model,
            credential,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_cost=total_cost,
            latency_ms=latency_ms,
        )
        return answer, RAGUsageSummary(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            total_cost=total_cost,
            latency_ms=latency_ms,
            model_name=model.name,
            provider=model.provider_name,
        )

    @staticmethod
    def _client_for(model: LLMModel, credential: LLMCredential):
        try:
            credentials = materialize_llm_client_credentials(
                credential,
                credential.provider,
            )
        except Exception as exc:  # noqa: BLE001 - secret 원문을 응답에 포함하지 않는다
            raise ValueError("Invalid credential config") from exc
        return get_llm_client(
            provider=credential.provider.name,
            model_id=model.model_id_for_api_call,
            credentials=credentials,
        )
