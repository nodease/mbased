from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from apps.shared.domain.workflow_graph import (
    MAX_WORKFLOW_GRAPH_NESTING_DEPTH,
    MAX_WORKFLOW_NODE_ID_LENGTH,
    is_valid_workflow_node_id,
)

CONTAINER_KIND_LOOP = "loop"
MAX_CANONICAL_NODE_ID_LENGTH = MAX_WORKFLOW_NODE_ID_LENGTH
_DIGEST_DOMAIN = "nodease:canonical-node-location:v1"

ContainerPath = tuple[tuple[str, str], ...]


class WorkflowNodeLocationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _frame(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded


@dataclass(frozen=True, slots=True)
class CanonicalWorkflowNodeLocation:
    container_path: ContainerPath
    node_id: str

    def __post_init__(self) -> None:
        if not (
            isinstance(self.container_path, tuple)
            and is_valid_workflow_node_id(self.node_id)
        ):
            raise WorkflowNodeLocationError("workflow_node_location.invalid")
        if len(self.container_path) > MAX_WORKFLOW_GRAPH_NESTING_DEPTH:
            raise WorkflowNodeLocationError("workflow_node_location.too_deep")
        for segment in self.container_path:
            if (
                not isinstance(segment, tuple)
                or len(segment) != 2
                or segment[0] != CONTAINER_KIND_LOOP
                or not is_valid_workflow_node_id(segment[1])
            ):
                raise WorkflowNodeLocationError("workflow_node_location.invalid")

    @property
    def digest(self) -> str:
        encoded = bytearray(_frame(_DIGEST_DOMAIN))
        encoded.extend(len(self.container_path).to_bytes(4, "big"))
        for kind, node_id in self.container_path:
            encoded.extend(_frame(kind))
            encoded.extend(_frame(node_id))
        encoded.extend(_frame(self.node_id))
        return hashlib.sha256(encoded).hexdigest()

    @property
    def safe_reference(self) -> str:
        return f"workflow-node-location:v1:{self.digest}"

    def to_container_path_payload(self) -> list[dict[str, str]]:
        return [
            {"kind": kind, "node_id": node_id}
            for kind, node_id in self.container_path
        ]

    @classmethod
    def from_container_path_payload(
        cls,
        *,
        container_path: object,
        node_id: str,
    ) -> CanonicalWorkflowNodeLocation:
        if not isinstance(container_path, list):
            raise WorkflowNodeLocationError("workflow_node_location.invalid")
        path: list[tuple[str, str]] = []
        for segment in container_path:
            if (
                not isinstance(segment, Mapping)
                or set(segment) != {"kind", "node_id"}
            ):
                raise WorkflowNodeLocationError("workflow_node_location.invalid")
            path.append((segment["kind"], segment["node_id"]))
        return cls(tuple(path), node_id)


@dataclass(frozen=True, slots=True)
class LocatedWorkflowNode:
    location: CanonicalWorkflowNodeLocation
    node: Mapping[str, Any]


def iter_workflow_node_locations(
    graph: Mapping[str, Any] | None,
) -> tuple[LocatedWorkflowNode, ...]:
    located: list[LocatedWorkflowNode] = []

    def walk(current: Mapping[str, Any], path: ContainerPath) -> None:
        nodes = current.get("nodes")
        if not isinstance(nodes, list):
            raise WorkflowNodeLocationError("workflow_node_location.invalid_graph")
        local_ids: set[str] = set()
        for node in nodes:
            if not isinstance(node, Mapping):
                raise WorkflowNodeLocationError(
                    "workflow_node_location.invalid_graph"
                )
            node_id = node.get("id")
            if not is_valid_workflow_node_id(node_id):
                raise WorkflowNodeLocationError(
                    "workflow_node_location.invalid_graph"
                )
            if node_id in local_ids:
                raise WorkflowNodeLocationError("workflow_node_location.ambiguous")
            local_ids.add(node_id)
            location = CanonicalWorkflowNodeLocation(path, node_id)
            located.append(LocatedWorkflowNode(location=location, node=node))

            if node.get("type") != "loopNode":
                continue
            data = node.get("data")
            subgraph = data.get("subGraph") if isinstance(data, Mapping) else None
            if subgraph is None:
                continue
            if not isinstance(subgraph, Mapping):
                raise WorkflowNodeLocationError(
                    "workflow_node_location.invalid_graph"
                )
            child_path = path + ((CONTAINER_KIND_LOOP, node_id),)
            if len(child_path) > MAX_WORKFLOW_GRAPH_NESTING_DEPTH:
                raise WorkflowNodeLocationError("workflow_node_location.too_deep")
            walk(subgraph, child_path)

    if not isinstance(graph, Mapping):
        raise WorkflowNodeLocationError("workflow_node_location.invalid_graph")
    walk(graph, ())
    return tuple(located)


def find_workflow_node_at_location(
    graph: Mapping[str, Any] | None,
    location: CanonicalWorkflowNodeLocation,
) -> Mapping[str, Any]:
    matches = [
        located.node
        for located in iter_workflow_node_locations(graph)
        if located.location == location
    ]
    if not matches:
        raise WorkflowNodeLocationError("workflow_node_location.not_found")
    if len(matches) != 1:
        raise WorkflowNodeLocationError("workflow_node_location.ambiguous")
    return matches[0]


__all__ = [
    "CONTAINER_KIND_LOOP",
    "MAX_CANONICAL_NODE_ID_LENGTH",
    "CanonicalWorkflowNodeLocation",
    "ContainerPath",
    "LocatedWorkflowNode",
    "WorkflowNodeLocationError",
    "find_workflow_node_at_location",
    "iter_workflow_node_locations",
]
