from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from apps.gateway.adapters.db import knowledge_collection_sync_repository as repository_module
from apps.gateway.adapters.db.knowledge_collection_sync_repository import (
    SqlAlchemyCollectionSyncAuthorization,
    SqlAlchemyCollectionSyncRepository,
)
from apps.gateway.application.knowledge_collection_sync.use_cases import (
    CollectionSyncCommand,
    CollectionSyncTarget,
)
from apps.shared.db.models.knowledge import KnowledgeCollectionSyncJobItem
from apps.shared.domain.knowledge_collection_sync import sync_target_revision


class FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)

    def flush(self) -> None:
        return None


class CapturingRepository(SqlAlchemyCollectionSyncRepository):
    @staticmethod
    def _job_snapshot(row):
        return row


def test_legacy_organization_manager_without_membership_can_request_sync(
    monkeypatch,
) -> None:
    manager_check = Mock(return_value=True)
    membership_check = Mock(return_value=False)
    monkeypatch.setattr(
        repository_module,
        "has_organization_manager_permission",
        manager_check,
    )
    monkeypatch.setattr(
        repository_module,
        "has_active_organization_membership",
        membership_check,
    )

    allowed = SqlAlchemyCollectionSyncAuthorization(Mock()).is_allowed(
        actor_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
    )

    assert allowed is True
    manager_check.assert_called_once()
    membership_check.assert_not_called()


def test_create_job_persists_each_target_revision() -> None:
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    command = CollectionSyncCommand(
        actor_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4(),
    )
    target = CollectionSyncTarget(
        collection_item_id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        item_rank=3,
        item_created_at=now - timedelta(days=1),
        document_updated_at=now - timedelta(minutes=1),
    )
    db = FakeDb()

    CapturingRepository(db).create_job(
        command=command,
        request_key_hash="a" * 64,
        target_snapshot_revision="b" * 64,
        previous_sync_state="manual",
        targets=(target,),
        now=now,
        execution_deadline_at=now + timedelta(minutes=30),
    )

    item = next(
        value
        for value in db.added
        if isinstance(value, KnowledgeCollectionSyncJobItem)
    )
    assert item.target_revision == sync_target_revision(
        collection_id=command.collection_id,
        collection_item_id=target.collection_item_id,
        knowledge_base_id=target.knowledge_base_id,
        document_id=target.document_id,
        item_rank=target.item_rank,
        item_created_at=target.item_created_at,
        document_updated_at=target.document_updated_at,
    )
