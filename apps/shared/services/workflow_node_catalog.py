from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from apps.shared.services.workflow_node_secret_service import (
    is_workflow_node_secret_reference,
)

_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "workflow_node_catalog.json"
)

_CONNECTION_ROLES = {"entry", "intermediate", "branch", "terminal"}
_CONNECTION_CARDINALITIES = {"forbidden", "allowed", "required"}
_OUTGOING_HANDLE_POLICIES = {"standard", "condition_cases", "unrestricted"}
_PARAMETER_INPUT_TYPES = {
    "boolean",
    "code",
    "credential_ref",
    "json",
    "number",
    "resource_ref",
    "secret",
    "select",
    "text",
    "textarea",
    "variable_selector",
    "variable_selector_list",
}
_DEFER_POLICIES = {"forbidden", "allow_unresolved"}
_PARAMETER_SENSITIVITIES = {"safe", "reference_only", "secret_forbidden"}
_OUTPUT_MODES = {"static", "parameter_names"}
_STANDALONE_CREATION_POLICIES = {"allowed", "requires_context", "forbidden"}


@dataclass(frozen=True)
class WorkflowConnectionPolicyIssue:
    code: str
    edge_id: str | None
    source_node_id: str | None
    target_node_id: str | None


@lru_cache(maxsize=1)
def load_workflow_node_catalog() -> dict[str, Any]:
    catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    validate_workflow_node_catalog(catalog)
    return catalog


