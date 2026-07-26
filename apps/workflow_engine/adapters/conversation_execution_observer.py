"""Durable content-free observer for public conversation executions."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from apps.shared.db.models.workflow_conversation_execution import (
    ConversationWorkflowExecutionEventRecord,
)

_EVENT_TYPES = frozenset(
    {
        "execution_admitted",
        "execution_running",
        "execution_completed",
        "execution_failed",
        "execution_outcome_unknown",
    }
)
_TERMINAL_FAILURE_EVENTS = frozenset(
    {"execution_failed", "execution_outcome_unknown"}
)
_SAFE_REASON = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}")


class SqlAlchemyConversationExecutionJournalObserver:
    """Append an idempotent safe event without persisting conversation content."""

    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        *,
        event: str,
        safe_failure_reason: str | None,
        **correlation: Any,
    ) -> None:
        _validate_event(event, safe_failure_reason=safe_failure_reason)
        session = self._session_factory()
        try:
            statement = (
                insert(ConversationWorkflowExecutionEventRecord)
                .values(
                    actor_type="public",
                    event_type=event,
                    safe_failure_reason=safe_failure_reason,
                    **correlation,
                )
                .on_conflict_do_nothing(
                    constraint="uq_conv_workflow_event_admission_type"
                )
            )
            session.execute(statement)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _validate_event(event: str, *, safe_failure_reason: str | None) -> None:
    if event not in _EVENT_TYPES:
        raise ValueError("memory.execution_event_invalid")
    if event in _TERMINAL_FAILURE_EVENTS:
        if safe_failure_reason is None or _SAFE_REASON.fullmatch(
            safe_failure_reason
        ) is None:
            raise ValueError("memory.execution_failure_reason_invalid")
    elif safe_failure_reason is not None:
        raise ValueError("memory.execution_failure_reason_unexpected")


__all__ = ["SqlAlchemyConversationExecutionJournalObserver"]
