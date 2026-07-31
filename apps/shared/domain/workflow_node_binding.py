from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from apps.shared.domain.workflow_node_location import (
    CanonicalWorkflowNodeLocation,
    WorkflowNodeLocationError,
    iter_workflow_node_locations,
)

RUNTIME_METADATA_KEY = "_nodease_runtime"
BINDINGS_KEY = "workflow_node_bindings"
BINDING_VERSION = "workflow-node-bindings.v1"
MAX_WORKFLOW_NODE_DEPTH = 3
_SIDE_EFFECT_VALUES = frozenset(
    {"none", "external_read", "local_execution", "external_write"}
)


class WorkflowNodeBindingError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WorkflowNodeReference:
    container_path: tuple[tuple[str, str], ...]
    workflow_node_id: str
    target_app_id: uuid.UUID


@dataclass(frozen=True)
class WorkflowNodeBinding:
    container_path: tuple[tuple[str, str], ...]
    workflow_node_id: str
    target_app_id: uuid.UUID
    deployment_id: uuid.UUID
    deployment_version: int
    snapshot_sha256: str

    def __post_init__(self) -> None:
        try:
            CanonicalWorkflowNodeLocation(
                self.container_path,
                self.workflow_node_id,
            )
        except WorkflowNodeLocationError as exc:
            raise WorkflowNodeBindingError("workflow_node.binding_invalid") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "container_path": [
                {"kind": kind, "node_id": node_id}
                for kind, node_id in self.container_path
            ],
            "workflow_node_id": self.workflow_node_id,
            "target_app_id": str(self.target_app_id),
            "deployment_id": str(self.deployment_id),
            "deployment_version": self.deployment_version,
            "snapshot_sha256": self.snapshot_sha256,
        }


def canonical_snapshot_sha256(graph: dict[str, Any]) -> str:
    try:
        canonical = json.dumps(
            graph,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WorkflowNodeBindingError("workflow_node.snapshot_not_canonical") from exc
    return hashlib.sha256(canonical).hexdigest()


def strip_workflow_node_bindings(graph: dict[str, Any] | None) -> dict[str, Any]:
    cleaned = copy.deepcopy(graph or {})

    def clean(current: dict[str, Any]) -> None:
        runtime = current.get(RUNTIME_METADATA_KEY)
        if isinstance(runtime, dict):
            runtime.pop(BINDINGS_KEY, None)
            if runtime:
                current[RUNTIME_METADATA_KEY] = runtime
            else:
                current.pop(RUNTIME_METADATA_KEY, None)
        else:
            current.pop(RUNTIME_METADATA_KEY, None)
        nodes = current.get("nodes")
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict) or node.get("type") != "loopNode":
                continue
            data = node.get("data")
            subgraph = data.get("subGraph") if isinstance(data, dict) else None
            if isinstance(subgraph, dict):
                clean(subgraph)

    clean(cleaned)
    return cleaned


def workflow_node_references(
    graph: dict[str, Any] | None,
) -> tuple[WorkflowNodeReference, ...]:
    references: list[WorkflowNodeReference] = []
    if graph is None:
        return ()
    try:
        located_nodes = iter_workflow_node_locations(graph)
    except WorkflowNodeLocationError as exc:
        raise WorkflowNodeBindingError("workflow_node.invalid_graph") from exc
    for located in located_nodes:
        node = located.node
        if node.get("type") != "workflowNode":
            continue
        data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
        try:
            target_app_id = uuid.UUID(str(data.get("appId")))
        except (TypeError, ValueError):
            raise WorkflowNodeBindingError("workflow_node.target_unavailable") from None
        references.append(
            WorkflowNodeReference(
                located.location.container_path,
                located.location.node_id,
                target_app_id,
            )
        )
    return tuple(
        sorted(
            references,
            key=lambda item: (item.container_path, item.workflow_node_id),
        )
    )


def apply_workflow_node_bindings(
    graph: dict[str, Any],
    bindings: tuple[WorkflowNodeBinding, ...],
) -> dict[str, Any]:
    cleaned = strip_workflow_node_bindings(graph)
    keys = [(binding.container_path, binding.workflow_node_id) for binding in bindings]
    if len(keys) != len(set(keys)):
        raise WorkflowNodeBindingError("workflow_node.binding_duplicate")
    if bindings:
        runtime = dict(cleaned.get(RUNTIME_METADATA_KEY) or {})
        runtime[BINDINGS_KEY] = {
            "version": BINDING_VERSION,
            "entries": [binding.to_dict() for binding in bindings],
        }
        cleaned[RUNTIME_METADATA_KEY] = runtime
    return cleaned