def validate_workflow_node_catalog(catalog: dict[str, Any]) -> None:
    nodes = catalog.get("nodes")
    if catalog.get("version") != 3 or not isinstance(nodes, list):
        raise RuntimeError("Invalid workflow node capability catalog")

    node_types = [str(node.get("node_type") or "") for node in nodes]
    if any(not node_type for node_type in node_types):
        raise RuntimeError("Workflow node catalog contains an empty node type")
    if len(node_types) != len(set(node_types)):
        raise RuntimeError("Workflow node catalog contains duplicate node types")

    capabilities = [
        str(capability)
        for node in nodes
        for capability in node.get("capabilities") or []
    ]
    if len(capabilities) != len(set(capabilities)):
        raise RuntimeError("Workflow node catalog contains duplicate capabilities")

    contracts = catalog.get("capability_contracts")
    if not isinstance(contracts, dict) or set(contracts) != set(capabilities):
        raise RuntimeError("Workflow node catalog capability contracts are incomplete")
    for capability, contract in contracts.items():
        if not isinstance(contract, dict):
            raise RuntimeError("Workflow node catalog capability contract is invalid")
        if not isinstance(contract.get("description"), str) or not contract[
            "description"
        ].strip():
            raise RuntimeError("Workflow node catalog capability description is invalid")
        aliases = contract.get("planner_aliases")
        if (
            not isinstance(aliases, list)
            or not aliases
            or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
            or len(aliases) != len(set(aliases))
        ):
            raise RuntimeError("Workflow node catalog capability aliases are invalid")
        if contract.get("standalone_creation") not in _STANDALONE_CREATION_POLICIES:
            raise RuntimeError(
                "Workflow node catalog standalone creation policy is invalid"
            )

    for node in nodes:
        policy = node.get("connection_policy")
        if not isinstance(policy, dict):
            raise RuntimeError("Workflow node catalog is missing connection policy")
        if policy.get("role") not in _CONNECTION_ROLES:
            raise RuntimeError("Workflow node catalog has an invalid connection role")
        if policy.get("incoming") not in _CONNECTION_CARDINALITIES:
            raise RuntimeError("Workflow node catalog has an invalid incoming policy")
        if policy.get("outgoing") not in _CONNECTION_CARDINALITIES:
            raise RuntimeError("Workflow node catalog has an invalid outgoing policy")
        if policy.get("outgoing_handles") not in _OUTGOING_HANDLE_POLICIES:
            raise RuntimeError("Workflow node catalog has an invalid handle policy")
        parameters = node.get("parameters")
        if not isinstance(parameters, list):
            raise RuntimeError("Workflow node catalog is missing parameter definitions")
        parameter_keys = [
            str(parameter.get("key") or "")
            for parameter in parameters
            if isinstance(parameter, dict)
        ]
        if len(parameter_keys) != len(parameters) or any(
            not key for key in parameter_keys
        ):
            raise RuntimeError("Workflow node catalog has an invalid parameter key")
        if len(parameter_keys) != len(set(parameter_keys)):
            raise RuntimeError("Workflow node catalog has duplicate parameter keys")
        required_configuration = node.get("required_configuration") or []
        if not isinstance(required_configuration, list) or not set(
            map(str, required_configuration)
        ).issubset(set(parameter_keys)):
            raise RuntimeError(
                "Workflow node catalog required configuration has no parameter"
            )
        required_any_configuration = node.get("required_any_configuration") or []
        if not isinstance(required_any_configuration, list):
            raise RuntimeError(
                "Workflow node catalog required any configuration is invalid"
            )
        for group in required_any_configuration:
            if not isinstance(group, dict) or not str(group.get("key") or ""):
                raise RuntimeError(
                    "Workflow node catalog required any configuration is invalid"
                )
            group_parameters = group.get("parameters")
            if (
                not isinstance(group_parameters, list)
                or not group_parameters
                or not set(map(str, group_parameters)).issubset(set(parameter_keys))
            ):
                raise RuntimeError(
                    "Workflow node catalog required any configuration has no parameter"
                )
        for parameter in parameters:
            if parameter.get("input_type") not in _PARAMETER_INPUT_TYPES:
                raise RuntimeError("Workflow node catalog has an invalid input type")
            if not isinstance(parameter.get("required"), bool):
                raise RuntimeError("Workflow node catalog parameter required is invalid")
            if (
                "agent_builder_task" in parameter
                and not isinstance(parameter["agent_builder_task"], bool)
            ):
                raise RuntimeError(
                    "Workflow node catalog agent builder task flag is invalid"
                )
            if parameter.get("defer_policy", "forbidden") not in _DEFER_POLICIES:
                raise RuntimeError("Workflow node catalog has an invalid defer policy")
            if parameter.get("sensitivity", "safe") not in _PARAMETER_SENSITIVITIES:
                raise RuntimeError("Workflow node catalog parameter sensitivity is invalid")
            for guidance_key in ("reason", "input_guidance"):
                if guidance_key in parameter and (
                    not isinstance(parameter[guidance_key], str)
                    or not parameter[guidance_key].strip()
                ):
                    raise RuntimeError(
                        "Workflow node catalog parameter guidance is invalid"
                    )
            validation = parameter.get("validation", {})
            if not isinstance(validation, dict):
                raise RuntimeError("Workflow node catalog parameter validation is invalid")
            for condition_key in ("visible_when", "required_when"):
                condition = validation.get(condition_key)
                if condition is None:
                    continue
                if (
                    not isinstance(condition, dict)
                    or str(condition.get("parameter_key") or "")
                    not in parameter_keys
                    or "equals" not in condition
                ):
                    raise RuntimeError(
                        "Workflow node catalog parameter condition is invalid"
                    )
        outputs = node.get("outputs")
        if not isinstance(outputs, list):
            raise RuntimeError("Workflow node catalog is missing output contracts")
        output_capabilities: set[str] = set()
        for output in outputs:
            if not isinstance(output, dict):
                raise RuntimeError("Workflow node catalog output contract is invalid")
            capability = str(output.get("capability") or "")
            if capability not in node.get("capabilities", []):
                raise RuntimeError("Workflow node catalog output capability is invalid")
            if capability in output_capabilities:
                raise RuntimeError("Workflow node catalog has duplicate output contracts")
            output_capabilities.add(capability)
            mode = output.get("mode")
            if mode not in _OUTPUT_MODES:
                raise RuntimeError("Workflow node catalog output mode is invalid")
            if mode == "static" and not all(
                isinstance(key, str) and key for key in output.get("keys", [])
            ):
                raise RuntimeError("Workflow node catalog static output keys are invalid")
            if mode == "parameter_names":
                if output.get("parameter_key") not in parameter_keys:
                    raise RuntimeError(
                        "Workflow node catalog dynamic output parameter is invalid"
                    )
                if not isinstance(output.get("name_key"), str):
                    raise RuntimeError(
                        "Workflow node catalog dynamic output name key is invalid"
                    )
        if set(node.get("capabilities") or []) != output_capabilities:
            raise RuntimeError("Workflow node catalog is missing capability outputs")


