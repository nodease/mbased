"""Pure validation for Workflow LLM Knowledge graph references.

The stored graph carries user configuration intent, not an authorization
capability.  This module therefore validates and projects identifiers only; it
does not import an ORM, framework, queue, or concrete runtime implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from apps.shared.domain.knowledge_runtime_candidates import (
    MAX_RUNTIME_COLLECTION_REFERENCES,
    MAX_RUNTIME_DIRECT_KB_REFERENCES,
)

MAX_KNOWLEDGE_REFERENCE_DISPLAY_LENGTH = 255


class WorkflowKnowledgeReferenceError(ValueError):
    """Fixed-code graph validation error that never echoes graph values."""

    def __init__(self, reason_code: str, field_path: str) -> None:
        self.reason_code = reason_code
        self.field_path = field_path
        super().__init__(reason_code)


def _raise(reason_code: str, field_path: str) -> None:
    raise WorkflowKnowledgeReferenceError(reason_code, field_path)


def _canonical_uuid(value: object, *, field_path: str) -> UUID:
    if not isinstance(value, str):
        _raise("knowledge_reference_id_invalid", field_path)
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError):
        _raise("knowledge_reference_id_invalid", field_path)
    if str(parsed) != value:
        _raise("knowledge_reference_id_invalid", field_path)
    return parsed


def _display_value(
    value: object,
    *,
    field_path: str,
    required: bool,
) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        _raise("knowledge_reference_display_invalid", field_path)
    if len(value) > MAX_KNOWLEDGE_REFERENCE_DISPLAY_LENGTH or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        _raise("knowledge_reference_display_invalid", field_path)
    return value


@dataclass(frozen=True, slots=True)
class KnowledgeBaseGraphReference:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class KnowledgeCollectionGraphReference:
    id: UUID
    safe_label: str | None = None


def _unique_ids(values: tuple[UUID, ...]) -> tuple[UUID, ...]:
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True, slots=True)
class WorkflowNodeKnowledgeReferences:
    """Validated references for one LLM node without mutating its graph data."""

    field_path: str
    direct_references: tuple[KnowledgeBaseGraphReference, ...] = ()
    collection_references: tuple[KnowledgeCollectionGraphReference, ...] = ()

    @property
    def direct_kb_ids(self) -> tuple[UUID, ...]:
        return _unique_ids(tuple(item.id for item in self.direct_references))

    @property
    def collection_ids(self) -> tuple[UUID, ...]:
        return _unique_ids(tuple(item.id for item in self.collection_references))

    @property
    def has_references(self) -> bool:
        return bool(self.direct_references or self.collection_references)


def _reference_list(
    data: Mapping[str, Any],
    *,
    key: str,
    field_path: str,
    limit: int,
) -> list[object]:
    if key not in data:
        return []
    raw_items = data[key]
    if not isinstance(raw_items, list):
        _raise("knowledge_reference_list_invalid", f"{field_path}.{key}")
    if len(raw_items) > limit:
        _raise("knowledge_reference_limit_exceeded", f"{field_path}.{key}")
    return raw_items


def parse_llm_knowledge_references(
    data: Mapping[str, Any],
    *,
    field_path: str = "data",
) -> WorkflowNodeKnowledgeReferences:
    """Validate one LLM node's additive direct-KB and Collection references."""

    if not isinstance(data, Mapping):
        _raise("knowledge_reference_data_invalid", field_path)

    raw_direct = _reference_list(
        data,
        key="knowledgeBases",
        field_path=field_path,
        limit=MAX_RUNTIME_DIRECT_KB_REFERENCES,
    )
    direct: list[KnowledgeBaseGraphReference] = []
    for index, raw_item in enumerate(raw_direct):
        item_path = f"{field_path}.knowledgeBases[{index}]"
        if not isinstance(raw_item, Mapping):
            _raise("knowledge_reference_item_invalid", item_path)
        if set(raw_item) != {"id", "name"}:
            _raise("knowledge_reference_item_invalid", item_path)
        direct.append(
            KnowledgeBaseGraphReference(
                id=_canonical_uuid(raw_item["id"], field_path=f"{item_path}.id"),
                name=_display_value(
                    raw_item["name"],
                    field_path=f"{item_path}.name",
                    required=True,
                )
                or "",
            )
        )

    raw_collections = _reference_list(
        data,
        key="knowledgeCollections",
        field_path=field_path,
        limit=MAX_RUNTIME_COLLECTION_REFERENCES,
    )
    collections: list[KnowledgeCollectionGraphReference] = []
    for index, raw_item in enumerate(raw_collections):
        item_path = f"{field_path}.knowledgeCollections[{index}]"
        if not isinstance(raw_item, Mapping):
            _raise("knowledge_reference_item_invalid", item_path)
        allowed_fields = {"id", "safeLabel"}
        if "id" not in raw_item or not set(raw_item).issubset(allowed_fields):
            _raise("knowledge_reference_item_invalid", item_path)
        collections.append(
            KnowledgeCollectionGraphReference(
                id=_canonical_uuid(raw_item["id"], field_path=f"{item_path}.id"),
                safe_label=_display_value(
                    raw_item.get("safeLabel"),
                    field_path=f"{item_path}.safeLabel",
                    required=False,
                ),
            )
        )

    return WorkflowNodeKnowledgeReferences(
        field_path=field_path,
        direct_references=tuple(direct),
        collection_references=tuple(collections),
    )


def parse_workflow_knowledge_references(
    graph: Mapping[str, Any],
) -> tuple[WorkflowNodeKnowledgeReferences, ...]:
    """Validate Knowledge references in root and nested Loop subgraphs."""

    if not isinstance(graph, Mapping):
        _raise("workflow_graph_invalid", "graph")

    results: list[WorkflowNodeKnowledgeReferences] = []
    pending: list[tuple[Mapping[str, Any], str]] = [(graph, "graph")]
    while pending:
        current_graph, graph_path = pending.pop()
        nodes = current_graph.get("nodes", [])
        if not isinstance(nodes, list):
            _raise("workflow_graph_invalid", f"{graph_path}.nodes")
        for index, node in enumerate(nodes):
            node_path = f"{graph_path}.nodes[{index}]"
            if not isinstance(node, Mapping):
                _raise("workflow_graph_invalid", node_path)
            data = node.get("data")
            if node.get("type") == "llmNode":
                if not isinstance(data, Mapping):
                    _raise("knowledge_reference_data_invalid", f"{node_path}.data")
                results.append(
                    parse_llm_knowledge_references(
                        data,
                        field_path=f"{node_path}.data",
                    )
                )
            if not isinstance(data, Mapping):
                continue
            subgraph = data.get("subGraph")
            if subgraph is None:
                continue
            if not isinstance(subgraph, Mapping):
                _raise("workflow_graph_invalid", f"{node_path}.data.subGraph")
            pending.append((subgraph, f"{node_path}.data.subGraph"))
    return tuple(results)


def aggregate_workflow_knowledge_reference_ids(
    parsed_nodes: tuple[WorkflowNodeKnowledgeReferences, ...],
) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
    """Return first-occurrence IDs across a graph for bulk authorization."""

    direct: list[UUID] = []
    collections: list[UUID] = []
    for node in parsed_nodes:
        direct.extend(node.direct_kb_ids)
        collections.extend(node.collection_ids)
    return _unique_ids(tuple(direct)), _unique_ids(tuple(collections))
