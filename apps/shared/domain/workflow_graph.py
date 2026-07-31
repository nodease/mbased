from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

MAX_WORKFLOW_GRAPH_NODES = 1000
MAX_WORKFLOW_GRAPH_EDGES = 5000
MAX_WORKFLOW_GRAPH_NESTING_DEPTH = 16
MAX_WORKFLOW_NODE_ID_LENGTH = 255

SOURCE_ONLY_NODE_TYPES = frozenset({"startNode", "webhookTrigger", "scheduleTrigger"})
TERMINAL_NODE_TYPES = frozenset({"answerNode", "mailAcknowledgeNode"})
AUXILIARY_NODE_TYPES = frozenset({"note"})


@dataclass(frozen=True)
class WorkflowGraphValidationError(ValueError):
    code: str
    reference: str | None = None

    def __str__(self) -> str:
        return self.code


def is_valid_workflow_node_id(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_WORKFLOW_NODE_ID_LENGTH
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def validate_workflow_graph(graph: Any) -> None:
    """Validate executable workflow structure without framework dependencies."""
    _validate_graph_tree(graph, root_requires_source_node=True)


def validate_loop_subgraph(graph: Any) -> str:
    """Validate a Loop body and return its single executable entry node ID."""
    return _validate_graph_tree(graph, root_requires_source_node=False)


def _validate_graph_tree(
    graph: Any,
    *,
    root_requires_source_node: bool,
) -> str:
    if not isinstance(graph, Mapping):
        raise WorkflowGraphValidationError("workflow_graph_invalid")
    pending: list[tuple[Mapping[str, Any], int, bool]] = [
        (graph, 0, root_requires_source_node)
    ]
    total_nodes = 0
    total_edges = 0
    root_entry_id: str | None = None

    while pending:
        current, depth, require_source_node = pending.pop()
        if depth > MAX_WORKFLOW_GRAPH_NESTING_DEPTH:
            raise WorkflowGraphValidationError("workflow_graph_too_deep")

        nested, node_count, edge_count, entry_id = _validate_single_graph(
            current,
            require_source_node=require_source_node,
        )
        if depth == 0:
            root_entry_id = entry_id
        total_nodes += node_count
        total_edges += edge_count
        if total_nodes > MAX_WORKFLOW_GRAPH_NODES:
            raise WorkflowGraphValidationError("workflow_graph_too_many_nodes")
        if total_edges > MAX_WORKFLOW_GRAPH_EDGES:
            raise WorkflowGraphValidationError("workflow_graph_too_many_edges")
        pending.extend((subgraph, depth + 1, False) for subgraph in nested)
    if root_entry_id is None:
        raise WorkflowGraphValidationError("workflow_start_node_invalid")
    return root_entry_id


def _validate_single_graph(
    graph: Mapping[str, Any],
    *,
    require_source_node: bool,
) -> tuple[list[Mapping[str, Any]], int, int, str]:
    nodes = graph.get("nodes")
    edges = graph.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise WorkflowGraphValidationError("workflow_graph_invalid")

    node_by_id: dict[str, Mapping[str, Any]] = {}
    nested: list[Mapping[str, Any]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            raise WorkflowGraphValidationError("workflow_node_invalid")
        node_id = node.get("id")
        node_type = node.get("type")
        position = node.get("position")
        data = node.get("data")
        if (
            not is_valid_workflow_node_id(node_id)
            or node_id in node_by_id
            or not isinstance(node_type, str)
            or not node_type
            or not _valid_position(position)
            or not isinstance(data, Mapping)
        ):
            raise WorkflowGraphValidationError("workflow_node_invalid")
        node_by_id[node_id] = node
        subgraph = data.get("subGraph")
        if subgraph is not None:
            if not isinstance(subgraph, Mapping):
                raise WorkflowGraphValidationError("workflow_graph_invalid")
            nested.append(subgraph)

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_by_id}
    incoming_counts: dict[str, int] = {node_id: 0 for node_id in node_by_id}
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise WorkflowGraphValidationError("workflow_edge_invalid")
        source_id = edge.get("source")
        target_id = edge.get("target")
        reference = str(edge.get("id")) if edge.get("id") is not None else None
        if not isinstance(edge.get("id"), str) or not edge.get("id"):
            raise WorkflowGraphValidationError("workflow_edge_invalid", reference)
        if not isinstance(source_id, str) or not source_id:
            raise WorkflowGraphValidationError("workflow_edge_invalid", reference)
        if not isinstance(target_id, str) or not target_id:
            raise WorkflowGraphValidationError("workflow_edge_invalid", reference)
        source_node = node_by_id.get(source_id)
        target_node = node_by_id.get(target_id)
        if source_node is None:
            raise WorkflowGraphValidationError(
                "workflow_edge_source_missing", reference
            )
        if target_node is None:
            raise WorkflowGraphValidationError(
                "workflow_edge_target_missing", reference
            )
        if target_node.get("type") in SOURCE_ONLY_NODE_TYPES:
            raise WorkflowGraphValidationError(
                "workflow_edge_targets_source", reference
            )
        if source_node.get("type") in TERMINAL_NODE_TYPES:
            raise WorkflowGraphValidationError("workflow_edge_from_terminal", reference)
        adjacency[source_id].append(target_id)
        if source_node.get("type") not in AUXILIARY_NODE_TYPES:
            incoming_counts[target_id] += 1

    _reject_cycles(adjacency)
    executable_ids = {
        node_id
        for node_id, node in node_by_id.items()
        if node.get("type") not in AUXILIARY_NODE_TYPES
    }
    if require_source_node:
        entry_ids = [
            node_id
            for node_id, node in node_by_id.items()
            if node.get("type") in SOURCE_ONLY_NODE_TYPES
        ]
    else:
        entry_ids = [
            node_id for node_id in executable_ids if incoming_counts[node_id] == 0
        ]
    if len(entry_ids) != 1:
        raise WorkflowGraphValidationError("workflow_start_node_invalid")
    _reject_isolated_nodes(node_by_id, adjacency, entry_ids[0])
    return nested, len(nodes), len(edges), entry_ids[0]


def _valid_position(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    for coordinate in (value.get("x"), value.get("y")):
        if not isinstance(coordinate, (int, float)) or isinstance(coordinate, bool):
            return False
        try:
            finite = math.isfinite(coordinate)
        except OverflowError:
            return False
        if not finite:
            return False
    return True


def _reject_cycles(adjacency: Mapping[str, list[str]]) -> None:
    visited: set[str] = set()
    for start_id in adjacency:
        if start_id in visited:
            continue
        path: set[str] = {start_id}
        stack = [(start_id, iter(adjacency.get(start_id, [])))]
        while stack:
            node_id, children = stack[-1]
            try:
                child = next(children)
                if child in path:
                    raise WorkflowGraphValidationError("workflow_cycle_detected", child)
                if child not in visited:
                    path.add(child)
                    stack.append((child, iter(adjacency.get(child, []))))
            except StopIteration:
                path.discard(node_id)
                visited.add(node_id)
                stack.pop()


def _reject_isolated_nodes(
    node_by_id: Mapping[str, Mapping[str, Any]],
    adjacency: Mapping[str, list[str]],
    start_id: str,
) -> None:
    visited = {start_id}
    pending = [start_id]
    while pending:
        current = pending.pop()
        for child in adjacency.get(current, []):
            child_node = node_by_id[child]
            if (
                child_node.get("type") not in AUXILIARY_NODE_TYPES
                and child not in visited
            ):
                visited.add(child)
                pending.append(child)
    executable_ids = {
        node_id
        for node_id, node in node_by_id.items()
        if node.get("type") not in AUXILIARY_NODE_TYPES
    }
    if executable_ids - visited:
        raise WorkflowGraphValidationError("workflow_isolated_node")