def implemented_node_types() -> set[str]:
    return {
        str(node["node_type"])
        for node in load_workflow_node_catalog()["nodes"]
        if node.get("implemented") is True
    }


def agent_builder_supported_node_types() -> set[str]:
    return {
        str(node["node_type"])
        for node in load_workflow_node_catalog()["nodes"]
        if node.get("implemented") is True
        and node.get("agent_builder_supported") is True
    }


def agent_builder_supported_capabilities() -> set[str]:
    return {
        str(capability)
        for node in load_workflow_node_catalog()["nodes"]
        if node.get("implemented") is True
        and node.get("agent_builder_supported") is True
        for capability in node.get("capabilities") or []
    }


def node_side_effect_mapping():
    return MappingProxyType(
        {
            str(node["node_type"]): str(node.get("side_effect") or "external_write")
            for node in load_workflow_node_catalog()["nodes"]
        }
    )


def node_type_for_capability(capability: str) -> str | None:
    for node in load_workflow_node_catalog()["nodes"]:
        if capability in (node.get("capabilities") or []):
            return str(node["node_type"])
    return None


def capability_contract(capability: str) -> dict[str, Any] | None:
    contract = load_workflow_node_catalog()["capability_contracts"].get(capability)
    return copy.deepcopy(contract) if isinstance(contract, dict) else None


def node_definition(node_type: str) -> dict[str, Any] | None:
    for node in load_workflow_node_catalog()["nodes"]:
        if node.get("node_type") == node_type:
            return node
    return None


def node_parameter_definitions(node_type: str) -> list[dict[str, Any]]:
    definition = node_definition(node_type)
    if definition is None:
        return []
    return [
        {
            **dict(parameter),
            "task_group": parameter.get("task_group")
            or parameter_task_group(node_type, str(parameter.get("key") or "")),
            "defer_policy": parameter.get("defer_policy", "forbidden"),
            "agent_builder_task": parameter.get("agent_builder_task", True),
            "sensitivity": parameter.get(
                "sensitivity",
                "reference_only"
                if parameter.get("input_type") in {"resource_ref", "credential_ref"}
                else "safe",
            ),
            "validation": dict(parameter.get("validation") or {}),
        }
        for parameter in definition.get("parameters") or []
        if isinstance(parameter, dict)
    ]


def parameter_definition(node_type: str, parameter_key: str) -> dict[str, Any] | None:
    return next(
        (
            parameter
            for parameter in node_parameter_definitions(node_type)
            if str(parameter.get("key")) == parameter_key
        ),
        None,
    )


def _parameter_condition_matches(
    node_type: str,
    condition: Any,
    node_data: dict[str, Any] | None,
) -> bool:
    if not isinstance(condition, dict):
        return False
    controlling_key = condition.get("parameter_key")
    if not isinstance(controlling_key, str) or not controlling_key:
        return False
    found, current_value = node_parameter_value(
        node_type,
        controlling_key,
        node_data,
    )
    return found and current_value == condition.get("equals")


def node_parameter_is_required(
    node_type: str,
    parameter_key: str,
    node_data: dict[str, Any] | None,
) -> bool:
    parameter = parameter_definition(node_type, parameter_key)
    if parameter is None:
        return False
    if parameter.get("required") is True:
        return True
    validation = parameter.get("validation")
    return isinstance(validation, dict) and _parameter_condition_matches(
        node_type,
        validation.get("required_when"),
        node_data,
    )


