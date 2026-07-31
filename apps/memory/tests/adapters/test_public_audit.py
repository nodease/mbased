from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from apps.memory.adapters.audit import SqlAlchemyPublicConversationAudit
from apps.memory.domain.errors import MemoryAdapterUnavailableError


def test_public_lifecycle_audit_uses_anonymous_public_actor_and_safe_metadata(monkeypatch):
    captured = {}

    def record_audit(**kwargs):
        captured.update(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr("apps.memory.adapters.audit.record_audit", record_audit)
    audit = SqlAlchemyPublicConversationAudit(MagicMock(spec=Session))

    audit.record(
        action="memory.session.created",
        organization_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        target_type="conversation_session",
        target_id=uuid.uuid4(),
    )

    assert captured["actor_id"] is None
    assert captured["actor_type"] == "public"
    assert captured["target_type"] == "conversation_session"
    assert set(captured["metadata"]) == {
        "organization_id",
        "deployment_id",
        "session_id",
        "surface",
        "purge_job_id",
    }


def test_public_lifecycle_audit_outbox_failure_aborts_the_memory_uow(monkeypatch):
    monkeypatch.setattr("apps.memory.adapters.audit.record_audit", lambda **_kwargs: None)
    audit = SqlAlchemyPublicConversationAudit(MagicMock(spec=Session))

    with pytest.raises(MemoryAdapterUnavailableError):
        audit.record(
            action="memory.session.created",
            organization_id=uuid.uuid4(),
            deployment_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            target_type="conversation_session",
            target_id=uuid.uuid4(),
        )
