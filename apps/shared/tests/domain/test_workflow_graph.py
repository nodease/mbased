import pytest
from apps.shared.domain.workflow_graph import (
    MAX_WORKFLOW_GRAPH_EDGES,
    MAX_WORKFLOW_GRAPH_NESTING_DEPTH,
    MAX_WORKFLOW_GRAPH_NODES,
    MAX_WORKFLOW_NODE_ID_LENGTH,
    WorkflowGraphValidationError,
    validate_loop_subgraph,
    validate_workflow_graph,
)
from apps.shared.schemas.workflow import EdgeSchema, NodeSchema


def _node(node_id: str, node_type: str, data: dict | None = None) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": data or {},
    }


def _valid_graph() -> dict:
    return {
        "nodes": [
            _node("start", "startNode"),
            _node("llm", "llmNode"),
            _node("answer", "answerNode"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "llm"},
            {"id": "e2", "source": "llm", "target": "answer"},
        ],
    }


def test_valid_workflow_graph_passes() -> None:
    graph = _valid_graph()

    validate_workflow_graph(graph)
    assert all(NodeSchema.model_validate(node) for node in graph["nodes"])
    assert all(EdgeSchema.model_validate(edge) for edge in graph["edges"])


def test_loop_subgraph_uses_single_zero_incoming_node_as_entry() -> None:
    graph = {
        "nodes": [
            _node("first", "templateNode"),
            _node("second", "llmNode"),
        ],
        "edges": [{"id": "body-edge", "source": "first", "target": "second"}],
    }

    assert validate_loop_subgraph(graph) == "first"


def test_loop_subgraph_rejects_multiple_implicit_entries() -> None:
    graph = {
        "nodes": [
            _node("first", "templateNode"),
            _node("second", "llmNode"),
        ],
        "edges": [],
    }

    with pytest.raises(WorkflowGraphValidationError) as exc_info:
        validate_loop_subgraph(graph)

    assert exc_info.value.code == "workflow_start_node_invalid"


def test_root_graph_still_requires_explicit_trigger() -> None:
    graph = {
        "nodes": [_node("body", "templateNode")],
        "edges": [],
    }

    assert validate_loop_subgraph(graph) == "body"
    with pytest.raises(WorkflowGraphValidationError) as exc_info:
        validate_workflow_graph(graph)

    assert exc_info.value.code == "workflow_start_node_invalid"


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda graph: graph["edges"].append(
                {"id": "dangling", "source": "missing", "target": "llm"}
            ),
            "workflow_edge_source_missing",
        ),
        (
            lambda graph: graph["edges"].append(
                {"id": "cycle", "source": "llm", "target": "llm"}
            ),
            "workflow_cycle_detected",
        ),
        (
            lambda graph: graph["nodes"].append(_node("llm", "llmNode")),
            "workflow_node_invalid",
        ),
        (
            lambda graph: graph["nodes"].append(_node("isolated", "llmNode")),
            "workflow_isolated_node",
        ),
    ],
)
def test_invalid_structure_fails_closed(mutate, expected_code: str) -> None:
    graph = _valid_graph()
    mutate(graph)

    with pytest.raises(WorkflowGraphValidationError) as exc_info:
        validate_workflow_graph(graph)

    assert exc_info.value.code == expected_code


def test_node_id_length_matches_durable_location_contract() -> None:
    graph = _valid_graph()
    graph["nodes"][1]["id"] = "n" * (MAX_WORKFLOW_NODE_ID_LENGTH + 1)
    graph["edges"][0]["target"] = graph["nodes"][1]["id"]
    graph["edges"][1]["source"] = graph["nodes"][1]["id"]

    with pytest.raises(WorkflowGraphValidationError) as exc_info:
        validate_workflow_graph(graph)

    assert exc_info.value.code == "workflow_node_invalid"


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda graph: graph["nodes"][0].pop("position"), "workflow_node_invalid"),
        (
            lambda graph: graph["nodes"][0].update({"position": {"x": "0", "y": 0}}),
            "workflow_node_invalid",
        ),
        (
            lambda graph: graph["nodes"][0].update(
                {"position": {"x": 10**1000, "y": 0}}
            ),
            "workflow_node_invalid",
        ),
        (lambda graph: graph["edges"][0].pop("id"), "workflow_edge_invalid"),
    ],
)
def test_runtime_required_shape_fails_before_publish(
    mutate, expected_code: str
) -> None:
    graph = _valid_graph()
    mutate(graph)

    with pytest.raises(WorkflowGraphValidationError) as exc_info:
        validate_workflow_graph(graph)

    assert exc_info.value.code == expected_code


