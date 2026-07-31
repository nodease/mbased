from __future__ import annotations

from sqlalchemy.orm import Session

from apps.gateway.application.knowledge_administration.collection_operations import (
    CollectionOperationCommand,
)
from apps.gateway.services.audit_records import add_data_change_audit


class SqlAlchemyKnowledgeCollectionOperationAudit:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        *,
        command: CollectionOperationCommand,
        action: str,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        add_data_change_audit(
            self.db,
            action,
            command.actor_id,
            "knowledge_collection",
            command.collection_id,
            before=before,
            after=after,
            organization_id=command.organization_id,
            metadata=metadata,
        )
