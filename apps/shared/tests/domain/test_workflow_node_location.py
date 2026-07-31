import hashlib

import pytest
from apps.shared.domain.workflow_graph import MAX_WORKFLOW_GRAPH_NESTING_DEPTH
from apps.shared.domain.workflow_node_location import (
    MAX_CANONICAL_NODE_ID_LENGTH,
    CanonicalWorkflowNodeLocation,
    WorkflowNodeLocationError,
    find_workflow_node_at_location,
    iter_workflow_node_locations,
)


def _node(node_id: str, node_type: str, data: dict | None = None) -> dict:
    return {"id": node_id, "type": node_type, "data": data or {}}


def _frame(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded


def test_location_digest_uses_versioned_length_framing() -> None:
    location = CanonicalWorkflowNodeLocation(
        container_path=(("loop", "loop-a"), ("loop", "loop-b")),
        node_id="llm-1",
    )
    expected = b"".join(
        (
            _frame("nodease:canonical-node-location:v1"),
            (2).to_bytes(4, "big"),
            _frame("loop"),
            _frame("loop-a"),
            _frame("loop"),
            _frame("loop-b"),
            _frame("llm-1"),
        )
    )

    assert location.digest == hashlib.sha256(expected).hexdigest()
    assert location.to_container_path_payload() == [
        {"kind": "loop", "node_id": "loop-a"},
        {"kind": "loop", "node_id": "loop-b"},
    ]


def test_canonical_location_exposes_only_a_bounded_opaque_audit_reference():
    location = CanonicalWorkflowNodeLocation(
        container_path=(("loop", "private-loop-name"),),
        node_id="private-node-name",
    )

    assert location.safe_reference == f"workflow-node-location:v1:{location.digest}"
    assert "private-loop-name" not in location.safe_reference
    assert "private-node-name" not in location.safe_reference


def test_walker_distinguishes_repeated_node_ids_in_different_containers() -> None:
    graph = {
        "nodes": [
            _node(
                "loop-a",
                "loopNode",
                {"subGraph": {"nodes": [_node("llm-1", "llmNode")], "edges": []}},
            ),
            _node(
                "loop-b",
                "loopNode",
                {"subGraph": {"nodes": [_node("llm-1", "llmNode")], "edges": []}},
            ),
        ],
        "edges": [],
    }

    locations = [
        located.location
        for located in iter_workflow_node_locations(graph)
        if located.node.get("type") == "llmNode"
    ]

    assert locations == [
        CanonicalWorkflowNodeLocation((("loop", "loop-a"),), "llm-1"),
        CanonicalWorkflowNodeLocation((("loop", "loop-b"),), "llm-1"),
    ]
    assert locations[0].digest != locations[1].digest
    assert find_workflow_node_at_location(graph, locations[1]) is graph["nodes"][1][
        "data"
    ]["subGraph"]["nodes"][0]


def test_root_location_does_not_fall_back_to_nested_node() -> None:
    graph = {
        "nodes": [
            _node(
                "loop-a",
                "loopNode",
                {"subGraph": {"nodes": [_node("llm-1", "llmNode")], "edges": []}},
            )
        ],
        "edges": [],
    }

    with pytest.raises(WorkflowNodeLocationError) as exc_info:
        find_workflow_node_at_location(
            graph,
            CanonicalWorkflowNodeLocation((), "llm-1"),
        )

    assert exc_info.value.code == "workflow_node_location.not_found"


@pytest.mark.parametrize(
    "path",
    [
        [{"kind": "future", "node_id": "parent"}],
        [{"kind": "loop", "node_id": ""}],
        [{"kind": "loop", "node_id": "x" * (MAX_CANONICAL_NODE_ID_LENGTH + 1)}],
        [{"kind": "loop", "node_id": "parent", "extra": True}],
        "loop/parent",
    ],
)
def test_location_parser_rejects_noncanonical_segments(path: object) -> None:
    with pytest.raises(WorkflowNodeLocationError) as exc_info:
        CanonicalWorkflowNodeLocation.from_container_path_payload(
            container_path=path,
            node_id="llm-1",
        )

    assert exc_info.value.code == "workflow_node_location.invalid"


def test_location_rejects_excessive_nesting_and_duplicate_local_ids() -> None:
    with pytest.raises(WorkflowNodeLocationError) as exc_info:
        CanonicalWorkflowNodeLocation(
            tuple(
                ("loop", f"loop-{index}")
                for index in range(MAX_WORKFLOW_GRAPH_NESTING_DEPTH + 1)
            ),
            "llm-1",
        )
    assert exc_info.value.code == "workflow_node_location.too_deep"

    graph = {
        "nodes": [_node("same", "llmNode"), _node("same", "templateNode")],
        "edges": [],
    }
    with pytest.raises(WorkflowNodeLocationError) as exc_info:
        iter_workflow_node_locations(graph)
    assert exc_info.value.code == "workflow_node_location.ambiguous"


@pytest.mark.parametrize("node_id", ["\ud800", "node-\udfff"])
def test_location_rejects_node_ids_that_cannot_be_encoded_as_utf8(
    node_id: str,
) -> None:
    with pytest.raises(WorkflowNodeLocationError) as exc_info:
        CanonicalWorkflowNodeLocation((), node_id)

    assert exc_info.value.code == "workflow_node_location.invalid"
