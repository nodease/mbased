import json
from pathlib import Path

from apps.shared.services.workflow_layout import calculate_workflow_auto_layout

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "workflow_layout_cases.json"
)


def test_workflow_layout_matches_canonical_fixtures():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    for case in fixture["cases"]:
        layouted = calculate_workflow_auto_layout(
            {"nodes": case["nodes"], "edges": case["edges"]}
        )
        positions = {
            node["id"]: node["position"] for node in layouted["nodes"]
        }
        assert positions == case["expected_positions"], case["name"]


def test_workflow_layout_uses_measured_node_size_for_sibling_spacing():
    graph = {
        "nodes": [
            {"id": "start", "type": "startNode", "position": {"x": 0, "y": 0}},
            {
                "id": "large",
                "type": "conditionNode",
                "position": {"x": 0, "y": 0},
                "measured": {"width": 420, "height": 500},
            },
            {"id": "small", "type": "llmNode", "position": {"x": 0, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "large"},
            {"id": "e2", "source": "start", "target": "small"},
        ],
    }

    layouted = calculate_workflow_auto_layout(graph)
    positions = {node["id"]: node["position"] for node in layouted["nodes"]}

    assert positions["small"]["y"] == 600