def parse_workflow_node_bindings(
    graph: dict[str, Any] | None,
) -> tuple[WorkflowNodeBinding, ...] | None:
    graph = graph or {}
    runtime = graph.get(RUNTIME_METADATA_KEY)
    if runtime is None:
        return None
    if not isinstance(runtime, dict) or set(runtime) != {BINDINGS_KEY}:
        raise WorkflowNodeBindingError("workflow_node.binding_invalid")
    payload = runtime.get(BINDINGS_KEY)
    if not isinstance(payload, dict) or set(payload) != {"version", "entries"}:
        raise WorkflowNodeBindingError("workflow_node.binding_invalid")
    if payload.get("version") != BINDING_VERSION or not isinstance(
        payload.get("entries"), list
    ):
        raise WorkflowNodeBindingError("workflow_node.binding_version_unknown")
    bindings: list[WorkflowNodeBinding] = []
    for entry in payload["entries"]:
        if not isinstance(entry, dict) or set(entry) != {
            "container_path",
            "workflow_node_id",
            "target_app_id",
            "deployment_id",
            "deployment_version",
            "snapshot_sha256",
        }:
            raise WorkflowNodeBindingError("workflow_node.binding_invalid")
        path_raw = entry["container_path"]
        if not isinstance(path_raw, list):
            raise WorkflowNodeBindingError("workflow_node.binding_invalid")
        path: list[tuple[str, str]] = []
        for segment in path_raw:
            if (
                not isinstance(segment, dict)
                or set(segment) != {"kind", "node_id"}
                or segment.get("kind") != "loop"
                or not isinstance(segment.get("node_id"), str)
            ):
                raise WorkflowNodeBindingError("workflow_node.binding_invalid")
            path.append(("loop", segment["node_id"]))
        try:
            target_app_id = uuid.UUID(str(entry["target_app_id"]))
            deployment_id = uuid.UUID(str(entry["deployment_id"]))
            deployment_version = int(entry["deployment_version"])
        except (TypeError, ValueError):
            raise WorkflowNodeBindingError("workflow_node.binding_invalid") from None
        snapshot_hash = entry["snapshot_sha256"]
        if (
            not isinstance(entry["workflow_node_id"], str)
            or not isinstance(snapshot_hash, str)
            or len(snapshot_hash) != 64
            or any(char not in "0123456789abcdef" for char in snapshot_hash)
            or deployment_version < 1
        ):
            raise WorkflowNodeBindingError("workflow_node.binding_invalid")
        bindings.append(
            WorkflowNodeBinding(
                container_path=tuple(path),
                workflow_node_id=entry["workflow_node_id"],
                target_app_id=target_app_id,
                deployment_id=deployment_id,
                deployment_version=deployment_version,
                snapshot_sha256=snapshot_hash,
            )
        )
    ordered = tuple(
        sorted(bindings, key=lambda item: (item.container_path, item.workflow_node_id))
    )
    if tuple(bindings) != ordered:
        raise WorkflowNodeBindingError("workflow_node.binding_not_canonical")
    if len({(item.container_path, item.workflow_node_id) for item in ordered}) != len(
        ordered
    ):
        raise WorkflowNodeBindingError("workflow_node.binding_duplicate")
    return ordered


def graph_has_external_effect(
    graph: dict[str, Any] | None,
    side_effect_by_node_type: Mapping[str, str],
) -> bool:
    nodes = (graph or {}).get("nodes")
    if not isinstance(nodes, list):
        return True
    for node in nodes:
        if not isinstance(node, dict):
            return True
        node_type = node.get("type")
        if node_type == "note":
            continue
        if not isinstance(node_type, str) or node_type not in side_effect_by_node_type:
            return True
        if not isinstance(node.get("id"), str) or not node["id"]:
            return True
        if not isinstance(node.get("data"), dict):
            return True
        data = node["data"]
        side_effect = side_effect_by_node_type[node_type]
        if side_effect not in _SIDE_EFFECT_VALUES:
            return True
        if node_type == "httpRequestNode" and str(data.get("method", "GET")).upper() == "GET":
            continue
        if node_type == "githubNode" and data.get("action", "get_pr") == "get_pr":
            continue
        if side_effect == "external_write":
            return True
        if node_type == "loopNode":
            subgraph = data.get("subGraph")
            if not isinstance(subgraph, dict) or graph_has_external_effect(
                subgraph, side_effect_by_node_type
            ):
                return True
    return False
