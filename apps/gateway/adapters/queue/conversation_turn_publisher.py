"""Post-commit publisher for reference-only Conversation turn work."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from apps.memory.adapters.persistence.repository import (
    SqlAlchemyConversationMemoryRepository,
    SqlAlchemyMemoryUnitOfWork,
)
from apps.memory.application.dispatch import (
    ClaimTurnDispatchCommand,
    ClaimTurnDispatchUseCase,
    FinalizeTerminalTurnDispatchCommand,
    FinalizeTerminalTurnDispatchUseCase,
    MarkTurnDispatchPublishedCommand,
    MarkTurnDispatchPublishedUseCase,
    RecordTurnDispatchPublishFailureCommand,
    RecordTurnDispatchPublishFailureUseCase,
)
from apps.memory.domain.conversation import DispatchStatus
from apps.shared.domain.conversation_memory_task import (
    CONVERSATION_TURN_TASK_NAME,
    CONVERSATION_TURN_TASK_QUEUE,
    ConversationTurnTaskEnvelope,
)
from apps.shared.services.workflow_task_publisher import send_workflow_task


class CeleryConversationTurnPublisher:
    def __init__(
        self,
        *,
        celery_app: Any,
        session_factory: Callable[[], Session],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._celery_app = celery_app
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def publish(
        self,
        *,
        organization_id: uuid.UUID,
        session_id: uuid.UUID,
        dispatch_id: uuid.UUID,
        turn_id: uuid.UUID,
        memory_contract_version: str,
        storage_generation: int,
        minimum_worker_capability: str,
    ) -> None:
        session = self._session_factory()
        try:
            repository = SqlAlchemyConversationMemoryRepository(session)
            uow = SqlAlchemyMemoryUnitOfWork(session)
            owner = f"gateway-{uuid.uuid4().hex}"
            now = self._clock()
            claim = ClaimTurnDispatchUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                ClaimTurnDispatchCommand(
                    organization_id=organization_id,
                    dispatch_id=dispatch_id,
                    owner=owner,
                    deadline=now + timedelta(seconds=30),
                    now=now,
                )
            )
            message_id = f"conversation-turn-{dispatch_id}-g{claim.claim_generation}"
            envelope = ConversationTurnTaskEnvelope(
                organization_id=organization_id,
                dispatch_id=dispatch_id,
                turn_id=turn_id,
                claim_generation=claim.claim_generation,
                broker_message_id=message_id,
                memory_contract_version=memory_contract_version,
                storage_generation=storage_generation,
                minimum_worker_capability=minimum_worker_capability,
            )
            try:
                send_workflow_task(
                    self._celery_app,
                    CONVERSATION_TURN_TASK_NAME,
                    args=[envelope.to_payload()],
                    task_id=message_id,
                    queue=CONVERSATION_TURN_TASK_QUEUE,
                    ignore_result=True,
                    retry=False,
                )
            except Exception:
                failure = RecordTurnDispatchPublishFailureUseCase(
                    repository=repository,
                    uow=uow,
                ).execute(
                    RecordTurnDispatchPublishFailureCommand(
                        organization_id=organization_id,
                        dispatch_id=dispatch_id,
                        owner=owner,
                        claim_generation=claim.claim_generation,
                        safe_reason_code="memory.dispatch_publish_failed",
                        now=self._clock(),
                    )
                )
                if failure.status is DispatchStatus.TERMINAL:
                    FinalizeTerminalTurnDispatchUseCase(
                        repository=repository,
                        uow=uow,
                    ).execute(
                        FinalizeTerminalTurnDispatchCommand(
                            organization_id=organization_id,
                            session_id=session_id,
                            turn_id=turn_id,
                            dispatch_id=dispatch_id,
                            now=self._clock(),
                        )
                    )
                raise
            MarkTurnDispatchPublishedUseCase(
                repository=repository,
                uow=uow,
            ).execute(
                MarkTurnDispatchPublishedCommand(
                    organization_id=organization_id,
                    dispatch_id=dispatch_id,
                    owner=owner,
                    claim_generation=claim.claim_generation,
                    broker_message_id=message_id,
                    now=self._clock(),
                )
            )
        finally:
            session.close()


__all__ = ["CeleryConversationTurnPublisher"]
