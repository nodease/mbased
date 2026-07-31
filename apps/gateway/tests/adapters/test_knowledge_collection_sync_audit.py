import uuid

import pytest

from apps.gateway.adapters.audit.knowledge_collection_sync import (
    SqlAlchemyCollectionSyncAudit,
)


class Db:
    def __init__(self) -> None:
        self.rows = []

    def add(self, row) -> None:
        self.rows.append(row)


def test_collection_sync_audit_default_denies_unknown_metadata() -> None:
    db = Db()
    SqlAlchemyCollectionSyncAudit(db).record(
        actor_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        action="knowledge.collection.sync.requested",
        metadata={
            "target_count_bucket": "2-10",
            "connection_id": "must-not-survive",
            "raw_error": "must-not-survive",
        },
    )

    metadata = db.rows[0].audit_metadata
    assert metadata["target_count_bucket"] == "2-10"
    assert "connection_id" not in metadata
    assert "raw_error" not in metadata


def test_collection_sync_audit_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        SqlAlchemyCollectionSyncAudit(Db()).record(
            actor_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            collection_id=uuid.uuid4(),
            action="knowledge.collection.sync.raw_dump",
        )
