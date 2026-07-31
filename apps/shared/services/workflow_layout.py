import copy
import math
from collections import defaultdict
from typing import Any

DEFAULT_NODE_WIDTH = 420.0
DEFAULT_NODE_HEIGHT = 200.0
RANK_GAP = 160.0
SIBLING_GAP = 100.0
ORPHAN_GAP_X = 50.0
ORPHAN_GAP_Y = 50.0
MIN_ORPHAN_ROW_WIDTH = 1000.0
SNAP_GRID_SIZE = 10.0


def calculate_workflow_auto_layout(graph: dict[str, Any]) -> dict[str, Any]:
    """Apply the canonical left-to-right workflow layout to a graph copy."""
    layouted = copy.deepcopy(graph or {})
    nodes = list(layouted.get("nodes") or [])
    edges = list(layouted.get("edges") or [])
    node_by_id = {
        str(node.get("id")): node
        for node in nodes
        if node.get("id") and node.get("type") != "note"
    }
    node_order = {node_id: index for index, node_id in enumerate(node_by_id)}

    valid_edges = [
        edge
        for edge in edges
        if str(edge.get("source")) in node_by_id
        and str(edge.get("target")) in node_by_id
    ]
    connected_ids = {
        node_id
        for edge in valid_edges
        for node_id in (str(edge.get("source")), str(edge.get("target")))
    }
    connected_order = [node_id for node_id in node_by_id if node_id in connected_ids]
    orphan_order = [node_id for node_id in node_by_id if node_id not in connected_ids]

    layers = _assign_layers(connected_order, valid_edges, node_order)
    layer_nodes: dict[int, list[str]] = defaultdict(list)
    for node_id in connected_order:
        layer_nodes[layers[node_id]].append(node_id)

    condition_branch_order = _condition_branch_order(
        valid_edges,
        node_by_id,
        node_order,
    )
    for node_ids in layer_nodes.values():
        node_ids.sort(
            key=lambda node_id: condition_branch_order.get(
                node_id,
                (node_order[node_id], math.inf, node_order[node_id]),
            )
        )

    layer_widths = {
        layer: max(_node_size(node_by_id[node_id])[0] for node_id in node_ids)
        for layer, node_ids in layer_nodes.items()
    }
    layer_heights = {
        layer: sum(_node_size(node_by_id[node_id])[1] for node_id in node_ids)
        + SIBLING_GAP * max(0, len(node_ids) - 1)
        for layer, node_ids in layer_nodes.items()
    }
    max_connected_height = max(layer_heights.values(), default=0.0)

    layer_x: dict[int, float] = {}
    current_x = 0.0
    for layer in sorted(layer_nodes):
        layer_x[layer] = current_x
        current_x += layer_widths[layer] + RANK_GAP

    for layer in sorted(layer_nodes):
        current_y = 0.0
        for node_id in layer_nodes[layer]:
            node = node_by_id[node_id]
            width, height = _node_size(node)
            node["position"] = {
                "x": _snap(layer_x[layer]),
                "y": _snap(current_y),
            }
            current_y += height + SIBLING_GAP

    connected_width = max(
        (
            layer_x[layer] + layer_widths[layer]
            for layer in layer_nodes
        ),
        default=1000.0,
    )
    orphan_row_width = max(connected_width, MIN_ORPHAN_ROW_WIDTH)
    orphan_x = 0.0
    orphan_y = max_connected_height + RANK_GAP
    current_row_height = 0.0
    for node_id in orphan_order:
        node = node_by_id[node_id]
        width, height = _node_size(node)
        node["position"] = {"x": _snap(orphan_x), "y": _snap(orphan_y)}
        orphan_x += width + ORPHAN_GAP_X
        current_row_height = max(current_row_height, height)
        if orphan_x > orphan_row_width:
            orphan_x = 0.0
            orphan_y += current_row_height + ORPHAN_GAP_Y
            current_row_height = 0.0

    layouted["nodes"] = nodes
    layouted["edges"] = edges
    return layouted


def _assign_layers(
    node_ids: list[str],
    edges: list[dict[str, Any]],
    node_order: dict[str, int],
) -> dict[str, int]:
    incoming_count = {node_id: 0 for node_id in node_ids}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if source not in incoming_count or target not in incoming_count:
            continue
        incoming_count[target] += 1
        outgoing[source].append(target)

    for targets in outgoing.values():
        targets.sort(key=lambda node_id: node_order[node_id])

    queue = sorted(
        (node_id for node_id, count in incoming_count.items() if count == 0),
        key=lambda node_id: node_order[node_id],
    )
    layers = {node_id: 0 for node_id in node_ids}
    processed: set[str] = set()

    while queue:
        node_id = queue.pop(0)
        processed.add(node_id)
        for target in outgoing.get(node_id, []):
            layers[target] = max(layers[target], layers[node_id] + 1)
            incoming_count[target] -= 1
            if incoming_count[target] == 0:
                queue.append(target)
                queue.sort(key=lambda item: node_order[item])

    next_cycle_layer = max((layers[node_id] for node_id in processed), default=-1) + 1
    for node_id in node_ids:
        if node_id in processed:
            continue
        layers[node_id] = next_cycle_layer
        next_cycle_layer += 1

    return layers


def _condition_branch_order(
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    node_order: dict[str, int],
) -> dict[str, tuple[int, int, int]]:
    """Keep each condition's Default target above its configured branches."""
    ordered_targets: dict[str, tuple[int, int, int]] = {}

    for edge in edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        source_node = node_by_id.get(source)
        if not source_node or source_node.get("type") != "conditionNode":
            continue

        cases = (source_node.get("data") or {}).get("cases") or []
        case_order = {
            str(case.get("id")): index + 1
            for index, case in enumerate(cases)
            if isinstance(case, dict) and case.get("id")
        }
        source_handle = str(edge.get("sourceHandle") or "")
        branch_order = 0 if source_handle == "default" else case_order.get(
            source_handle,
            len(case_order) + 1,
        )
        candidate = (node_order[source], branch_order, node_order[target])
        current = ordered_targets.get(target)
        if current is None or candidate < current:
            ordered_targets[target] = candidate

    return ordered_targets


def _node_size(node: dict[str, Any]) -> tuple[float, float]:
    measured = node.get("measured") if isinstance(node.get("measured"), dict) else {}
    return (
        _positive_number(
            measured.get("width"),
            _positive_number(node.get("width"), DEFAULT_NODE_WIDTH),
        ),
        _positive_number(
            measured.get("height"),
            _positive_number(node.get("height"), DEFAULT_NODE_HEIGHT),
        ),
    )


def _positive_number(value: Any, default: float) -> float:
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        return float(value)
    return default


def _snap(value: float) -> int:
    return int(math.floor(value / SNAP_GRID_SIZE + 0.5) * SNAP_GRID_SIZE)