def validate_node_parameter_value(
    node_type: str,
    parameter_key: str,
    value: Any,
) -> list[str]:
    parameter = parameter_definition(node_type, parameter_key)
    if parameter is None:
        return ["unknown_parameter"]
    input_type = str(parameter.get("input_type") or "")
    validation = dict(parameter.get("validation") or {})
    if value is None:
        return ["required"] if parameter.get("required") else []
    if (
        node_type == "llmNode"
        and parameter_key == "output_json_schema"
        and not isinstance(value, dict)
    ):
        return ["json_object_required"]
    if (
        node_type == "slackPostNode"
        and parameter_key in {"blocks", "attachments"}
        and not isinstance(value, list)
    ):
        return ["json_array_required"]
    if input_type in {"text", "textarea", "code", "secret"}:
        if not isinstance(value, str):
            return ["invalid_type"]
        if input_type == "secret" and is_workflow_node_secret_reference(value):
            return []
        if len(value) < int(validation.get("min_length", 0)):
            return ["too_short"]
        if len(value) > int(validation.get("max_length", 1_000_000)):
            return ["too_long"]
        pattern = validation.get("pattern")
        if pattern and re.fullmatch(str(pattern), value) is None:
            return ["pattern_mismatch"]
    elif input_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return ["invalid_type"]
        if validation.get("integer") and not float(value).is_integer():
            return ["integer_required"]
        if "min" in validation and value < validation["min"]:
            return ["below_minimum"]
        if "max" in validation and value > validation["max"]:
            return ["above_maximum"]
    elif input_type == "boolean" and not isinstance(value, bool):
        return ["invalid_type"]
    elif input_type == "variable_selector":
        if (
            not isinstance(value, list)
            or len(value) < 2
            or len(value) > 32
            or any(not isinstance(item, str) or not item for item in value)
        ):
            return ["invalid_selector"]
    elif input_type == "variable_selector_list":
        if not isinstance(value, list) or not value or len(value) > 32:
            return ["invalid_selector_list"]
        if any(
                len(selector) < 2
                or len(selector) > 32
                or any(not isinstance(item, str) or not item for item in selector)
                for selector in value
                if isinstance(selector, list)
            ) or any(not isinstance(selector, list) for selector in value):
            return ["invalid_selector_list"]
        selectors = [tuple(selector) for selector in value]
        if len(selectors) != len(set(selectors)):
            return ["invalid_selector_list"]
    options = validation.get("options")
    if isinstance(options, list) and value not in options:
        return ["option_not_allowed"]
    return []


def capability_output_contract(capability: str) -> dict[str, Any] | None:
    for node in load_workflow_node_catalog()["nodes"]:
        for output in node.get("outputs") or []:
            if output.get("capability") == capability:
                return dict(output)
    return None


def capability_output_keys(
    capability: str,
    node_data: dict[str, Any] | None,
) -> list[str]:
    contract = capability_output_contract(capability)
    if contract is None:
        return []
    if contract.get("mode") == "static":
        return list(dict.fromkeys(str(key) for key in contract.get("keys") or []))

    data = node_data if isinstance(node_data, dict) else {}
    values = data.get(str(contract.get("parameter_key") or ""))
    if not isinstance(values, list):
        return []
    name_key = str(contract.get("name_key") or "")
    return list(
        dict.fromkeys(
            str(item[name_key])
            for item in values
            if isinstance(item, dict) and item.get(name_key)
        )
    )


def node_output_keys(
    node_type: str,
    node_data: dict[str, Any] | None,
) -> list[str]:
    definition = node_definition(node_type)
    if definition is None:
        return []
    return list(
        dict.fromkeys(
            key
            for capability in definition.get("capabilities") or []
            for key in capability_output_keys(str(capability), node_data)
        )
    )


def classify_catalog_version(catalog_version: Any) -> str:
    return "current" if catalog_version == 3 else "legacy_stale"


def _configuration_value_is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


_LLM_ROUTING_PARAMETER_PATHS = {
    "model_routing_refresh_every_runs": ("refresh", "refresh_every_runs"),
    "model_routing_validation_budget_usd": ("validation_budget_usd",),
    "model_routing_max_cohorts": ("max_cohorts",),
}

_LLM_BASIC_PARAMETER_PATHS = {
    "output_format_type": ("output_format", "type"),
    "output_json_schema": ("output_format", "schema"),
}

LLM_ROUTING_GRAPH_PARAMETER_KEYS = (
    "model_id",
    "auto_model_routing",
    "fallback_model_id",
    *_LLM_ROUTING_PARAMETER_PATHS.keys(),
)


def parameter_task_group(node_type: str, parameter_key: str) -> str | None:
    if node_type == "llmNode" and parameter_key == "auto_model_routing":
        return "model_routing"
    return None


