from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.shared.domain.workflow_graph import (
    AUXILIARY_NODE_TYPES,
    MAX_WORKFLOW_GRAPH_NESTING_DEPTH,
)
from apps.shared.services.workflow_node_catalog import (
    missing_required_configuration,
    node_definition,
    node_output_keys,
    node_parameter_definitions,
    node_parameter_is_configured,
    stored_parameter_value_for_validation,
    validate_node_parameter_value,
)

SelectorKeys = frozenset[str] | None


@dataclass(frozen=True)
class WorkflowConfigurationIssue:
    node_id: str
    node_type: str
    missing_parameters: tuple[str, ...]


class WorkflowConfigurationPreflightError(ValueError):
    def __init__(
        self, surface: str, issues: list[WorkflowConfigurationIssue]
    ) -> None:
        self.surface = surface
        self.issues = issues
        super().__init__("workflow_configuration_unresolved")


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _upstream_node_ids(graph: dict[str, Any], target_node_id: str) -> set[str]:
    incoming: dict[str, list[str]] = {}
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if source is None or target is None:
            continue
        incoming.setdefault(str(target), []).append(str(source))

    upstream: set[str] = set()
    pending = list(incoming.get(target_node_id, []))
    while pending:
        source_id = pending.pop()
        if source_id == target_node_id or source_id in upstream:
            continue
        upstream.add(source_id)
        pending.extend(incoming.get(source_id, []))
    return upstream


def _implicit_entry_node_id(
    graph: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
) -> str | None:
    executable_node_ids = {
        node_id
        for node_id, node in node_by_id.items()
        if node.get("type") not in AUXILIARY_NODE_TYPES
    }
    incoming_node_ids: set[str] = set()
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source_id = str(edge.get("source") or "")
        target_id = str(edge.get("target") or "")
        source = node_by_id.get(source_id)
        if (
            source is not None
            and source.get("type") not in AUXILIARY_NODE_TYPES
            and target_id in executable_node_ids
        ):
            incoming_node_ids.add(target_id)
    entry_node_ids = executable_node_ids - incoming_node_ids
    if len(entry_node_ids) != 1:
        return None
    return next(iter(entry_node_ids))


