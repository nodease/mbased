import pytest
from apps.shared.domain.public_chat_conversation import (
    PUBLIC_CHAT_CONVERSATION_CONTRACT_VERSION,
    PublicChatConversationContractError,
    public_chat_conversation_capability,
    resolve_public_chat_conversation_contract,
)
from apps.shared.domain.workflow_node_location import CanonicalWorkflowNodeLocation


def _config(node_id: str):
    return {
        "public_conversation": {
            "contract_version": PUBLIC_CHAT_CONVERSATION_CONTRACT_VERSION,
            "history_consumer": {
                "node_id": node_id,
                "container_path": [],
            },
        }
    }


def test_public_chat_contract_resolves_one_explicit_llm_consumer():
    graph = {
        "nodes": [
            {"id": "classifier", "type": "llmNode", "data": {}},
            {"id": "answer", "type": "llmNode", "data": {}},
        ],
        "edges": [],
    }

    contract = resolve_public_chat_conversation_contract(
        _config("answer"),
        graph,
        required=True,
    )

    assert contract is not None
    assert contract.history_consumer == CanonicalWorkflowNodeLocation((), "answer")
    assert (
        contract.history_consumer_ref
        == CanonicalWorkflowNodeLocation((), "answer").safe_reference
    )


@pytest.mark.parametrize(
    ("config", "graph", "expected_code"),
    [
        ({}, {"nodes": [], "edges": []}, "conversation.consumer_mapping_required"),
        (
            _config("missing"),
            {"nodes": [{"id": "answer", "type": "llmNode", "data": {}}]},
            "conversation.consumer_mapping_not_found",
        ),
        (
            _config("answer"),
            {"nodes": [{"id": "answer", "type": "answerNode", "data": {}}]},
            "conversation.consumer_mapping_node_type_invalid",
        ),
    ],
)
def test_public_chat_contract_fails_closed_for_missing_or_invalid_mapping(
    config,
    graph,
    expected_code,
):
    with pytest.raises(PublicChatConversationContractError) as exc_info:
        resolve_public_chat_conversation_contract(config, graph, required=True)

    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    ("config", "expected"),
    [(_config("answer"), "client_history_v1"), ({}, "legacy_v0")],
)
def test_public_chat_capability_projects_rollout_contract(config, expected):
    graph = {
        "nodes": [{"id": "answer", "type": "llmNode", "data": {}}],
        "edges": [],
    }

    assert public_chat_conversation_capability(config, graph) == expected
