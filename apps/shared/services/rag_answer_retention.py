import uuid
from datetime import datetime, timezone
from typing import Any

from apps.shared.audit.actions import AuditAction
from apps.shared.audit.logger import record_audit
from apps.shared.db.models.knowledge import RAGAnswerRun
from sqlalchemy.orm import Session

DEFAULT_RAG_ANSWER_PURGE_LIMIT = 1000
MAX_RAG_ANSWER_PURGE_LIMIT = 5000


class RAGAnswerRetentionService:
    """만료된 standalone RAG Agent answer run을 정리한다."""

    @staticmethod
    def validate_limit(limit: int) -> int:
        if isinstance(limit, bool):
            raise ValueError("rag_answer_retention_purge limit must be an integer.")
        try:
            normalized_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "rag_answer_retention_purge limit must be an integer."
            ) from exc
        if normalized_limit < 1 or normalized_limit > MAX_RAG_ANSWER_PURGE_LIMIT:
            raise ValueError(
                "rag_answer_retention_purge limit must be between "
                f"1 and {MAX_RAG_ANSWER_PURGE_LIMIT}."
            )
        return normalized_limit

    @staticmethod
    def purge(
        db: Session,
        *,
        now: datetime | None = None,
        organization_id: uuid.UUID | None = None,
        limit: int = DEFAULT_RAG_ANSWER_PURGE_LIMIT,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        normalized_limit = RAGAnswerRetentionService.validate_limit(limit)
        cutoff = now or datetime.now(timezone.utc)
        query = db.query(RAGAnswerRun).filter(
            RAGAnswerRun.retention_expires_at <= cutoff
        )
        if organization_id is not None:
            query = query.filter(RAGAnswerRun.organization_id == organization_id)
        rows = (
            query.order_by(RAGAnswerRun.retention_expires_at.asc())
            .limit(normalized_limit)
            .all()
        )
        would_purge_count = len(rows)

        purged_count = 0
        failed_count = 0
        if not dry_run:
            try:
                for row in rows:
                    db.delete(row)
                    purged_count += 1
                db.commit()
            except Exception:
                db.rollback()
                failed_count = len(rows)
                purged_count = 0
                RAGAnswerRetentionService._record_purge_audit(
                    organization_id=organization_id,
                    cutoff=cutoff,
                    purged_count=purged_count,
                    failed_count=failed_count,
                    retryable=True,
                    status="failure",
                )
                raise

        status = "success" if failed_count == 0 else "failure"
        if not dry_run:
            RAGAnswerRetentionService._record_purge_audit(
                organization_id=organization_id,
                cutoff=cutoff,
                purged_count=purged_count,
                failed_count=failed_count,
                retryable=failed_count > 0,
                status=status,
            )
        return {
            "cutoff": cutoff.isoformat(),
            "would_purge_count": would_purge_count,
            "purged_count": purged_count,
            "failed_count": failed_count,
            "retryable": failed_count > 0,
            "dry_run": dry_run,
        }

    @staticmethod
    def _record_purge_audit(
        *,
        organization_id: uuid.UUID | None,
        cutoff: datetime,
        purged_count: int,
        failed_count: int,
        retryable: bool,
        status: str,
    ) -> None:
        metadata: dict[str, Any] = {
            "cutoff": cutoff.isoformat(),
            "purged_count": purged_count,
            "failed_count": failed_count,
            "retryable": retryable,
            "status": status,
        }
        if organization_id is not None:
            metadata["organization_id"] = str(organization_id)
        record_audit(
            action=AuditAction.RAG_ANSWER_PURGE,
            category="system",
            actor_type="system",
            target_type="rag_answer_runs",
            target_id=None,
            status=status,
            metadata=metadata,
        )
