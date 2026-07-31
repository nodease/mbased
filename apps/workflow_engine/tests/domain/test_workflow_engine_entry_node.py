from types import SimpleNamespace

import pytest

from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine


def _engine_with_nodes(*, entry_node_id: str | None) -> WorkflowEngine:
    engine = WorkflowEngine.__new__(WorkflowEngine)
    engine.start_node_id = entry_node_id
    engine.user_input = {}
    engine.node_schemas = {
        "body": SimpleNamespace(type="templateNode"),
    }
    return engine


def test_validated_entry_node_allows_triggerless_loop_body() -> None:
    engine = _engine_with_nodes(entry_node_id="body")

    engine._check_start_nodes()

    assert engine.start_node_id == "body"


def test_triggerless_graph_without_validated_entry_remains_invalid() -> None:
    engine = _engine_with_nodes(entry_node_id=None)

    with pytest.raises(ValueError, match="시작 노드"):
        engine._check_start_nodes()


def test_unknown_validated_entry_is_rejected() -> None:
    engine = _engine_with_nodes(entry_node_id="missing")

    with pytest.raises(ValueError, match="진입 노드"):
        engine._check_start_nodes()


def test_validated_entry_node_receives_loop_iteration_context() -> None:
    engine = _engine_with_nodes(entry_node_id="body")
    iteration_context = {
        "source": {"items": ["alpha"]},
        "loop": {"item": "alpha", "index": 0},
    }
    engine.user_input = iteration_context

    context = engine._get_context(
        "body",
        {"completed": {"value": "must-not-replace-iteration-input"}},
    )

    assert context == iteration_context


def test_non_entry_node_receives_completed_results() -> None:
    engine = _engine_with_nodes(entry_node_id="body")
    engine.node_schemas["next"] = SimpleNamespace(type="llmNode")
    engine.user_input = {"loop": {"item": "alpha", "index": 0}}
    results = {"body": {"text": "rendered"}}

    assert engine._get_context("next", results) == results
