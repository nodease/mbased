from __future__ import annotations

import uuid

from sqlalchemy.dialects import postgresql

from apps.shared.db.models.workflow_conversation_execution import (
    ConversationWorkflowExecutionEventRecord,
)
from apps.workflow_engine.adapters.conversation_execution_observer import (
    SqlAlchemyConversationExecutionJournalObserver,
)


class _Session:
    def __init__(self) -> None:
        self.statements = []
        self.commits = 0
        self.closed = False

    def execute(self, statement) -> None:
        self.statements.append(statement)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


def test_journal_observer_writes_idempotent_content_free_public_projection() -> None:
    session = _Session()
    observer = SqlAlchemyConversationExecutionJournalObserver(
        session_factory=lambda: session
    )
    values = {
        "event": "execution_running",
        "organization_id": uuid.uuid4(),
        "admission_id": uuid.uuid4(),
        "execution_id": uuid.uuid4(),
        "app_id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(),
        "deployment_id": uuid.uuid4(),
        "deployment_version": 3,
        "session_id": uuid.uuid4(),
        "turn_id": uuid.uuid4(),
        "node_id": "llm",
        "node_type": "llmNode",
        "safe_failure_reason": None,
    }

    observer.record(**values)

    assert session.commits == 1
    assert session.closed is True
    assert len(session.statements) == 1
    statement = session.statements[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    params = compiled.params
    assert params["actor_type"] == "public"
    assert params["event_type"] == "execution_running"
    for key, value in values.items():
        if key == "event":
            continue
        assert params[key] == value
    assert "ON CONFLICT" in str(compiled)
    assert set(params).isdisjoint(
        {
            "inputs",
            "outputs",
            "prompt",
            "raw_input",
            "raw_output",
            "provider_response",
        }
    )


def test_journal_schema_exposes_only_safe_correlation_fields() -> None:
    columns = set(ConversationWorkflowExecutionEventRecord.__table__.columns.keys())

    assert columns == {
        "id",
        "organization_id",
        "admission_id",
        "execution_id",
        "app_id",
        "workflow_id",
        "deployment_id",
        "deployment_version",
        "session_id",
        "turn_id",
        "node_id",
        "node_type",
        "actor_type",
        "event_type",
        "safe_failure_reason",
        "created_at",
    }
