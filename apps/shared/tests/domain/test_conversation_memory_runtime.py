from __future__ import annotations

from copy import deepcopy

import pytest
from apps.shared.domain.conversation_memory_runtime import (
    ConversationMemoryRuntimeContractError,
    conversation_memory_runtime_requested,
    validate_conversation_memory_runtime,
)


def _graph() -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "Start",
                    "variables": [
                        {
                            "id": "question",
                            "name": "question",
                            "label": "Question",
                            "type": "paragraph",
                            "required": True,
                            "max_length": 16_384,
                        }
                    ],
                },
            },
            {
                "id": "llm",
                "type": "llmNode",
                "position": {"x": 1, "y": 0},
                "data": {
                    "title": "Answer",
                    "model_id": "fixed-model",
                    "task_type": "generate",
                    "system_prompt": "Follow the instructions.",
                    "user_prompt": "{{ question }}",
                    "referenced_variables": [
                        {
                            "name": "question",
                            "value_selector": ["start", "question"],
                        }
                    ],
                    "parameters": {"temperature": 0.2, "n": 1},
                    "memory": {
                        "enabled": True,
                        "channel": "conversation",
                        "readSource": "conversation_turns",
                        "writeMode": "none",
                        "maxTurns": 5,
                        "maxContextTokens": 1_200,
                        "strategy": "window",
                        "failurePolicy": "fail_node",
                    },
                },
            },
            {
                "id": "answer",
                "type": "answerNode",
                "position": {"x": 2, "y": 0},
                "data": {
                    "title": "Response",
                    "outputs": [
                        {
                            "variable": "answer",
                            "value_selector": ["llm", "text"],
                        }
                    ],
                },
            },
        ],
        "edges": [
            {"id": "start-llm", "source": "start", "target": "llm"},
            {"id": "llm-answer", "source": "llm", "target": "answer"},
        ],
    }


def _config() -> dict:
    return {
        "conversation_memory": {
            "contract_version": "conversation-memory-v1",
            "storage_generation": 1,
            "mapping_version": "conversation-mapping-v1",
            "memory_policy_version": "memory-policy-v1",
            "input": {"node_id": "start", "variable": "question"},
            "output": {"node_id": "answer", "variable": "answer"},
        }
    }


def _invalid(graph: dict, config: dict, code: str) -> None:
    with pytest.raises(ConversationMemoryRuntimeContractError) as exc_info:
        validate_conversation_memory_runtime(graph, config)
    assert exc_info.value.code == code


def test_valid_initial_public_conversation_contract_is_canonicalized() -> None:
    contract = validate_conversation_memory_runtime(_graph(), _config())

    assert contract is not None
    assert contract.start_node_id == "start"
    assert contract.input_variable == "question"
    assert contract.input_max_length == 16_384
    assert contract.llm_node_id == "llm"
    assert contract.answer_node_id == "answer"
    assert contract.output_variable == "answer"
    assert contract.memory.max_turns == 5
    assert contract.memory.max_context_tokens == 1_200


def test_memory_runtime_intent_survives_invalid_contract_validation() -> None:
    graph = _graph()
    config = {"conversation_memory": "invalid"}

    assert conversation_memory_runtime_requested(graph, config) is True
    with pytest.raises(ConversationMemoryRuntimeContractError):
        validate_conversation_memory_runtime(graph, config)


def test_memory_runtime_intent_detects_enabled_graph_without_runtime_config() -> None:
    assert conversation_memory_runtime_requested(_graph(), {}) is True

    graph = _graph()
    graph["nodes"][1]["data"]["memory"]["enabled"] = False
    assert conversation_memory_runtime_requested(graph, {}) is False


@pytest.mark.parametrize("enabled", [1, 0, "true", "false", "yes", None])
def test_memory_runtime_intent_treats_non_literal_boolean_as_malformed_memory_on(
    enabled,
) -> None:
    graph = _graph()
    graph["nodes"][1]["data"]["memory"]["enabled"] = enabled

    assert conversation_memory_runtime_requested(graph, {}) is True


def test_serialized_memory_schema_defaults_remain_runtime_compatible() -> None:
    graph = _graph()
    graph["nodes"][1]["data"]["memory"].update(
        {
            "selectedNodeIds": [],
            "summaryModelPolicy": "inherit_node",
        }
    )

    contract = validate_conversation_memory_runtime(graph, _config())

    assert contract is not None
    assert contract.memory.strategy == "window"


@pytest.mark.parametrize(
    "field,value",
    [
        ("selectedNodeIds", ["another-node"]),
        ("summaryModelPolicy", "organization_default"),
    ],
)
def test_inactive_schema_fields_must_have_v1_safe_values(
    field: str,
    value: object,
) -> None:
    graph = _graph()
    graph["nodes"][1]["data"]["memory"].update(
        {
            "selectedNodeIds": [],
            "summaryModelPolicy": "inherit_node",
            field: value,
        }
    )

    _invalid(graph, _config(), "memory.memory_policy_unsupported")


def test_absent_memory_contract_preserves_memory_off_graph() -> None:
    graph = _graph()
    graph["nodes"][1]["data"].pop("memory")

    assert validate_conversation_memory_runtime(graph, {}) is None