def test_nested_loop_graph_uses_same_structure_contract() -> None:
    graph = _valid_graph()
    nested = _valid_graph()
    nested["edges"].append(
        {"id": "nested-dangling", "source": "missing", "target": "llm"}
    )
    graph["nodes"][1] = {
        "id": "loop",
        "type": "loopNode",
        "position": {"x": 0, "y": 0},
        "data": {"subGraph": nested},
    }
    graph["edges"][0]["target"] = "loop"
    graph["edges"][1]["source"] = "loop"

    with pytest.raises(WorkflowGraphValidationError) as exc_info:
        validate_workflow_graph(graph)

    assert exc_info.value.code == "workflow_edge_source_missing"


def test_node_count_is_bounded() -> None:
    graph = {
        "nodes": [
            _node("start", "startNode"),
            *[
                _node(f"note-{index}", "note")
                for index in range(MAX_WORKFLOW_GRAPH_NODES)
            ],
        ],
        "edges": [],
    }

    with pytest.raises(WorkflowGraphValidationError) as exc_info:
        validate_workflow_graph(graph)

    assert exc_info.value.code == "workflow_graph_too_many_nodes"


def test_node_count_accepts_exact_limit() -> None:
    graph = {
        "nodes": [
            _node("start", "startNode"),
            *[
                _node(f"note-{index}", "note")
                for index in range(MAX_WORKFLOW_GRAPH_NODES - 1)
            ],
        ],
        "edges": [],
    }

    validate_workflow_graph(graph)


def test_edge_count_is_bounded() -> None:
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("llm", "llmNode"),
        ],
        "edges": [
            {"id": f"edge-{index}", "source": "start", "target": "llm"}
            for index in range(MAX_WORKFLOW_GRAPH_EDGES + 1)
        ],
    }

    with pytest.raises(WorkflowGraphValidationError) as exc_info:
        validate_workflow_graph(graph)

    assert exc_info.value.code == "workflow_graph_too_many_edges"


def test_edge_count_accepts_exact_limit() -> None:
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("llm", "llmNode"),
        ],
        "edges": [
            {"id": f"edge-{index}", "source": "start", "target": "llm"}
            for index in range(MAX_WORKFLOW_GRAPH_EDGES)
        ],
    }

    validate_workflow_graph(graph)


def test_nested_graph_depth_accepts_exact_limit_and_rejects_one_more() -> None:
    validate_workflow_graph(_nested_graph(MAX_WORKFLOW_GRAPH_NESTING_DEPTH))

    with pytest.raises(WorkflowGraphValidationError) as exc_info:
        validate_workflow_graph(_nested_graph(MAX_WORKFLOW_GRAPH_NESTING_DEPTH + 1))

    assert exc_info.value.code == "workflow_graph_too_deep"


def _nested_graph(nesting_depth: int) -> dict:
    graph = {
        "nodes": [_node("start-leaf", "startNode")],
        "edges": [],
    }
    for depth in range(nesting_depth):
        start_id = f"start-{depth}"
        loop_id = f"loop-{depth}"
        graph = {
            "nodes": [
                _node(start_id, "startNode"),
                _node(loop_id, "loopNode", {"subGraph": graph}),
            ],
            "edges": [{"id": f"edge-{depth}", "source": start_id, "target": loop_id}],
        }
    return graph
