import uuid
from typing import Any

from sqlalchemy.orm import Session

from apps.shared.audit.actions import AuditAction
from apps.shared.audit.logger import record_audit
from apps.shared.db.models.knowledge import RAGAnswerRun
from apps.shared.db.models.llm import LLMCredential, LLMModel, LLMUsageLog
from apps.shared.services.security_alert_policy_reason import (
    with_normalized_security_alert_policy_reason,
)


class RAGAgentAnswerAuditRecorder:
    """RAG Agent answer의 audit event와 canonical usage log 기록을 담당한다."""

    def __init__(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.organization_id = organization_id

    def record_lifecycle(
        self, action: str, run: RAGAnswerRun, status: str = "success"
    ) -> None:
        record_audit(
            action=action,
            category="action",
            actor_id=self.user_id,
            actor_type="user",
            target_type="rag_answer_run",
            target_id=run.id,
            status=status,
            metadata={
                "organization_id": str(self.organization_id),
                "answer_run_id": str(run.id),
                "correlation_id": run.correlation_id,
                "run_status": run.status,
            },
        )

    def record_retrieval(
        self, run: RAGAnswerRun, metadata_filter, result_count: int, mode: str
    ) -> None:
        record_audit(
            action=AuditAction.RAG_RETRIEVE,
            category="action",
            actor_id=self.user_id,
            actor_type="user",
            target_type="knowledge_base",
            target_id=run.knowledge_base_id,
            status="success",
            metadata={
                "organization_id": str(self.organization_id),
                "answer_run_id": str(run.id),
                "correlation_id": run.correlation_id,
                "retrieval_mode": mode,
                "result_count": result_count,
                "metadata_filter": metadata_filter.audit_summary()
                if metadata_filter is not None
                else {},
            },
        )

    def record_llm_call(
        self,
        run: RAGAnswerRun,
        model: LLMModel,
        credential: LLMCredential,
        *,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        record_audit(
            action=AuditAction.LLM_CALL,
            category="action",
            actor_id=self.user_id,
            actor_type="user",
            target_type="llm_model",
            target_id=model.id,
            status=status,
            metadata={
                "organization_id": str(self.organization_id),
                "answer_run_id": str(run.id),
                "correlation_id": run.correlation_id,
                "model_id": str(model.id),
                "credential_id": str(credential.id),
                **(metadata or {}),
            },
        )

    def record_policy_block(
        self, run: RAGAnswerRun, policy_result: dict[str, Any]
    ) -> None:
        metadata = with_normalized_security_alert_policy_reason(
            {
                "organization_id": str(self.organization_id),
                "answer_run_id": str(run.id),
                "correlation_id": run.correlation_id,
                "policy_result": policy_result,
            }
        )
        record_audit(
            action=AuditAction.POLICY_BLOCK,
            category="action",
            actor_id=self.user_id,
            actor_type="user",
            target_type="rag_answer_run",
            target_id=run.id,
            status="failure",
            metadata=metadata,
        )

    def record_usage_log(
        self,
        model: LLMModel,
        credential: LLMCredential,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_cost: float | None,
        latency_ms: int,
    ) -> None:
        usage_log = LLMUsageLog(
            user_id=self.user_id,
            organization_id=self.organization_id,
            credential_id=credential.id,
            model_id=model.id,
            workflow_id=None,
            workflow_run_id=None,
            node_id=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_cost=total_cost,
            latency_ms=latency_ms,
            status="success",
        )
        self.db.add(usage_log)
