"""Pure contract for the first Conversation Memory workflow runtime.

The validator deliberately consumes the frozen raw graph.  Gateway preflight
and Workflow workers can therefore make the same fail-closed decision without
letting a permissive Pydantic adapter discard an active field first.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

MEMORY_CONTRACT_VERSION = "conversation-memory-v1"
MEMORY_MAPPING_VERSION = "conversation-mapping-v1"
MEMORY_POLICY_VERSION = "memory-policy-v1"
MEMORY_STORAGE_GENERATION = 1
MAX_MEMORY_TURNS = 20
MAX_MEMORY_CONTEXT_TOKENS = 4_096
MAX_MEMORY_TEXT_BYTES = 16_384

_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_JINJA_VARIABLE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_MEMORY_KEYS = frozenset(
    {
        "enabled",
        "channel",
        "readSource",
        "writeMode",
        "maxTurns",
        "maxContextTokens",
        "strategy",
        "failurePolicy",
        "selectedNodeIds",
        "summaryModelPolicy",
    }
)
_REQUIRED_MEMORY_KEYS = _MEMORY_KEYS - {
    "selectedNodeIds",
    "summaryModelPolicy",
}
_PARAMETER_KEYS = frozenset(
    {
        "temperature",
        "top_p",
        "max_tokens",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "n",
        "best_of",
    }
)


class ConversationMemoryRuntimeContractError(ValueError):
    """Typed internal failure; the message never contains graph values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ConversationMemoryNodePolicy:
    channel: str
    max_turns: int
    max_context_tokens: int
    strategy: str
    failure_policy: str


@dataclass(frozen=True, slots=True)
class ConversationMemoryRuntimeContract:
    contract_version: str
    storage_generation: int
    mapping_version: str
    memory_policy_version: str
    start_node_id: str
    input_variable: str
    input_max_length: int
    llm_node_id: str
    answer_node_id: str
    output_variable: str
    memory: ConversationMemoryNodePolicy


def conversation_memory_runtime_requested(
    graph_snapshot: Mapping[str, Any] | object,
    deployment_config: Mapping[str, Any] | object | None,
) -> bool:
    """Detect Memory-on intent without accepting an invalid contract.

    Public admission uses this before validation so a malformed Memory
    deployment cannot fall back to the legacy execution path.
    """

    config = deployment_config if isinstance(deployment_config, Mapping) else {}
    if config.get("conversation_memory") is not None:
        return True
    if not isinstance(graph_snapshot, Mapping):
        return False
    nodes = graph_snapshot.get("nodes")
    if not isinstance(nodes, list):
        return False
    for node in nodes:
        data = _node_data(node)
        if "memory" not in data:
            continue
        memory = data.get("memory")
        if isinstance(memory, Mapping):
            if "enabled" in memory and memory.get("enabled") is not False:
                return True
            continue
        if memory is not None:
            return True
    return False


