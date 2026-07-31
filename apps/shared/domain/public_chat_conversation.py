from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from apps.shared.domain.workflow_node_location import (
    CanonicalWorkflowNodeLocation,
    WorkflowNodeLocationError,
    find_workflow_node_at_location,
)

PUBLIC_CHAT_CONVERSATION_CONTRACT_VERSION = "public_chat_conversation.v1"
PUBLIC_CHAT_CLIENT_HISTORY_CAPABILITY = "client_history_v1"
PUBLIC_CHAT_LEGACY_CAPABILITY = "legacy_v0"
PUBLIC_CHAT_REQUEST_TTL_SECONDS = 600

_PUBLIC_CONVERSATION_KEY = "public_conversation"
_CONTRACT_KEYS = frozenset({"contract_version", "history_consumer"})
_CONSUMER_KEYS = frozenset({"node_id", "container_path"})


class PublicChatConversationContractError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PublicChatConversationContract:
    history_consumer: CanonicalWorkflowNodeLocation

    @property
    def history_consumer_ref(self) -> str:
        return self.history_consumer.safe_reference


def resolve_public_chat_conversation_contract(
    config: Mapping[str, Any] | None,
    graph: Mapping[str, Any] | None,
    *,
    required: bool,
) -> PublicChatConversationContract | None:
    raw_contract = (
        config.get(_PUBLIC_CONVERSATION_KEY) if isinstance(config, Mapping) else None
    )
    if raw_contract is None:
        if required:
            raise PublicChatConversationContractError(
                "conversation.consumer_mapping_required"
            )
        return None
    if (
        not isinstance(raw_contract, Mapping)
        or set(raw_contract) != _CONTRACT_KEYS
        or raw_contract.get("contract_version")
        != PUBLIC_CHAT_CONVERSATION_CONTRACT_VERSION
    ):
        raise PublicChatConversationContractError(
            "conversation.consumer_mapping_invalid"
        )

    raw_consumer = raw_contract.get("history_consumer")
    if (
        not isinstance(raw_consumer, Mapping)
        or set(raw_consumer) != _CONSUMER_KEYS
        or not isinstance(raw_consumer.get("node_id"), str)
    ):
        raise PublicChatConversationContractError(
            "conversation.consumer_mapping_invalid"
        )

    try:
        location = CanonicalWorkflowNodeLocation.from_container_path_payload(
            container_path=raw_consumer.get("container_path"),
            node_id=raw_consumer["node_id"],
        )
        node = find_workflow_node_at_location(graph, location)
    except WorkflowNodeLocationError as error:
        code = (
            "conversation.consumer_mapping_not_found"
            if error.code == "workflow_node_location.not_found"
            else "conversation.consumer_mapping_invalid"
        )
        raise PublicChatConversationContractError(code) from None

    if node.get("type") != "llmNode":
        raise PublicChatConversationContractError(
            "conversation.consumer_mapping_node_type_invalid"
        )
    return PublicChatConversationContract(history_consumer=location)


def public_chat_conversation_capability(
    config: Mapping[str, Any] | None,
    graph: Mapping[str, Any] | None,
) -> str:
    try:
        contract = resolve_public_chat_conversation_contract(
            config,
            graph,
            required=False,
        )
    except PublicChatConversationContractError:
        return PUBLIC_CHAT_LEGACY_CAPABILITY
    return (
        PUBLIC_CHAT_CLIENT_HISTORY_CAPABILITY
        if contract is not None
        else PUBLIC_CHAT_LEGACY_CAPABILITY
    )


__all__ = [
    "PUBLIC_CHAT_CLIENT_HISTORY_CAPABILITY",
    "PUBLIC_CHAT_CONVERSATION_CONTRACT_VERSION",
    "PUBLIC_CHAT_LEGACY_CAPABILITY",
    "PUBLIC_CHAT_REQUEST_TTL_SECONDS",
    "PublicChatConversationContract",
    "PublicChatConversationContractError",
    "public_chat_conversation_capability",
    "resolve_public_chat_conversation_contract",
]