def node_parameter_value(
    node_type: str,
    parameter_key: str,
    node_data: dict[str, Any] | None,
) -> tuple[bool, Any]:
    data = node_data if isinstance(node_data, dict) else {}
    if (
        node_type == "githubNode"
        and parameter_key == "action"
        and "action" not in data
    ):
        return True, "get_pr"
    if node_type == "llmNode" and parameter_key in _LLM_BASIC_PARAMETER_PATHS:
        value: Any = data
        for path_key in _LLM_BASIC_PARAMETER_PATHS[parameter_key]:
            if not isinstance(value, dict) or path_key not in value:
                return False, None
            value = value[path_key]
        return True, value
    if node_type == "llmNode" and parameter_key == "referenced_variables":
        values = data.get("referenced_variables")
        if not isinstance(values, list):
            return False, None
        selectors = []
        for item in values:
            if not isinstance(item, dict) or not isinstance(
                item.get("value_selector"), list
            ):
                return False, None
            selectors.append(copy.deepcopy(item["value_selector"]))
        return True, selectors
    if node_type == "slackPostNode" and parameter_key in {"blocks", "attachments"}:
        if parameter_key not in data:
            return False, None
        value = data.get(parameter_key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return False, None
        return (True, value) if isinstance(value, list) else (False, None)
    if parameter_key in data:
        return True, data.get(parameter_key)
    if node_type == "llmNode" and parameter_key in _LLM_ROUTING_PARAMETER_PATHS:
        value: Any = data.get("model_routing_policy")
        for path_key in _LLM_ROUTING_PARAMETER_PATHS[parameter_key]:
            if not isinstance(value, dict) or path_key not in value:
                return False, None
            value = value[path_key]
        return True, value
    if node_type == "slackPostNode" and parameter_key == "bot_token":
        auth_config = data.get("authConfig")
        if isinstance(auth_config, dict):
            return ("token" in auth_config), auth_config.get("token")
    return False, None


def node_parameter_is_configured(
    node_type: str,
    parameter_key: str,
    node_data: dict[str, Any] | None,
) -> bool:
    found, value = node_parameter_value(node_type, parameter_key, node_data)
    return found and _configuration_value_is_present(value)


def apply_node_parameter_value(
    node_type: str,
    parameter_key: str,
    node_data: dict[str, Any],
    value: Any,
) -> dict[str, Any]:
    data = copy.deepcopy(node_data)
    if node_type == "slackPostNode" and parameter_key == "slackMode":
        data[parameter_key] = copy.deepcopy(value)
        if value == "webhook":
            data = remove_node_parameter_value(
                node_type,
                "bot_token",
                data,
            )
            data = remove_node_parameter_value(node_type, "channel", data)
        elif value == "api":
            data = remove_node_parameter_value(node_type, "url", data)
        return data
    if node_type == "llmNode" and parameter_key in _LLM_BASIC_PARAMETER_PATHS:
        path = _LLM_BASIC_PARAMETER_PATHS[parameter_key]
        target = data
        for path_key in path[:-1]:
            child = target.get(path_key)
            if not isinstance(child, dict):
                child = {}
            child = copy.deepcopy(child)
            target[path_key] = child
            target = child
        target[path[-1]] = copy.deepcopy(value)
        data.pop(parameter_key, None)
        return data
    if node_type == "llmNode" and parameter_key == "referenced_variables":
        selectors = copy.deepcopy(value)
        existing_names = {
            tuple(item["value_selector"]): str(item["name"])
            for item in data.get(parameter_key) or []
            if isinstance(item, dict)
            and item.get("name")
            and isinstance(item.get("value_selector"), list)
        }
        data[parameter_key] = [
            {
                "name": existing_names.get(
                    tuple(selector), str(selector[-1])
                ),
                "value_selector": selector,
            }
            for selector in selectors
        ]
        return data
    if node_type == "llmNode" and parameter_key in _LLM_ROUTING_PARAMETER_PATHS:
        policy = data.get("model_routing_policy")
        if not isinstance(policy, dict):
            policy = {}
        policy = copy.deepcopy(policy)
        target = policy
        path = _LLM_ROUTING_PARAMETER_PATHS[parameter_key]
        for path_key in path[:-1]:
            child = target.get(path_key)
            if not isinstance(child, dict):
                child = {}
            child = copy.deepcopy(child)
            target[path_key] = child
            target = child
        target[path[-1]] = copy.deepcopy(value)
        data["model_routing_policy"] = policy
        data.pop(parameter_key, None)
        return data
    if node_type == "slackPostNode" and parameter_key == "bot_token":
        auth_config = data.get("authConfig")
        if not isinstance(auth_config, dict):
            auth_config = {}
        auth_config = copy.deepcopy(auth_config)
        auth_config["token"] = copy.deepcopy(value)
        data["authConfig"] = auth_config
        data.pop("bot_token", None)
        return data
    if node_type == "slackPostNode" and parameter_key in {"blocks", "attachments"}:
        data[parameter_key] = json.dumps(value, ensure_ascii=False)
        return data
    if node_type == "githubNode" and parameter_key == "pr_number":
        data[parameter_key] = str(int(value))
        return data
    if node_type == "fileExtractionNode" and parameter_key == "referenced_variables":
        selector = copy.deepcopy(value)
        data[parameter_key] = [
            {
                "name": str(selector[-1]),
                "value_selector": selector,
            }
        ]
        return data
    data[parameter_key] = copy.deepcopy(value)
    if node_type == "slackPostNode" and parameter_key == "channel":
        body = data.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except (TypeError, ValueError):
                body = {}
        if not isinstance(body, dict):
            body = {}
        body["channel"] = value
        data["body"] = json.dumps(body, ensure_ascii=False)
    return data


def remove_node_parameter_value(
    node_type: str,
    parameter_key: str,
    node_data: dict[str, Any],
) -> dict[str, Any]:
    data = copy.deepcopy(node_data)

    def remove_nested_value(target: dict[str, Any], path: tuple[str, ...]) -> None:
        parents: list[tuple[dict[str, Any], str]] = []
        current = target
        for path_key in path[:-1]:
            child = current.get(path_key)
            if not isinstance(child, dict):
                return
            parents.append((current, path_key))
            current = child
        current.pop(path[-1], None)
        for parent, path_key in reversed(parents):
            child = parent.get(path_key)
            if isinstance(child, dict) and not child:
                parent.pop(path_key, None)

    if node_type == "llmNode" and parameter_key in _LLM_BASIC_PARAMETER_PATHS:
        remove_nested_value(data, _LLM_BASIC_PARAMETER_PATHS[parameter_key])
        data.pop(parameter_key, None)
        return data
    if node_type == "llmNode" and parameter_key in _LLM_ROUTING_PARAMETER_PATHS:
        policy = data.get("model_routing_policy")
        if isinstance(policy, dict):
            policy = copy.deepcopy(policy)
            remove_nested_value(policy, _LLM_ROUTING_PARAMETER_PATHS[parameter_key])
            if policy:
                data["model_routing_policy"] = policy
            else:
                data.pop("model_routing_policy", None)
        data.pop(parameter_key, None)
        return data
    if node_type == "slackPostNode" and parameter_key == "bot_token":
        auth_config = data.get("authConfig")
        if isinstance(auth_config, dict):
            auth_config = copy.deepcopy(auth_config)
            auth_config.pop("token", None)
            if auth_config:
                data["authConfig"] = auth_config
            else:
                data.pop("authConfig", None)
        data.pop("bot_token", None)
        return data
    if node_type == "slackPostNode" and parameter_key == "channel":
        data.pop("channel", None)
        body = data.get("body")
        parsed_body = body
        if isinstance(body, str):
            try:
                parsed_body = json.loads(body)
            except (TypeError, ValueError):
                parsed_body = None
        if isinstance(parsed_body, dict):
            parsed_body = copy.deepcopy(parsed_body)
            parsed_body.pop("channel", None)
            if parsed_body:
                data["body"] = json.dumps(parsed_body, ensure_ascii=False)
            else:
                data.pop("body", None)
        return data
    data.pop(parameter_key, None)
    return data


def _stored_parameter_value_for_validation(
    node_type: str,
    parameter_key: str,
    node_data: dict[str, Any],
) -> Any:
    _, value = node_parameter_value(node_type, parameter_key, node_data)
    if node_type == "githubNode" and parameter_key == "pr_number":
        if isinstance(value, str) and value.isdigit():
            return int(value)
    if (
        node_type == "fileExtractionNode"
        and parameter_key == "referenced_variables"
        and isinstance(value, list)
        and len(value) == 1
        and isinstance(value[0], dict)
    ):
        return value[0].get("value_selector")
    return value


def stored_parameter_value_for_validation(
    node_type: str,
    parameter_key: str,
    node_data: dict[str, Any] | None,
) -> Any:
    data = node_data if isinstance(node_data, dict) else {}
    return _stored_parameter_value_for_validation(node_type, parameter_key, data)


def normalize_deferred_parameters(
    node_type: str,
    node_data: dict[str, Any] | None,
) -> dict[str, Any]:
    data = copy.deepcopy(node_data) if isinstance(node_data, dict) else {}
    raw_deferred = data.get("_deferred_parameters")
    if not isinstance(raw_deferred, list):
        return data

    deferred: list[str] = []
    for raw_key in raw_deferred:
        if not isinstance(raw_key, str) or raw_key in deferred:
            continue
        if parameter_definition(node_type, raw_key) is None:
            deferred.append(raw_key)
            continue
        validation_value = _stored_parameter_value_for_validation(
            node_type,
            raw_key,
            data,
        )
        if not node_parameter_is_configured(node_type, raw_key, data):
            deferred.append(raw_key)
            continue
        if validate_node_parameter_value(node_type, raw_key, validation_value):
            deferred.append(raw_key)

    if deferred:
        data["_deferred_parameters"] = deferred
    else:
        data.pop("_deferred_parameters", None)
    return data


def required_any_configuration_groups(
    node_type: str,
) -> list[tuple[str, tuple[str, ...]]]:
    definition = node_definition(node_type)
    if definition is None:
        return []
    groups: list[tuple[str, tuple[str, ...]]] = []
    for group in definition.get("required_any_configuration") or []:
        group_key = str(group.get("key") or "")
        parameters = tuple(str(key) for key in group.get("parameters") or [])
        if group_key and parameters:
            groups.append((group_key, parameters))
    return groups


def _parameter_satisfies_configuration(
    node_type: str,
    parameter_key: str,
    node_data: dict[str, Any],
    deferred: set[str],
) -> bool:
    if parameter_key in deferred:
        return False
    validation_value = _stored_parameter_value_for_validation(
        node_type,
        parameter_key,
        node_data,
    )
    return node_parameter_is_configured(
        node_type,
        parameter_key,
        node_data,
    ) and not validate_node_parameter_value(
        node_type,
        parameter_key,
        validation_value,
    )


def validate_node_parameter_update(
    node_type: str,
    parameter_key: str,
    node_data: dict[str, Any] | None,
    value: Any,
) -> list[str]:
    issues = validate_node_parameter_value(node_type, parameter_key, value)
    if issues:
        return issues
    if node_type == "llmNode" and parameter_key == "referenced_variables":
        updated_data = apply_node_parameter_value(
            node_type,
            parameter_key,
            node_data if isinstance(node_data, dict) else {},
            value,
        )
        names = [
            str(item.get("name") or "")
            for item in updated_data.get("referenced_variables") or []
            if isinstance(item, dict)
        ]
        if len(names) != len(set(names)):
            return ["duplicate_variable_name"]
        return []
    if node_type != "llmNode" or parameter_key not in {
        "model_id",
        "fallback_model_id",
    }:
        return []
    updated_data = apply_node_parameter_value(
        node_type,
        parameter_key,
        node_data if isinstance(node_data, dict) else {},
        value,
    )
    _, model_id = node_parameter_value(node_type, "model_id", updated_data)
    _, fallback_model_id = node_parameter_value(
        node_type,
        "fallback_model_id",
        updated_data,
    )
    if (
        _configuration_value_is_present(model_id)
        and _configuration_value_is_present(fallback_model_id)
        and str(model_id) == str(fallback_model_id)
    ):
        return ["fallback_must_differ"]
    return []


def missing_required_configuration(
    node_type: str, node_data: dict[str, Any] | None
) -> list[str]:
    definition = node_definition(node_type)
    if definition is None:
        return []
    data = node_data if isinstance(node_data, dict) else {}
    deferred = {
        str(key)
        for key in data.get("_deferred_parameters", [])
        if isinstance(key, str)
    }
    missing: list[str] = []
    required_keys = [
        str(key) for key in definition.get("required_configuration") or []
    ]
    for parameter in node_parameter_definitions(node_type):
        parameter_key = str(parameter.get("key") or "")
        if (
            parameter_key
            and parameter_key not in required_keys
            and node_parameter_is_required(node_type, parameter_key, data)
        ):
            required_keys.append(parameter_key)
    for key in required_keys:
        validation_value = _stored_parameter_value_for_validation(
            node_type,
            str(key),
            data,
        )
        if (
            str(key) in deferred
            or not node_parameter_is_configured(node_type, str(key), data)
            or validate_node_parameter_value(
                node_type,
                str(key),
                validation_value,
            )
        ):
            missing.append(str(key))
    for group_key, parameter_keys in required_any_configuration_groups(node_type):
        configured = any(
            _parameter_satisfies_configuration(
                node_type,
                parameter_key,
                data,
                deferred,
            )
            for parameter_key in parameter_keys
        )
        if not configured and group_key:
            missing.append(group_key)
    if node_type == "slackPostNode":
        mode = str(data.get("slackMode") or "api")
        mode_required = ["url"] if mode == "webhook" else ["bot_token", "channel"]
        for key in mode_required:
            if not _parameter_satisfies_configuration(
                node_type,
                key,
                data,
                deferred,
            ):
                missing.append(key)
    return list(dict.fromkeys(missing))


def derive_node_configuration_state(
    node_type: str, node_data: dict[str, Any] | None
) -> str:
    if node_definition(node_type) is None:
        return "unresolved"
    return (
        "unresolved"
        if missing_required_configuration(node_type, node_data)
        else "resolved"
    )


def connection_policy_for_node_type(node_type: str) -> dict[str, str] | None:
    definition = node_definition(node_type)
    if definition is None:
        return None
    policy = definition.get("connection_policy")
    return dict(policy) if isinstance(policy, dict) else None


def _condition_source_handles(node: dict[str, Any]) -> set[str]:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    cases = data.get("cases") if isinstance(data.get("cases"), list) else []
    return {
        "default",
        *(
            str(case.get("id"))
            for case in cases
            if isinstance(case, dict) and case.get("id")
        ),
    }


def validate_workflow_graph_connections(
    graph: dict[str, Any] | None,
) -> list[WorkflowConnectionPolicyIssue]:
    graph = graph or {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    node_by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    issues: list[WorkflowConnectionPolicyIssue] = []

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_id = str(edge.get("source") or "")
        target_id = str(edge.get("target") or "")
        source_node = node_by_id.get(source_id)
        target_node = node_by_id.get(target_id)
        if source_node is None or target_node is None:
            continue

        source_type = str(source_node.get("type") or "")
        target_type = str(target_node.get("type") or "")
        source_policy = connection_policy_for_node_type(source_type)
        target_policy = connection_policy_for_node_type(target_type)
        edge_id = str(edge.get("id")) if edge.get("id") is not None else None

        if target_policy and target_policy.get("incoming") == "forbidden":
            issues.append(
                WorkflowConnectionPolicyIssue(
                    code=(
                        "START_NODE_HAS_INCOMING_EDGE"
                        if target_type == "startNode"
                        else "TRIGGER_NODE_HAS_INCOMING_EDGE"
                    ),
                    edge_id=edge_id,
                    source_node_id=source_id,
                    target_node_id=target_id,
                )
            )
        if source_policy and source_policy.get("outgoing") == "forbidden":
            issues.append(
                WorkflowConnectionPolicyIssue(
                    code="TERMINAL_NODE_HAS_OUTGOING_EDGE",
                    edge_id=edge_id,
                    source_node_id=source_id,
                    target_node_id=target_id,
                )
            )
        if (
            source_policy
            and source_policy.get("outgoing_handles") == "condition_cases"
            and (
                not isinstance(edge.get("sourceHandle"), str)
                or not edge.get("sourceHandle")
                or edge["sourceHandle"]
                not in _condition_source_handles(source_node)
            )
        ):
            issues.append(
                WorkflowConnectionPolicyIssue(
                    code="INVALID_CONDITION_SOURCE_HANDLE",
                    edge_id=edge_id,
                    source_node_id=source_id,
                    target_node_id=target_id,
                )
            )

    return issues