def validate_conversation_memory_runtime(
    graph_snapshot: Mapping[str, Any],
    deployment_config: Mapping[str, Any] | None,
) -> ConversationMemoryRuntimeContract | None:
    """Return the canonical V1 contract, ``None`` for Memory-OFF, or fail.

    The V1 activation slice is intentionally narrow: one root Start, one fixed
    provider LLM, one Answer, and no source-producing or structured behavior.
    """

    if not isinstance(graph_snapshot, Mapping):
        raise _error("memory.graph_unsupported")
    nodes = graph_snapshot.get("nodes")
    edges = graph_snapshot.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise _error("memory.graph_unsupported")

    config = deployment_config if isinstance(deployment_config, Mapping) else {}
    raw_runtime = config.get("conversation_memory")
    enabled_memories = tuple(
        _node_data(node).get("memory")
        for node in nodes
        if isinstance(_node_data(node).get("memory"), Mapping)
        and _node_data(node)["memory"].get("enabled") is True
    )
    if raw_runtime is None:
        if enabled_memories:
            raise _error("memory.input_mapping_invalid")
        return None
    if not isinstance(raw_runtime, Mapping):
        raise _error("memory.input_mapping_invalid")

    runtime = _runtime_binding(raw_runtime)
    if len(nodes) != 3 or len(edges) != 2:
        raise _error("memory.graph_unsupported")
    by_type: dict[str, list[Mapping[str, Any]]] = {}
    ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, Mapping):
            raise _error("memory.graph_unsupported")
        node_id = node.get("id")
        node_type = node.get("type")
        if (
            not isinstance(node_id, str)
            or not node_id
            or node_id in ids
            or not isinstance(node_type, str)
        ):
            raise _error("memory.graph_unsupported")
        ids.add(node_id)
        by_type.setdefault(node_type, []).append(node)
    if any(
        len(by_type.get(node_type, [])) != 1
        for node_type in ("startNode", "llmNode", "answerNode")
    ) or set(by_type) != {"startNode", "llmNode", "answerNode"}:
        raise _error("memory.graph_unsupported")

    start = by_type["startNode"][0]
    llm = by_type["llmNode"][0]
    answer = by_type["answerNode"][0]
    start_id = str(start["id"])
    llm_id = str(llm["id"])
    answer_id = str(answer["id"])
    expected_edges = {(start_id, llm_id), (llm_id, answer_id)}
    actual_edges: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise _error("memory.graph_unsupported")
        source, target = edge.get("source"), edge.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise _error("memory.graph_unsupported")
        actual_edges.add((source, target))
    if actual_edges != expected_edges or len(actual_edges) != len(edges):
        raise _error("memory.graph_unsupported")

    input_node, input_variable = runtime["input"]
    output_node, output_variable = runtime["output"]
    if input_node != start_id:
        raise _error("memory.input_mapping_invalid")
    if output_node != answer_id:
        raise _error("memory.output_mapping_invalid")
    input_max_length = _validate_start(_node_data(start), input_variable)
    policy = _validate_llm(_node_data(llm), start_id, input_variable)
    _validate_answer(_node_data(answer), llm_id, output_variable)

    return ConversationMemoryRuntimeContract(
        contract_version=runtime["contract_version"],
        storage_generation=runtime["storage_generation"],
        mapping_version=runtime["mapping_version"],
        memory_policy_version=runtime["memory_policy_version"],
        start_node_id=start_id,
        input_variable=input_variable,
        input_max_length=input_max_length,
        llm_node_id=llm_id,
        answer_node_id=answer_id,
        output_variable=output_variable,
        memory=policy,
    )


def _runtime_binding(raw: Mapping[str, Any]) -> dict[str, Any]:
    if set(raw) != {
        "contract_version",
        "storage_generation",
        "mapping_version",
        "memory_policy_version",
        "input",
        "output",
    }:
        raise _error("memory.input_mapping_invalid")
    versions = (
        raw.get("contract_version"),
        raw.get("mapping_version"),
        raw.get("memory_policy_version"),
    )
    if any(not isinstance(value, str) or not _SAFE_VERSION.fullmatch(value) for value in versions):
        raise _error("memory.input_mapping_invalid")
    if (
        raw.get("contract_version") != MEMORY_CONTRACT_VERSION
        or raw.get("mapping_version") != MEMORY_MAPPING_VERSION
        or raw.get("memory_policy_version") != MEMORY_POLICY_VERSION
        or type(raw.get("storage_generation")) is not int
        or raw.get("storage_generation") != MEMORY_STORAGE_GENERATION
    ):
        raise _error("memory.input_mapping_invalid")
    result: dict[str, Any] = {
        "contract_version": raw["contract_version"],
        "storage_generation": raw["storage_generation"],
        "mapping_version": raw["mapping_version"],
        "memory_policy_version": raw["memory_policy_version"],
    }
    for direction in ("input", "output"):
        mapping = raw.get(direction)
        if not isinstance(mapping, Mapping) or set(mapping) != {"node_id", "variable"}:
            raise _error(f"memory.{direction}_mapping_invalid")
        node_id, variable = mapping.get("node_id"), mapping.get("variable")
        if (
            not isinstance(node_id, str)
            or not node_id
            or not isinstance(variable, str)
            or not _SAFE_IDENTIFIER.fullmatch(variable)
        ):
            raise _error(f"memory.{direction}_mapping_invalid")
        result[direction] = (node_id, variable)
    return result