def workflow_configuration_issues(
    graph: dict[str, Any] | None,
) -> list[WorkflowConfigurationIssue]:
    issues: list[WorkflowConfigurationIssue] = []

    def parameter_selectors(value: Any) -> list[list[Any]]:
        if isinstance(value, list) and len(value) >= 2 and all(
            isinstance(item, str) for item in value
        ):
            return [value]
        if not isinstance(value, list):
            return []
        selectors: list[list[Any]] = []
        for item in value:
            if isinstance(item, list):
                selectors.append(item)
            elif isinstance(item, dict) and isinstance(item.get("value_selector"), list):
                selectors.append(item["value_selector"])
        return selectors

    if not isinstance(graph, dict):
        return issues

    pending: list[
        tuple[dict[str, Any], int, dict[str, SelectorKeys], str | None]
    ] = [(graph, 0, {}, None)]
    while pending:
        current, depth, inherited_sources, owner_loop_id = pending.pop()
        if depth > MAX_WORKFLOW_GRAPH_NESTING_DEPTH:
            issues.append(
                WorkflowConfigurationIssue(
                    node_id=owner_loop_id or "",
                    node_type="loopNode",
                    missing_parameters=("subGraph",),
                )
            )
            continue

        nodes = [
            node
            for node in current.get("nodes") or []
            if isinstance(node, dict)
        ]
        node_by_id = {
            str(node.get("id")): node for node in nodes if node.get("id")
        }
        inherited_source_consumer_id = (
            _implicit_entry_node_id(current, node_by_id)
            if inherited_sources
            else None
        )

        local_sources: dict[str, SelectorKeys] = {
            node_id: frozenset(
                str(key)
                for key in node_output_keys(
                    str(node.get("type") or ""),
                    node.get("data") if isinstance(node.get("data"), dict) else {},
                )
            )
            for node_id, node in node_by_id.items()
        }

        def selector_valid(
            selector: Any,
            *,
            allowed_local_source_ids: set[str] | None = None,
            allow_inherited_sources: bool = True,
        ) -> bool:
            if not isinstance(selector, list) or len(selector) < 2:
                return False
            source_id = str(selector[0])
            if source_id in local_sources:
                if (
                    allowed_local_source_ids is not None
                    and source_id not in allowed_local_source_ids
                ):
                    return False
                allowed_keys = local_sources[source_id]
            elif source_id in inherited_sources:
                if not allow_inherited_sources:
                    return False
                allowed_keys = inherited_sources[source_id]
            else:
                return False
            return allowed_keys is None or str(selector[1]) in allowed_keys

        for node in nodes:
            node_id = str(node.get("id") or "")
            allows_inherited_sources = (
                not inherited_sources or node_id == inherited_source_consumer_id
            )
            node_type = str(node.get("type") or "")
            definition = node_definition(node_type)
            data = node.get("data") if isinstance(node.get("data"), dict) else {}
            if definition is not None and definition.get("side_effect") in {
                "external_read",
                "external_write",
                "local_execution",
            }:
                deferred = {
                    str(key)
                    for key in data.get("_deferred_parameters", [])
                    if isinstance(key, str)
                }
                missing_items: list[str] = []
                for key in definition.get("required_configuration") or []:
                    parameter_key = str(key)
                    validation_value = stored_parameter_value_for_validation(
                        node_type,
                        parameter_key,
                        data,
                    )
                    invalid = (
                        parameter_key in deferred
                        or not node_parameter_is_configured(
                            node_type, parameter_key, data
                        )
                        or (
                            node_type not in {"githubNode", "slackPostNode"}
                            and bool(
                                validate_node_parameter_value(
                                    node_type,
                                    parameter_key,
                                    validation_value,
                                )
                            )
                        )
                    )
                    parameter = next(
                        (
                            item
                            for item in node_parameter_definitions(node_type)
                            if str(item.get("key")) == parameter_key
                        ),
                        None,
                    )
                    if parameter and parameter.get("input_type") in {
                        "variable_selector",
                        "variable_selector_list",
                    }:
                        selectors = parameter_selectors(validation_value)
                        invalid = invalid or not selectors or not all(
                            selector_valid(
                                selector,
                                allow_inherited_sources=allows_inherited_sources,
                            )
                            for selector in selectors
                        )
                    if invalid:
                        missing_items.append(parameter_key)
                for parameter_key in missing_required_configuration(node_type, data):
                    if parameter_key not in missing_items:
                        missing_items.append(parameter_key)
                missing = tuple(missing_items)
                if missing:
                    issues.append(
                        WorkflowConfigurationIssue(
                            node_id=node_id,
                            node_type=node_type,
                            missing_parameters=missing,
                        )
                    )

            subgraph = data.get("subGraph") if node_type == "loopNode" else None
            if isinstance(subgraph, dict):
                upstream_node_ids = _upstream_node_ids(current, node_id)
                inherited_child_sources = (
                    inherited_sources if allows_inherited_sources else {}
                )
                child_sources = {
                    **inherited_child_sources,
                    **{
                        source_id: output_keys
                        for source_id, output_keys in local_sources.items()
                        if source_id in upstream_node_ids
                    },
                }
                mappings = data.get("inputs")
                invalid_mapping = False
                if isinstance(mappings, list):
                    for mapping in mappings:
                        if not isinstance(mapping, dict):
                            invalid_mapping = True
                            continue
                        mapping_name = mapping.get("name")
                        if not (
                            isinstance(mapping_name, str) and mapping_name.strip()
                        ):
                            invalid_mapping = True
                            continue
                        mapping_selector = mapping.get("value_selector")
                        if mapping_selector in (None, []):
                            continue
                        if not selector_valid(
                            mapping_selector,
                            allowed_local_source_ids=upstream_node_ids,
                            allow_inherited_sources=allows_inherited_sources,
                        ):
                            invalid_mapping = True
                            continue
                        child_sources[mapping_name] = None
                elif mappings is not None:
                    invalid_mapping = True
                if invalid_mapping:
                    issues.append(
                        WorkflowConfigurationIssue(
                            node_id=node_id,
                            node_type=node_type,
                            missing_parameters=("inputs",),
                        )
                    )
                child_sources["loop"] = frozenset({"item", "index"})
                pending.append(
                    (
                        subgraph,
                        depth + 1,
                        child_sources,
                        node_id,
                    )
                )

    return issues


def enforce_workflow_configuration_preflight(
    graph: dict[str, Any] | None,
    *,
    surface: str,
) -> None:
    issues = workflow_configuration_issues(graph)
    if issues:
        raise WorkflowConfigurationPreflightError(surface, issues)
