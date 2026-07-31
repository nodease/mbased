from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from apps.gateway.services.audit_records import add_action_audit
from apps.shared.domain.knowledge_collection_sync import safe_reason_code

_ACTIONS = frozenset(
    {
        "knowledge.collection.sync.requested",
        "knowledge.collection.sync.reused",
        "knowledge.collection.sync.denied",
    }
)


class SqlAlchemyCollectionSyncAudit:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        *,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
        collection_id: uuid.UUID,
        action: str,
        job_id: uuid.UUID | None = None,
        metadata: dict[str, object] | None = None,
        status: str = "success",
    ) -> None:
        if action not in _ACTIONS:
            raise ValueError("unsupported collection sync audit action")
        safe_metadata: dict[str, object] = {}
        for key, value in (metadata or {}).items():
            if key in {"job_status", "target_count_bucket"} and isinstance(
                value, str
            ):
                safe_metadata[key] = value
            elif key == "reason_code" and isinstance(value, str):
                safe_metadata[key] = (
                    "permission.denied"
                    if value == "permission.denied"
                    else safe_reason_code(value)
                )
        if job_id is not None:
            safe_metadata["job_id"] = str(job_id)
        add_action_audit(
            self.db,
            action,
            actor_id,
            "knowledge_collection",
            collection_id,
            organization_id=organization_id,
            metadata=safe_metadata,
            status=status,
        )