def _validate_start(data: Mapping[str, Any], input_variable: str) -> int:
    variables = data.get("variables")
    if not isinstance(variables, list) or len(variables) != 1:
        raise _error("memory.input_mapping_invalid")
    variable = variables[0]
    if not isinstance(variable, Mapping):
        raise _error("memory.input_mapping_invalid")
    if (
        variable.get("name") != input_variable
        or variable.get("type") not in {"text", "paragraph"}
        or variable.get("required") is not True
    ):
        raise _error("memory.input_mapping_invalid")
    max_length = variable.get("max_length")
    if max_length is not None and (
        type(max_length) is not int or not 1 <= max_length <= MAX_MEMORY_TEXT_BYTES
    ):
        raise _error("memory.input_mapping_invalid")
    return max_length if max_length is not None else MAX_MEMORY_TEXT_BYTES


def _validate_llm(
    data: Mapping[str, Any], start_node_id: str, input_variable: str
) -> ConversationMemoryNodePolicy:
    model_id = data.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise _error("memory.llm_behavior_unsupported")
    if data.get("task_type", "generate") != "generate":
        raise _error("memory.llm_behavior_unsupported")
    if data.get("auto_model_routing", False) is not False:
        raise _error("memory.llm_behavior_unsupported")
    if _active(data.get("fallback_model_id")):
        raise _error("memory.llm_behavior_unsupported")
    if _active(data.get("model_routing_context")) or _active(
        data.get("model_routing_task_description")
    ):
        raise _error("memory.llm_behavior_unsupported")
    if _active(data.get("context_variable")):
        raise _error("memory.llm_behavior_unsupported")
    if data.get("queryRewriteMode", "off") != "off":
        raise _error("memory.llm_behavior_unsupported")
    for key in ("knowledgeBases", "knowledgeCollections", "tools", "retrievers"):
        if _active(data.get(key)):
            raise _error("memory.llm_behavior_unsupported")
    output_format = data.get("output_format")
    if output_format not in (None, {}) and output_format != {"type": "text"}:
        raise _error("memory.llm_behavior_unsupported")

    references = data.get("referenced_variables", [])
    if not isinstance(references, list) or len(references) != 1:
        raise _error("memory.input_mapping_invalid")
    reference = references[0]
    if not isinstance(reference, Mapping) or set(reference) != {
        "name",
        "value_selector",
    }:
        raise _error("memory.input_mapping_invalid")
    if reference.get("name") != input_variable or reference.get(
        "value_selector"
    ) != [start_node_id, input_variable]:
        raise _error("memory.input_mapping_invalid")
    system_prompt = data.get("system_prompt") or ""
    user_prompt = data.get("user_prompt") or ""
    assistant_prompt = data.get("assistant_prompt") or ""
    if any(
        not isinstance(prompt, str)
        for prompt in (system_prompt, user_prompt, assistant_prompt)
    ):
        raise _error("memory.llm_behavior_unsupported")
    if (
        _JINJA_VARIABLE.findall(system_prompt)
        or _JINJA_VARIABLE.findall(assistant_prompt)
        or set(_JINJA_VARIABLE.findall(user_prompt)) != {input_variable}
    ):
        raise _error("memory.input_mapping_invalid")

    parameters = data.get("parameters", {})
    if not isinstance(parameters, Mapping) or not set(parameters).issubset(
        _PARAMETER_KEYS
    ):
        raise _error("memory.llm_parameters_unsupported")
    _validate_parameters(parameters)
    memory = data.get("memory")
    if (
        not isinstance(memory, Mapping)
        or not _REQUIRED_MEMORY_KEYS.issubset(memory)
        or not set(memory).issubset(_MEMORY_KEYS)
    ):
        raise _error("memory.memory_policy_unsupported")
    if (
        memory.get("enabled") is not True
        or memory.get("channel") != "conversation"
        or memory.get("readSource") != "conversation_turns"
        or memory.get("writeMode") != "none"
        or memory.get("strategy") != "window"
        or memory.get("failurePolicy") != "fail_node"
        or memory.get("selectedNodeIds", []) != []
        or memory.get("summaryModelPolicy", "inherit_node") != "inherit_node"
    ):
        raise _error("memory.memory_policy_unsupported")
    max_turns = memory.get("maxTurns")
    max_tokens = memory.get("maxContextTokens")
    if (
        type(max_turns) is not int
        or not 1 <= max_turns <= MAX_MEMORY_TURNS
        or type(max_tokens) is not int
        or not 1 <= max_tokens <= MAX_MEMORY_CONTEXT_TOKENS
    ):
        raise _error("memory.memory_policy_unsupported")
    return ConversationMemoryNodePolicy(
        channel="conversation",
        max_turns=max_turns,
        max_context_tokens=max_tokens,
        strategy="window",
        failure_policy="fail_node",
    )


