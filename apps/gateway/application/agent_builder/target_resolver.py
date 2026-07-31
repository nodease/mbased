from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class TargetResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedTarget:
    node_id: str | None = None
    edge_id: str | None = None


class TargetResolver:
    def __init__(self, server_graph: dict[str, Any] | None) -> None:
        graph = server_graph or {}
        self.nodes = {
            str(node["id"]): node
            for node in graph.get("nodes") or []
            if isinstance(node, dict) and node.get("id")
        }
        self.edges = {
            str(edge["id"]): edge
            for edge in graph.get("edges") or []
            if isinstance(edge, dict) and edge.get("id")
        }

    def resolve_selected_node(self, selected_node_id: str | None) -> ResolvedTarget:
        if not selected_node_id or selected_node_id not in self.nodes:
            raise TargetResolutionError("selected node not found in server graph")
        return ResolvedTarget(node_id=selected_node_id)

    def resolve_selected_edge(self, selected_edge_id: str | None) -> ResolvedTarget:
        if not selected_edge_id or selected_edge_id not in self.edges:
            raise TargetResolutionError("selected edge not found in server graph")
        return ResolvedTarget(edge_id=selected_edge_id)

    def resolve_node_types(self, node_types: list[str]) -> ResolvedTarget:
        allowed = set(node_types)
        matches = [
            node_id
            for node_id, node in self.nodes.items()
            if str(node.get("type")) in allowed
        ]
        if not matches:
            raise TargetResolutionError("target node not found")
        if len(matches) > 1:
            raise TargetResolutionError("multiple target nodes require clarification")
        return ResolvedTarget(node_id=matches[0])
