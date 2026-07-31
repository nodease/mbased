import pytest

WorkflowEngine = pytest.importorskip(
    "apps.workflow_engine.workflow.core.workflow_engine",
    exc_type=ImportError,
).WorkflowEngine


def test_condition_runtime_routes_only_the_selected_explicit_handle():
    engine = WorkflowEngine.__new__(WorkflowEngine)
    engine.edge_handles = {
        ("condition", "case-high"): ["high-answer"],
        ("condition", "default"): ["default-answer"],
        ("condition", None): ["implicit-answer"],
    }
    engine.adjacency_list = {
        "condition": ["high-answer", "default-answer", "implicit-answer"]
    }

    assert engine._get_next_nodes(
        "condition", {"selected_handle": "case-high"}
    ) == ["high-answer"]
    assert engine._get_next_nodes(
        "condition", {"selected_handle": "default"}
    ) == ["default-answer"]
    assert engine._get_next_nodes(
        "condition", {"selected_handle": "case-unconnected"}
    ) == []