def _validate_parameters(parameters: Mapping[str, Any]) -> None:
    for key in ("n", "best_of"):
        if key in parameters and (
            type(parameters[key]) is not int or parameters[key] != 1
        ):
            raise _error("memory.llm_parameters_unsupported")
    _bounded_number(parameters, "temperature", 0, 2)
    _bounded_number(parameters, "top_p", 0, 1, lower_exclusive=True)
    _bounded_number(parameters, "presence_penalty", -2, 2)
    _bounded_number(parameters, "frequency_penalty", -2, 2)
    if "max_tokens" in parameters and (
        type(parameters["max_tokens"]) is not int
        or not 1 <= parameters["max_tokens"] <= 32_768
    ):
        raise _error("memory.llm_parameters_unsupported")
    if "stop" in parameters:
        stop = parameters["stop"]
        values = [stop] if isinstance(stop, str) else stop
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 4
            or any(
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 256
                for value in values
            )
        ):
            raise _error("memory.llm_parameters_unsupported")


def _bounded_number(
    values: Mapping[str, Any],
    key: str,
    minimum: float,
    maximum: float,
    *,
    lower_exclusive: bool = False,
) -> None:
    if key not in values:
        return
    value = values[key]
    valid_type = not isinstance(value, bool) and isinstance(value, (int, float))
    valid_lower = value > minimum if valid_type and lower_exclusive else value >= minimum if valid_type else False
    if not valid_type or not math.isfinite(value) or not valid_lower or value > maximum:
        raise _error("memory.llm_parameters_unsupported")


def _validate_answer(
    data: Mapping[str, Any], llm_node_id: str, output_variable: str
) -> None:
    outputs = data.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 1:
        raise _error("memory.output_mapping_invalid")
    output = outputs[0]
    if not isinstance(output, Mapping) or output.get("variable") != output_variable:
        raise _error("memory.output_mapping_invalid")
    if output.get("value_selector") != [llm_node_id, "text"]:
        raise _error("memory.output_mapping_invalid")


def _node_data(node: Any) -> Mapping[str, Any]:
    if not isinstance(node, Mapping):
        return {}
    data = node.get("data")
    return data if isinstance(data, Mapping) else {}


def _active(value: Any) -> bool:
    return value not in (None, "", [], {}, False)


def _error(code: str) -> ConversationMemoryRuntimeContractError:
    return ConversationMemoryRuntimeContractError(code)


__all__ = [
    "ConversationMemoryNodePolicy",
    "ConversationMemoryRuntimeContract",
    "ConversationMemoryRuntimeContractError",
    "MEMORY_CONTRACT_VERSION",
    "MEMORY_MAPPING_VERSION",
    "MEMORY_POLICY_VERSION",
    "MEMORY_STORAGE_GENERATION",
    "MAX_MEMORY_CONTEXT_TOKENS",
    "MAX_MEMORY_TEXT_BYTES",
    "MAX_MEMORY_TURNS",
    "conversation_memory_runtime_requested",
    "validate_conversation_memory_runtime",
]