@pytest.mark.parametrize("value", [0, 2, True, 1.0, "1"])
def test_generation_cardinality_is_exact_integer_one(value: object) -> None:
    graph = _graph()
    graph["nodes"][1]["data"]["parameters"]["n"] = value

    _invalid(graph, _config(), "memory.llm_parameters_unsupported")


@pytest.mark.parametrize(
    "parameter",
    ["response_format", "tools", "tool_choice", "stream", "seed", "unknown"],
)
def test_unknown_or_structured_provider_parameters_fail_closed(parameter: str) -> None:
    graph = _graph()
    graph["nodes"][1]["data"]["parameters"][parameter] = {}

    _invalid(graph, _config(), "memory.llm_parameters_unsupported")


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            lambda graph: graph["nodes"].append(
                {"id": "extra", "type": "templateNode", "data": {}}
            ),
            "memory.graph_unsupported",
        ),
        (
            lambda graph: graph["edges"].append(
                {"id": "bypass", "source": "start", "target": "answer"}
            ),
            "memory.graph_unsupported",
        ),
        (
            lambda graph: graph["nodes"][1]["data"].update(
                {"auto_model_routing": True}
            ),
            "memory.llm_behavior_unsupported",
        ),
        (
            lambda graph: graph["nodes"][1]["data"].update(
                {"fallback_model_id": "fallback-model"}
            ),
            "memory.llm_behavior_unsupported",
        ),
        (
            lambda graph: graph["nodes"][1]["data"].update(
                {"knowledgeBases": [{"id": "kb", "name": "private"}]}
            ),
            "memory.llm_behavior_unsupported",
        ),
        (
            lambda graph: graph["nodes"][1]["data"].update(
                {"output_format": {"type": "json"}}
            ),
            "memory.llm_behavior_unsupported",
        ),
    ],
)
def test_active_behavior_outside_initial_allowlist_fails_closed(
    mutation, code: str
) -> None:
    graph = _graph()
    mutation(graph)

    _invalid(graph, _config(), code)


def test_prompt_variables_must_exactly_match_mapped_start_input() -> None:
    graph = _graph()
    graph["nodes"][1]["data"]["referenced_variables"][0]["value_selector"] = [
        "start",
        "other",
    ]

    _invalid(graph, _config(), "memory.input_mapping_invalid")


@pytest.mark.parametrize(
    ("prompt_key", "prompt_value", "user_prompt"),
    [
        ("system_prompt", "Policy for {{ question }}", "Fixed request"),
        ("assistant_prompt", "Prior answer: {{ question }}", "{{ question }}"),
    ],
)
def test_prompt_mapping_matches_runtime_message_boundary(
    prompt_key: str,
    prompt_value: str,
    user_prompt: str | None,
) -> None:
    graph = _graph()
    graph["nodes"][1]["data"][prompt_key] = prompt_value
    if user_prompt is not None:
        graph["nodes"][1]["data"]["user_prompt"] = user_prompt

    _invalid(graph, _config(), "memory.input_mapping_invalid")


def test_answer_must_select_the_single_llm_text_result() -> None:
    graph = _graph()
    graph["nodes"][2]["data"]["outputs"][0]["value_selector"] = [
        "llm",
        "usage",
    ]

    _invalid(graph, _config(), "memory.output_mapping_invalid")


def test_memory_enabled_node_without_versioned_deployment_mapping_fails_closed() -> None:
    _invalid(_graph(), {}, "memory.input_mapping_invalid")


def test_mapping_version_and_policy_are_bounded_and_exactly_bound() -> None:
    config = _config()
    config["conversation_memory"]["mapping_version"] = "x" * 65

    _invalid(_graph(), config, "memory.input_mapping_invalid")


def test_validation_does_not_mutate_the_frozen_snapshot() -> None:
    graph = _graph()
    config = _config()
    before_graph = deepcopy(graph)
    before_config = deepcopy(config)

    validate_conversation_memory_runtime(graph, config)

    assert graph == before_graph
    assert config == before_config


@pytest.mark.parametrize(
    "max_context_tokens,accepted",
    [(4_096, True), (4_097, False)],
)
def test_memory_context_token_limit_matches_the_public_contract(
    max_context_tokens: int, accepted: bool
) -> None:
    graph = _graph()
    graph["nodes"][1]["data"]["memory"]["maxContextTokens"] = max_context_tokens

    if accepted:
        contract = validate_conversation_memory_runtime(graph, _config())
        assert contract is not None
        assert contract.memory.max_context_tokens == max_context_tokens
    else:
        _invalid(graph, _config(), "memory.memory_policy_unsupported")


@pytest.mark.parametrize("max_length", [1, 16_384])
def test_start_input_max_length_is_preserved_in_the_runtime_contract(
    max_length: int,
) -> None:
    graph = _graph()
    graph["nodes"][0]["data"]["variables"][0]["max_length"] = max_length

    contract = validate_conversation_memory_runtime(graph, _config())

    assert contract is not None
    assert contract.input_max_length == max_length
