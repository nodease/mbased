from datetime import datetime, timedelta, timezone
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from apps.gateway.services.rag_agent_answer_audit import RAGAgentAnswerAuditRecorder
from apps.gateway.services.rag_agent_answer_constants import RETENTION_DAYS
from apps.gateway.services.rag_agent_answer_types import RAGAnswerResolvedContext
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.knowledge import RAGAnswerRun
from apps.shared.db.models.user import User
from apps.shared.schemas.rag import RAGAnswerSummary, RAGCitation, RAGRetrievalSummary

logger = logging.getLogger(__name__)


class RAGAgentAnswerLifecycle:
    """RAG answer run의 상태 전이와 lifecycle audit만 담당한다."""

    def __init__(
        self,
        db: Session,
        *,
        current_user: User,
        organization_id: uuid.UUID,
        audit: RAGAgentAnswerAuditRecorder,
        retention_days: int = RETENTION_DAYS,
    ) -> None:
        self.db = db
        self.current_user = current_user
        self.organization_id = organization_id
        self.audit = audit
        self.retention_days = retention_days

    def create_requested(self, resolved: RAGAnswerResolvedContext) -> RAGAnswerRun:
        now = datetime.now(timezone.utc)
        run = RAGAnswerRun(
            organization_id=self.organization_id,
            user_id=self.current_user.id,
            actor_user_ref={"id": str(self.current_user.id)},
            knowledge_base_id=resolved.kb.id,
            knowledge_base_ref={"id": str(resolved.kb.id), "name": resolved.kb.name},
            correlation_id=resolved.correlation_id,
            status="requested",
            retrieval_summary={},
            citation_summary=[],
            answer_summary={},
            policy_result={},
            generation_model_id=resolved.model.id,
            generation_model_snapshot={
                "id": str(resolved.model.id),
                "model_id_for_api_call": resolved.model.model_id_for_api_call,
                "name": resolved.model.name,
                "provider": resolved.model.provider_name,
                "type": resolved.model.type,
            },
            generation_credential_id=resolved.credential.id,
            generation_credential_ref={
                "id": str(resolved.credential.id),
                "provider": (
                    resolved.credential.provider.name
                    if resolved.credential.provider
                    else None
                ),
            },
            usage_summary={},
            retention_expires_at=now + timedelta(days=self.retention_days),
            created_at=now,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        self.audit.record_lifecycle(AuditAction.RAG_ANSWER_REQUESTED, run)
        return run

    def mark_running(self, run: RAGAnswerRun) -> None:
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

    def complete(
        self,
        run: RAGAnswerRun,
        *,
        retrieval_summary: RAGRetrievalSummary,
        citations: list[RAGCitation],
        answer_summary: RAGAnswerSummary,
        policy_result: dict[str, Any],
        durable_usage_summary: dict[str, Any],
    ) -> None:
        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        run.retrieval_summary = retrieval_summary.model_dump(mode="json")
        run.citation_summary = [
            citation.model_dump(mode="json", exclude={"content_preview"})
            for citation in citations
        ]
        run.answer_summary = answer_summary.model_dump(mode="json")
        run.policy_result = policy_result
        run.usage_summary = durable_usage_summary
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        self.audit.record_lifecycle(AuditAction.RAG_ANSWER_COMPLETED, run)

    def fail(self, run: RAGAnswerRun, error_code: str) -> None:
        run.status = "failed"
        run.error_code = error_code
        run.completed_at = datetime.now(timezone.utc)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        self.audit.record_lifecycle(
            AuditAction.RAG_ANSWER_FAILED, run, status="failure"
        )

    def mark_preflight_failed(self, run: RAGAnswerRun) -> None:
        try:
            self.db.rollback()
        except Exception:  # noqa: BLE001 - rollback 실패는 원래 예외를 대체하지 않는다
            logger.exception("RAG Agent answer preflight rollback failed")
        try:
            self.fail(run, "generation.failed")
        except Exception:  # noqa: BLE001 - 상태 마감 실패도 sanitized 응답은 유지한다
            logger.exception("RAG Agent answer preflight failure close failed")

    def block_permission(self, run: RAGAnswerRun, reason_code: str) -> None:
        run.status = "blocked"
        run.completed_at = datetime.now(timezone.utc)
        run.error_code = reason_code
        run.policy_result = {"result": "deny", "reason_code": reason_code}
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

    def block_policy(
        self,
        run: RAGAnswerRun,
        *,
        retrieval_summary: RAGRetrievalSummary,
        citations: list[RAGCitation],
        policy_result: dict[str, Any],
    ) -> None:
        run.status = "blocked"
        run.completed_at = datetime.now(timezone.utc)
        run.error_code = str(policy_result.get("reason_code") or "policy_blocked")
        run.retrieval_summary = retrieval_summary.model_dump(mode="json")
        run.citation_summary = [
            citation.model_dump(mode="json", exclude={"content_preview"})
            for citation in citations
        ]
        run.answer_summary = {
            "answer_length": 0,
            "cited_document_count": len({str(c.document_id) for c in citations}),
            "citation_ids": [c.citation_id for c in citations],
            "policy_result": policy_result,
            "completion_status": "blocked",
        }
        run.policy_result = policy_result
        run.usage_summary = {}
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        self.audit.record_policy_block(run, policy_result)

    def cancel_if_open(self, run: RAGAnswerRun) -> None:
        if getattr(run, "status", None) in {"requested", "running"}:
            self.cancel(run)

    def cancel(self, run: RAGAnswerRun) -> None:
        run.status = "cancelled"
        run.error_code = "client.cancelled"
        run.completed_at = datetime.now(timezone.utc)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        self.audit.record_lifecycle(
            AuditAction.RAG_ANSWER_CANCELLED, run, status="failure"
        )
