from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from apps.shared.schemas.agent_builder import (
    AddEdgeOperation,
    AddNodeOperation,
    GraphMutation,
    GraphMutationCompletionContext,
    GraphOperation,
    RemoveEdgeOperation,
    RemoveNodeOperation,
    ReplaceNodeDataOperation,
    ReplaceNodePositionOperation,
)
from apps.shared.schemas.workflow import EdgeSchema, NodeSchema, Position
from apps.shared.services.workflow_layout import calculate_workflow_auto_layout
from apps.shared.services.workflow_node_catalog import (
    derive_node_configuration_state,
    implemented_node_types,
    normalize_deferred_parameters,
    validate_workflow_graph_connections,
)


class GraphMutationValidationError(ValueError):
    pass


_OPERATION_ADAPTER = TypeAdapter(GraphOperation)
_OPERATION_ORDER = {
    "remove_edge": 0,
    "remove_node": 1,
    "add_node": 2,
    "replace_node_position": 3,
    "replace_node_data": 4,
    "add_edge": 5,
}
_EDITOR_RUNTIME_NODE_FIELDS = {
    "dragging",
    "height",
    "measured",
    "positionAbsolute",
    "resizing",
    "selected",
    "width",
}
_EDITOR_RUNTIME_EDGE_FIELDS = {"selected"}
_EDITOR_PRESENTATION_DATA_FIELDS = {
    "displayNumber",
    "observability",
    "status",
}


def build_insert_operations(
    *,
    base_graph: dict[str, Any],
    placement: str,
    target: dict[str, str],
    node: dict[str, Any],
) -> list[dict[str, Any]]:
    nodes = {
        str(item.get("id")): item
        for item in base_graph.get("nodes") or []
        if isinstance(item, dict) and item.get("id")
    }
    edges = {
        str(item.get("id")): item
        for item in base_graph.get("edges") or []
        if isinstance(item, dict) and item.get("id")
    }
    new_node_id = str(node.get("id") or "")
    if not new_node_id or new_node_id in nodes:
        raise GraphMutationValidationError("insert node id is invalid")

    selected_edge: dict[str, Any] | None = None
    node_id = target.get("node_id")
    edge_id = target.get("edge_id")
    if placement == "between":
        selected_edge = edges.get(str(edge_id or ""))
        if selected_edge is None:
            raise GraphMutationValidationError("selected edge is required")
    elif placement in {"before", "after"}:
        if not node_id or node_id not in nodes:
            raise GraphMutationValidationError("selected node is required")
        adjacent = [
            edge
            for edge in edges.values()
            if (
                placement == "after" and str(edge.get("source")) == node_id
            )
            or (
                placement == "before" and str(edge.get("target")) == node_id
            )
        ]
        if len(adjacent) > 1:
            raise GraphMutationValidationError(
                "multiple adjacent edges require clarification"
            )
        selected_edge = adjacent[0] if adjacent else None
    else:
        raise GraphMutationValidationError("unsupported insert placement")

    operations: list[dict[str, Any]] = [{"op": "add_node", "node": node}]
    if selected_edge is not None:
        operations.append(
            {"op": "remove_edge", "edge_id": str(selected_edge["id"])}
        )
        operations.extend(
            [
                {
                    "op": "add_edge",
                    "edge": {
                        "id": f"agent-{new_node_id}-in",
                        "source": str(selected_edge["source"]),
                        "target": new_node_id,
                        "sourceHandle": selected_edge.get("sourceHandle"),
                    },
                },
                {
                    "op": "add_edge",
                    "edge": {
                        "id": f"agent-{new_node_id}-out",
                        "source": new_node_id,
                        "target": str(selected_edge["target"]),
                        "targetHandle": selected_edge.get("targetHandle"),
                    },
                },
            ]
        )
    elif placement == "after":
        operations.append(
            {
                "op": "add_edge",
                "edge": {
                    "id": f"agent-{new_node_id}-in",
                    "source": str(node_id),
                    "target": new_node_id,
                },
            }
        )
    elif placement == "before":
        operations.append(
            {
                "op": "add_edge",
                "edge": {
                    "id": f"agent-{new_node_id}-out",
                    "source": new_node_id,
                    "target": str(node_id),
                },
            }
        )
    return operations


