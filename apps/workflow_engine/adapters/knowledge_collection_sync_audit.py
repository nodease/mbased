from __future__ import annotations

from sqlalchemy.orm import Session

from apps.shared.db.models.audit_log import (
    ActorType,
    AuditCategory,
    AuditLog,
    AuditStatus,
)
from apps.shared.domain.knowledge_collection_sync import safe_reason_code
from apps.workflow_engine.application.knowledge_collection_sync import WorkerSyncJob

_ACTIONS = frozenset(
    {
        "knowledge.collection.sync.started",
        "knowledge.collection.sync.completed",
        "knowledge.collection.sync.cancelled",
    }
)
_METADATA_KEYS = frozenset(
    {"job_status", "result_count_bucket", "reason_code", "retryable"}
)


class SqlAlchemyKnowledgeCollectionSyncAudit:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        *,
        job: WorkerSyncJob,
        action: str,
        status: str = "success",
        metadata: dict[str, object] | None = None,
    ) -> None:
        if action not in _ACTIONS:
            raise ValueError("unsupported collection sync audit action")
        safe_metadata: dict[str, object] = {
            "organization_id": str(job.organization_id),
            "job_id": str(job.job_id),
        }
        for key, value in (metadata or {}).items():
            if key not in _METADATA_KEYS:
                continue
            if key == "reason_code":
                value = safe_reason_code(value if isinstance(value, str) else None)
            if isinstance(value, (str, bool)) or value is None:
                safe_metadata[key] = value
        self.db.add(
            AuditLog(
                action=action,
                category=AuditCategory.ACTION,
                actor_id=job.requested_by,
                actor_type=ActorType.USER,
                target_type="knowledge_collection",
                target_id=str(job.collection_id),
                before=None,
                after=None,
                status=(
                    AuditStatus.SUCCESS
                    if status == "success"
                    else AuditStatus.FAILURE
                ),
                audit_metadata=safe_metadata,
            )
        )
