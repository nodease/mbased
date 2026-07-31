from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.gateway.application.agent_builder.graph_mutation_builder import (
    GraphMutationBuilder,
    GraphMutationValidationError,
    apply_graph_operations,
    canonical_graph_hash,
    build_insert_operations,
)
from apps.gateway.application.agent_builder.target_resolver import (
    TargetResolutionError,
    TargetResolver,
)
from apps.shared.schemas.agent_builder import GraphMutation, GraphMutationSafeEnvelope


def _node(node_id: str, node_type: str) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"title": node_id},
    }


def _initial_operations() -> list[dict]:
    return [
        {"op": "add_node", "node": _node("start", "startNode")},
        {"op": "add_node", "node": _node("llm", "llmNode")},
        {"op": "add_node", "node": _node("answer", "answerNode")},
        {
            "op": "add_edge",
            "edge": {"id": "e1", "source": "start", "target": "llm"},
        },
        {
            "op": "add_edge",
            "edge": {"id": "e2", "source": "llm", "target": "answer"},
        },
    ]


def test_canonical_graph_hash_normalizes_integral_floats_across_browser_json():
    server_graph = {
        "nodes": [
            {
                **_node("llm", "llmNode"),
                "data": {
                    "title": "LLM",
                    "parameters": {
                        "model_routing_validation_budget_usd": 3.0,
                        "presence_penalty": -0.0,
                    },
                },
            }
        ],
        "edges": [],
    }
    browser_graph = {
        "nodes": [
            {
                **_node("llm", "llmNode"),
                "data": {
                    "title": "LLM",
                    "parameters": {
                        "model_routing_validation_budget_usd": 3,
                        "presence_penalty": 0,
                    },
                },
            }
        ],
        "edges": [],
    }

    assert canonical_graph_hash(server_graph) == canonical_graph_hash(browser_graph)


@pytest.mark.parametrize("generation_mode", ["configure_and_generate", "structure_only"])
def test_initial_graph_kind_is_independent_from_generation_mode(generation_mode):
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="initial_graph",
        generation_mode=generation_mode,
        workflow_id=uuid4(),
        base_graph={"nodes": [], "edges": []},
        expected_workflow_updated_at=datetime.now(timezone.utc),
        operations=_initial_operations(),
    )

    assert mutation.kind == "initial_graph"
    assert mutation.generation_mode == generation_mode
    assert mutation.expected_result_graph_hash == canonical_graph_hash(
        apply_graph_operations({"nodes": [], "edges": []}, mutation.operations)
    )
    assert {operation.op for operation in mutation.operations} == {
        "add_node",
        "add_edge",
    }


def test_safe_envelope_never_contains_typed_operations():
    now = datetime.now(timezone.utc)
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="initial_graph",
        generation_mode="configure_and_generate",
        workflow_id=uuid4(),
        base_graph={"nodes": [], "edges": []},
        expected_workflow_updated_at=now,
        operations=_initial_operations(),
    )

    envelope = GraphMutationSafeEnvelope.from_mutation(mutation)

    assert "operations" not in envelope.model_dump()
    assert envelope.catalog_version == 3
    assert envelope.expected_result_graph_hash == mutation.expected_result_graph_hash


@pytest.mark.parametrize("schema", [GraphMutation, GraphMutationSafeEnvelope])
@pytest.mark.parametrize("timestamp", [None, pytest.param("missing", id="missing")])
def test_graph_mutation_contract_requires_expected_workflow_timestamp(
    schema, timestamp
):
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="initial_graph",
        generation_mode="structure_only",
        workflow_id=uuid4(),
        base_graph={"nodes": [], "edges": []},
        expected_workflow_updated_at=datetime.now(timezone.utc),
        operations=_initial_operations(),
    )
    payload = mutation.model_dump()
    if schema is GraphMutationSafeEnvelope:
        payload.pop("operations")
    if timestamp == "missing":
        payload.pop("expected_workflow_updated_at")
    else:
        payload["expected_workflow_updated_at"] = timestamp

    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_graph_mutation_schema_accepts_typed_workflow_replacement():
    base = {
        "nodes": [_node("old-start", "startNode")],
        "edges": [],
    }
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="replace_workflow",
        generation_mode="structure_only",
        workflow_id=uuid4(),
        base_graph=base,
        expected_workflow_updated_at=datetime.now(timezone.utc),
        operations=[
            {"op": "remove_node", "node_id": "old-start"},
            {"op": "add_node", "node": _node("new-start", "startNode")},
        ],
    )

    assert mutation.kind == "replace_workflow"
    assert [operation.op for operation in mutation.operations] == [
        "remove_node",
        "add_node",
    ]


def test_workflow_replacement_applies_auto_layout_to_the_new_graph():
    base = {
        "nodes": [_node("old-start", "startNode")],
        "edges": [],
    }
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="replace_workflow",
        generation_mode="structure_only",
        workflow_id=uuid4(),
        base_graph=base,
        expected_workflow_updated_at=datetime.now(timezone.utc),
        operations=[
            {"op": "remove_node", "node_id": "old-start"},
            {"op": "add_node", "node": _node("new-start", "startNode")},
            {"op": "add_node", "node": _node("new-answer", "answerNode")},
            {
                "op": "add_edge",
                "edge": {
                    "id": "new-edge",
                    "source": "new-start",
                    "target": "new-answer",
                },
            },
        ],
    )
    graph = apply_graph_operations(base, mutation.operations)
    positions = {node["id"]: node["position"] for node in graph["nodes"]}

    assert positions["new-start"] != positions["new-answer"]


