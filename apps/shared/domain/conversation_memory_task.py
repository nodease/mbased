"""Reference-only broker contract for one public Conversation turn."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

CONVERSATION_TURN_TASK_NAME = "workflow.execute_conversation_turn"
CONVERSATION_ADMISSION_RETENTION_TASK_NAME = "workflow.conversation_admission_retention_purge"
CONVERSATION_TURN_TASK_QUEUE = "conversation-memory-v1"
CONVERSATION_TURN_TASK_VERSION = "conversation-turn-task-v1"
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FIELDS = frozenset(
    {
        "envelope_version",
        "organization_id",
        "dispatch_id",
        "turn_id",
        "claim_generation",
        "broker_message_id",
        "memory_contract_version",
        "storage_generation",
        "minimum_worker_capability",
    }
)


class ConversationTurnTaskContractError(ValueError):
    code = "memory.dispatch_envelope_invalid"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ConversationTurnTaskEnvelope:
    organization_id: uuid.UUID
    dispatch_id: uuid.UUID
    turn_id: uuid.UUID
    claim_generation: int
    broker_message_id: str
    memory_contract_version: str
    storage_generation: int
    minimum_worker_capability: str
    envelope_version: str = CONVERSATION_TURN_TASK_VERSION

    def __post_init__(self) -> None:
        if (
            self.envelope_version != CONVERSATION_TURN_TASK_VERSION
            or type(self.claim_generation) is not int
            or self.claim_generation < 1
            or type(self.storage_generation) is not int
            or self.storage_generation < 1
            or not self.broker_message_id
            or len(self.broker_message_id) > 255
            or not _SAFE_VERSION.fullmatch(self.memory_contract_version)
            or not _SAFE_VERSION.fullmatch(self.minimum_worker_capability)
        ):
            raise ConversationTurnTaskContractError()

    def to_payload(self) -> dict[str, Any]:
        return {
            "envelope_version": self.envelope_version,
            "organization_id": str(self.organization_id),
            "dispatch_id": str(self.dispatch_id),
            "turn_id": str(self.turn_id),
            "claim_generation": self.claim_generation,
            "broker_message_id": self.broker_message_id,
            "memory_contract_version": self.memory_contract_version,
            "storage_generation": self.storage_generation,
            "minimum_worker_capability": self.minimum_worker_capability,
        }

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
    ) -> "ConversationTurnTaskEnvelope":
        if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
            raise ConversationTurnTaskContractError()
        try:
            return cls(
                envelope_version=payload["envelope_version"],
                organization_id=uuid.UUID(str(payload["organization_id"])),
                dispatch_id=uuid.UUID(str(payload["dispatch_id"])),
                turn_id=uuid.UUID(str(payload["turn_id"])),
                claim_generation=payload["claim_generation"],
                broker_message_id=payload["broker_message_id"],
                memory_contract_version=payload["memory_contract_version"],
                storage_generation=payload["storage_generation"],
                minimum_worker_capability=payload["minimum_worker_capability"],
            )
        except (KeyError, TypeError, ValueError):
            raise ConversationTurnTaskContractError() from None


__all__ = [
    "CONVERSATION_ADMISSION_RETENTION_TASK_NAME",
    "CONVERSATION_TURN_TASK_NAME",
    "CONVERSATION_TURN_TASK_QUEUE",
    "CONVERSATION_TURN_TASK_VERSION",
    "ConversationTurnTaskContractError",
    "ConversationTurnTaskEnvelope",
]