def canonical_graph_hash(graph: dict[str, Any] | None) -> str:
    graph = materialize_candidate_graph(graph or {})
    canonical = _normalize_canonical_json_numbers({
        "nodes": sorted(
            copy.deepcopy(graph.get("nodes") or []),
            key=lambda node: str(node.get("id") or ""),
        ),
        "edges": sorted(
            copy.deepcopy(graph.get("edges") or []),
            key=lambda edge: str(edge.get("id") or ""),
        ),
    })
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_canonical_json_numbers(value: Any) -> Any:
    """Match browser JSON number semantics before hashing a workflow graph."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {
            key: _normalize_canonical_json_numbers(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_canonical_json_numbers(item) for item in value]
    return value


def _coerce_operations(operations: list[Any]) -> list[GraphOperation]:
    try:
        parsed = [_OPERATION_ADAPTER.validate_python(operation) for operation in operations]
    except ValidationError as exc:
        raise GraphMutationValidationError("invalid graph operation") from exc
    return sorted(parsed, key=lambda operation: _OPERATION_ORDER[operation.op])


def apply_graph_operations(
    base_graph: dict[str, Any] | None,
    operations: list[GraphOperation] | list[dict[str, Any]],
) -> dict[str, Any]:
    graph = copy.deepcopy(base_graph or {})
    nodes = {
        str(node["id"]): copy.deepcopy(node)
        for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    edges = {
        str(edge["id"]): copy.deepcopy(edge)
        for edge in graph.get("edges") or []
        if isinstance(edge, dict) and edge.get("id")
    }
    for operation in _coerce_operations(list(operations)):
        if isinstance(operation, RemoveEdgeOperation):
            if operation.edge_id not in edges:
                raise GraphMutationValidationError("remove edge target is missing")
            del edges[operation.edge_id]
        elif isinstance(operation, RemoveNodeOperation):
            if operation.node_id not in nodes:
                raise GraphMutationValidationError("remove node target is missing")
            if any(
                operation.node_id in {str(edge.get("source")), str(edge.get("target"))}
                for edge in edges.values()
            ):
                raise GraphMutationValidationError("remove connected edges first")
            del nodes[operation.node_id]
        elif isinstance(operation, AddNodeOperation):
            node = operation.node.model_dump()
            if operation.node.id in nodes:
                raise GraphMutationValidationError("node id already exists")
            nodes[operation.node.id] = node
        elif isinstance(operation, ReplaceNodeDataOperation):
            if operation.node_id not in nodes:
                raise GraphMutationValidationError("replace node target is missing")
            nodes[operation.node_id]["data"] = copy.deepcopy(operation.data)
        elif isinstance(operation, ReplaceNodePositionOperation):
            if operation.node_id not in nodes:
                raise GraphMutationValidationError("replace node target is missing")
            nodes[operation.node_id]["position"] = operation.position.model_dump()
        elif isinstance(operation, AddEdgeOperation):
            edge = operation.edge.model_dump()
            if operation.edge.id in edges:
                raise GraphMutationValidationError("edge id already exists")
            edges[operation.edge.id] = edge
    graph["nodes"] = list(nodes.values())
    graph["edges"] = list(edges.values())
    return graph


def _materialize_candidate_node(
    raw_node: dict[str, Any], *, derive_configuration: bool
) -> dict[str, Any]:
    node = NodeSchema.model_validate(raw_node).model_dump(mode="python")
    for field in _EDITOR_RUNTIME_NODE_FIELDS:
        node.pop(field, None)
    data = dict(node.get("data") or {})
    for field in _EDITOR_PRESENTATION_DATA_FIELDS:
        data.pop(field, None)
    subgraph = data.get("subGraph")
    if isinstance(subgraph, dict):
        data["subGraph"] = materialize_candidate_graph(subgraph)
    if derive_configuration:
        data = normalize_deferred_parameters(
            str(node.get("type") or ""),
            data,
        )
        data["configuration_state"] = derive_node_configuration_state(
            str(node.get("type") or ""), data
        )
    node["data"] = data
    return node


def materialize_candidate_graph(graph: dict[str, Any]) -> dict[str, Any]:
    try:
        materialized = copy.deepcopy(graph)
        nodes = [
            _materialize_candidate_node(raw_node, derive_configuration=True)
            for raw_node in graph.get("nodes") or []
        ]
        materialized["nodes"] = nodes
        edges: list[dict[str, Any]] = []
        for raw_edge in graph.get("edges") or []:
            edge = EdgeSchema.model_validate(raw_edge).model_dump(mode="python")
            for field in _EDITOR_RUNTIME_EDGE_FIELDS:
                edge.pop(field, None)
            edges.append(edge)
        materialized["edges"] = edges
        return materialized
    except GraphMutationValidationError:
        raise
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise GraphMutationValidationError("workflow.graph_invalid") from exc


def deferred_parameter_projection(graph: dict[str, Any]) -> list[dict[str, Any]]:
    projection: list[dict[str, Any]] = []

    def append_nodes(nodes: Any, parent_path: list[str]) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = node.get("id")
            data = node.get("data")
            if not isinstance(node_id, str) or not isinstance(data, dict):
                continue
            node_path = [*parent_path, node_id]
            projection.append(
                {
                    "node_path": node_path,
                    "parameter_keys": [
                        key
                        for key in data.get("_deferred_parameters") or []
                        if isinstance(key, str)
                    ],
                }
            )
            subgraph = data.get("subGraph")
            if isinstance(subgraph, dict):
                append_nodes(subgraph.get("nodes"), node_path)

    append_nodes(graph.get("nodes"), [])
    return projection


def materialize_candidate_features(features: dict[str, Any] | None) -> dict[str, Any]:
    materialized = copy.deepcopy(features or {})
    note_nodes = materialized.get("noteNodes")
    if note_nodes is None:
        return materialized
    if not isinstance(note_nodes, list):
        raise GraphMutationValidationError("workflow.features_invalid")
    if any(
        not isinstance(node, dict) or node.get("type") != "note"
        for node in note_nodes
    ):
        raise GraphMutationValidationError("workflow.features_invalid")
    try:
        materialized["noteNodes"] = [
            _materialize_candidate_node(node, derive_configuration=False)
            for node in note_nodes
        ]
    except (TypeError, ValueError, ValidationError) as exc:
        raise GraphMutationValidationError("workflow.features_invalid") from exc
    return materialized


def _materialize_server_configuration(
    operations: list[GraphOperation],
    base_graph: dict[str, Any] | None,
) -> list[GraphOperation]:
    base_node_types = {
        str(node.get("id")): str(node.get("type") or "")
        for node in (base_graph or {}).get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    materialized: list[GraphOperation] = []
    for operation in operations:
        if isinstance(operation, AddNodeOperation):
            data = dict(operation.node.data)
            data["configuration_state"] = derive_node_configuration_state(
                operation.node.type, data
            )
            operation = operation.model_copy(
                update={"node": operation.node.model_copy(update={"data": data})}
            )
        elif isinstance(operation, ReplaceNodeDataOperation):
            data = dict(operation.data)
            data["configuration_state"] = derive_node_configuration_state(
                base_node_types.get(operation.node_id, ""), data
            )
            operation = operation.model_copy(update={"data": data})
        materialized.append(operation)
    return materialized


def _validate_candidate(graph: dict[str, Any]) -> None:
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    node_ids = {str(node.get("id")) for node in nodes if node.get("id")}
    if len(node_ids) != len(nodes):
        raise GraphMutationValidationError("candidate has invalid node ids")
    if any(str(node.get("type")) not in implemented_node_types() for node in nodes):
        raise GraphMutationValidationError("candidate has unsupported node type")
    if any(
        str(edge.get("source")) not in node_ids
        or str(edge.get("target")) not in node_ids
        for edge in edges
    ):
        raise GraphMutationValidationError("candidate has dangling edge")
    if len(nodes) > 1:
        connected = {
            node_id
            for edge in edges
            for node_id in (str(edge.get("source")), str(edge.get("target")))
        }
        if connected != node_ids:
            raise GraphMutationValidationError("candidate has detached node")
    if validate_workflow_graph_connections(graph):
        raise GraphMutationValidationError("candidate connection validation failed")


def validate_candidate_graph(graph: dict[str, Any]) -> None:
    _validate_candidate(graph)


class GraphMutationBuilder:
    def build(
        self,
        *,
        operation_id: UUID,
        kind: str,
        generation_mode: str,
        workflow_id: UUID,
        base_graph: dict[str, Any] | None,
        expected_workflow_updated_at: datetime,
        operations: list[dict[str, Any]] | list[GraphOperation],
        completion_context: GraphMutationCompletionContext | dict[str, Any] | None = None,
    ) -> GraphMutation:
        parsed = _materialize_server_configuration(
            _coerce_operations(list(operations)),
            base_graph,
        )
        candidate = apply_graph_operations(base_graph, parsed)
        if kind in {"initial_graph", "replace_workflow", "graph_edit"}:
            layouted = calculate_workflow_auto_layout(candidate)
            positions = {
                str(node["id"]): node.get("position", {"x": 0, "y": 0})
                for node in layouted.get("nodes") or []
            }
            parsed = [
                operation.model_copy(
                    update={
                        "node": operation.node.model_copy(
                            update={
                                "position": Position.model_validate(
                                    positions[operation.node.id]
                                )
                            }
                        )
                    }
                )
                if isinstance(operation, AddNodeOperation)
                else operation
                for operation in parsed
            ]
            positioned_node_ids = {
                operation.node.id
                for operation in parsed
                if isinstance(operation, AddNodeOperation)
            }
            candidate_after_add_positions = apply_graph_operations(base_graph, parsed)
            current_positions = {
                str(node["id"]): Position.model_validate(node.get("position", {}))
                for node in candidate_after_add_positions.get("nodes") or []
                if node.get("id")
            }
            parsed.extend(
                ReplaceNodePositionOperation(
                    op="replace_node_position",
                    node_id=node_id,
                    position=Position.model_validate(position),
                )
                for node_id, position in positions.items()
                if node_id not in positioned_node_ids
                and current_positions.get(node_id)
                != Position.model_validate(position)
            )
            candidate = apply_graph_operations(base_graph, parsed)
        candidate = materialize_candidate_graph(candidate)
        _validate_candidate(candidate)
        affected = sorted(
            {
                operation.node.id
                if isinstance(operation, AddNodeOperation)
                else operation.node_id
                for operation in parsed
                if isinstance(
                    operation,
                    (
                        AddNodeOperation,
                        RemoveNodeOperation,
                        ReplaceNodeDataOperation,
                        ReplaceNodePositionOperation,
                    ),
                )
            }
        )
        return GraphMutation(
            operation_id=operation_id,
            kind=kind,
            generation_mode=generation_mode,
            workflow_id=workflow_id,
            base_graph_hash=canonical_graph_hash(base_graph),
            expected_workflow_updated_at=expected_workflow_updated_at,
            expected_result_graph_hash=canonical_graph_hash(candidate),
            catalog_version=3,
            operations=parsed,
            affected_node_ids=affected,
            completion_context=completion_context,
        )