def test_graph_edit_persists_server_calculated_positions_for_existing_nodes():
    base = {
        "nodes": [
            _node("start", "startNode"),
            _node("answer", "answerNode"),
        ],
        "edges": [{"id": "old-edge", "source": "start", "target": "answer"}],
    }
    mutation = GraphMutationBuilder().build(
        operation_id=uuid4(),
        kind="graph_edit",
        generation_mode="structure_only",
        workflow_id=uuid4(),
        base_graph=base,
        expected_workflow_updated_at=datetime.now(timezone.utc),
        operations=[
            {"op": "remove_edge", "edge_id": "old-edge"},
            {"op": "add_node", "node": _node("llm", "llmNode")},
            {"op": "add_edge", "edge": {"id": "e1", "source": "start", "target": "llm"}},
            {"op": "add_edge", "edge": {"id": "e2", "source": "llm", "target": "answer"}},
        ],
    )

    graph = apply_graph_operations(base, mutation.operations)

    assert any(
        operation.op == "replace_node_position" and operation.node_id == "answer"
        for operation in mutation.operations
    )
    assert {node["id"] for node in graph["nodes"]} == {"start", "llm", "answer"}
    assert len({tuple(node["position"].values()) for node in graph["nodes"]}) == 3


def test_graph_mutation_schema_rejects_structure_only_as_kind_and_json_patch():
    common = {
        "operation_id": str(uuid4()),
        "status": "pending_apply",
        "workflow_id": str(uuid4()),
        "base_graph_hash": canonical_graph_hash({"nodes": [], "edges": []}),
        "expected_result_graph_hash": "a" * 64,
        "catalog_version": 3,
        "affected_node_ids": [],
        "generation_mode": "structure_only",
    }
    with pytest.raises(ValidationError):
        GraphMutation.model_validate({**common, "kind": "structure_only", "operations": []})
    with pytest.raises(ValidationError):
        GraphMutation.model_validate(
            {
                **common,
                "kind": "graph_edit",
                "operations": [{"op": "replace", "path": "/nodes"}],
            }
        )


def test_builder_rejects_detached_nodes_and_invalid_condition_handle():
    now = datetime.now(timezone.utc)
    detached = [
        {"op": "add_node", "node": _node("start", "startNode")},
        {"op": "add_node", "node": _node("answer", "answerNode")},
    ]
    with pytest.raises(GraphMutationValidationError, match="detached"):
        GraphMutationBuilder().build(
            operation_id=uuid4(),
            kind="initial_graph",
            generation_mode="structure_only",
            workflow_id=uuid4(),
            base_graph={"nodes": [], "edges": []},
            expected_workflow_updated_at=now,
            operations=detached,
        )

    invalid_handle = [
        {"op": "add_node", "node": _node("condition", "conditionNode")},
        {"op": "add_node", "node": _node("answer", "answerNode")},
        {
            "op": "add_edge",
            "edge": {
                "id": "e1",
                "source": "condition",
                "sourceHandle": "missing",
                "target": "answer",
            },
        },
    ]
    with pytest.raises(GraphMutationValidationError, match="connection"):
        GraphMutationBuilder().build(
            operation_id=uuid4(),
            kind="initial_graph",
            generation_mode="structure_only",
            workflow_id=uuid4(),
            base_graph={"nodes": [], "edges": []},
            expected_workflow_updated_at=now,
            operations=invalid_handle,
        )


def test_target_resolver_uses_server_graph_and_requires_one_match():
    graph = {
        "nodes": [
            _node("github-read", "githubNode"),
            _node("github-comment", "githubNode"),
        ],
        "edges": [],
    }
    resolver = TargetResolver(graph)

    assert resolver.resolve_selected_node("github-read").node_id == "github-read"
    with pytest.raises(TargetResolutionError, match="multiple"):
        resolver.resolve_node_types(["githubNode"])
    with pytest.raises(TargetResolutionError, match="not found"):
        resolver.resolve_selected_node("client-only-node")


@pytest.mark.parametrize(
    ("placement", "target", "expected_edges"),
    [
        ("after", {"node_id": "start"}, {("start", "new"), ("new", "answer")}),
        ("before", {"node_id": "answer"}, {("start", "new"), ("new", "answer")}),
        ("between", {"edge_id": "e1"}, {("start", "new"), ("new", "answer")}),
    ],
)
def test_insert_operations_rewire_one_server_resolved_edge(
    placement, target, expected_edges
):
    base = {
        "nodes": [_node("start", "startNode"), _node("answer", "answerNode")],
        "edges": [{"id": "e1", "source": "start", "target": "answer"}],
    }

    operations = build_insert_operations(
        base_graph=base,
        placement=placement,
        target=target,
        node=_node("new", "llmNode"),
    )
    result = apply_graph_operations(base, operations)

    assert {(edge["source"], edge["target"]) for edge in result["edges"]} == expected_edges


def test_insert_after_rejects_ambiguous_multiple_outgoing_edges():
    base = {
        "nodes": [
            _node("condition", "conditionNode"),
            _node("a", "answerNode"),
            _node("b", "answerNode"),
        ],
        "edges": [
            {"id": "e1", "source": "condition", "target": "a"},
            {"id": "e2", "source": "condition", "target": "b"},
        ],
    }

    with pytest.raises(GraphMutationValidationError, match="clarification"):
        build_insert_operations(
            base_graph=base,
            placement="after",
            target={"node_id": "condition"},
            node=_node("new", "llmNode"),
        )
