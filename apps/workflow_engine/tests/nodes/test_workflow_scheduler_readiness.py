import gevent
import pytest

from apps.workflow_engine.domain.external_effect import ExternalEffectError
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine


class _RecordingNode:
    def __init__(
        self,
        node_id: str,
        events: list[tuple[str, str, frozenset[str]]],
        *,
        delay: float = 0,
        selected_handle: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.node_id = node_id
        self.events = events
        self.delay = delay
        self.selected_handle = selected_handle
        self.error = error

    def execute(self, inputs, runtime_control=None):
        del runtime_control
        input_ids = frozenset(inputs)
        self.events.append(("start", self.node_id, input_ids))
        if self.delay:
            gevent.sleep(self.delay)
        if self.error is not None:
            raise self.error
        self.events.append(("finish", self.node_id, input_ids))
        result = {"value": self.node_id}
        if self.selected_handle is not None:
            result["selected_handle"] = self.selected_handle
        return result


def _node(node_id: str, node_type: str = "templateNode", **data):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"title": node_id, **data},
    }


def _install_recording_nodes(
    engine: WorkflowEngine,
    events: list[tuple[str, str, frozenset[str]]],
    **options,
) -> None:
    for node_id in engine.node_instances:
        engine.node_instances[node_id] = _RecordingNode(
            node_id,
            events,
            **options.get(node_id, {}),
        )


def _starts(events, node_id: str):
    return [event for event in events if event[0] == "start" and event[1] == node_id]


def test_join_waits_for_every_active_control_predecessor():
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("slow"),
            _node("fast"),
            _node("join", "answerNode"),
        ],
        "edges": [
            {"id": "start-slow", "source": "start", "target": "slow"},
            {"id": "start-fast", "source": "start", "target": "fast"},
            {"id": "slow-join", "source": "slow", "target": "join"},
            {"id": "fast-join", "source": "fast", "target": "join"},
        ],
    }
    events = []
    engine = WorkflowEngine(graph=graph)
    _install_recording_nodes(engine, events, slow={"delay": 0.05})

    result = engine.execute()

    join_start = _starts(events, "join")
    assert len(join_start) == 1
    assert {"slow", "fast"}.issubset(join_start[0][2])
    assert "join" in result


@pytest.mark.parametrize("selected_handle", ["case-selected", "default"])
def test_join_ignores_condition_branch_that_was_not_selected(selected_handle):
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("condition", "conditionNode", cases=[]),
            _node("case-path"),
            _node("default-path"),
            _node("join", "answerNode"),
        ],
        "edges": [
            {"id": "start-condition", "source": "start", "target": "condition"},
            {
                "id": "condition-case",
                "source": "condition",
                "target": "case-path",
                "sourceHandle": "case-selected",
            },
            {
                "id": "condition-default",
                "source": "condition",
                "target": "default-path",
                "sourceHandle": "default",
            },
            {"id": "case-join", "source": "case-path", "target": "join"},
            {"id": "default-join", "source": "default-path", "target": "join"},
        ],
    }
    events = []
    engine = WorkflowEngine(graph=graph)
    _install_recording_nodes(
        engine,
        events,
        condition={"selected_handle": selected_handle},
    )

    result = engine.execute()

    selected_node = (
        "case-path" if selected_handle == "case-selected" else "default-path"
    )
    skipped_node = "default-path" if selected_node == "case-path" else "case-path"
    assert len(_starts(events, selected_node)) == 1
    assert _starts(events, skipped_node) == []
    assert len(_starts(events, "join")) == 1
    assert "join" in result


def test_inactive_branches_reconverge_without_blocking_selected_path():
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("condition", "conditionNode", cases=[]),
            _node("inactive-a"),
            _node("inactive-b"),
            _node("inactive-merge"),
            _node("selected"),
            _node("final", "answerNode"),
        ],
        "edges": [
            {"id": "start-condition", "source": "start", "target": "condition"},
            {
                "id": "condition-a",
                "source": "condition",
                "target": "inactive-a",
                "sourceHandle": "case-a",
            },
            {
                "id": "condition-b",
                "source": "condition",
                "target": "inactive-b",
                "sourceHandle": "case-b",
            },
            {
                "id": "condition-default",
                "source": "condition",
                "target": "selected",
                "sourceHandle": "default",
            },
            {"id": "a-merge", "source": "inactive-a", "target": "inactive-merge"},
            {"id": "b-merge", "source": "inactive-b", "target": "inactive-merge"},
            {
                "id": "inactive-final",
                "source": "inactive-merge",
                "target": "final",
            },
            {"id": "selected-final", "source": "selected", "target": "final"},
        ],
    }
    events = []
    engine = WorkflowEngine(graph=graph)
    _install_recording_nodes(
        engine,
        events,
        condition={"selected_handle": "default"},
    )

    result = engine.execute()

    assert _starts(events, "inactive-a") == []
    assert _starts(events, "inactive-b") == []
    assert _starts(events, "inactive-merge") == []
    assert len(_starts(events, "selected")) == 1
    assert len(_starts(events, "final")) == 1
    assert "final" in result


def test_selector_only_dependency_completion_wakes_target():
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("extract"),
            _node(
                "target",
                "answerNode",
                outputs=[
                    {
                        "variable": "result",
                        "value_selector": ["extract", "value"],
                    }
                ],
            ),
        ],
        "edges": [
            {"id": "start-extract", "source": "start", "target": "extract"},
            {"id": "start-target", "source": "start", "target": "target"},
        ],
    }
    events = []
    engine = WorkflowEngine(graph=graph)
    _install_recording_nodes(engine, events, extract={"delay": 0.02})

    result = engine.execute()

    target_start = _starts(events, "target")
    assert len(target_start) == 1
    assert "extract" in target_start[0][2]
    assert "target" in result


def test_inactive_selector_source_makes_dependent_node_inactive():
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("condition", "conditionNode", cases=[]),
            _node("inactive-source"),
            _node(
                "target",
                "answerNode",
                outputs=[
                    {
                        "variable": "result",
                        "value_selector": ["inactive-source", "value"],
                    }
                ],
            ),
        ],
        "edges": [
            {"id": "start-condition", "source": "start", "target": "condition"},
            {
                "id": "condition-case",
                "source": "condition",
                "target": "inactive-source",
                "sourceHandle": "case-selected",
            },
            {
                "id": "condition-default",
                "source": "condition",
                "target": "target",
                "sourceHandle": "default",
            },
        ],
    }
    events = []
    engine = WorkflowEngine(graph=graph)
    _install_recording_nodes(
        engine,
        events,
        condition={"selected_handle": "default"},
    )

    result = engine.execute()

    assert _starts(events, "inactive-source") == []
    assert _starts(events, "target") == []
    assert "target" not in result


def test_failed_active_predecessor_never_starts_join():
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("failing"),
            _node("slow"),
            _node("join", "answerNode"),
        ],
        "edges": [
            {"id": "start-failing", "source": "start", "target": "failing"},
            {"id": "start-slow", "source": "start", "target": "slow"},
            {"id": "failing-join", "source": "failing", "target": "join"},
            {"id": "slow-join", "source": "slow", "target": "join"},
        ],
    }
    events = []
    engine = WorkflowEngine(graph=graph)
    _install_recording_nodes(
        engine,
        events,
        failing={"error": RuntimeError("provider failure")},
        slow={"delay": 0.05},
    )

    with pytest.raises(ValueError, match="node_error"):
        engine.execute()

    assert _starts(events, "join") == []


def test_workflow_timeout_never_starts_downstream_node():
    graph = {
        "nodes": [
            _node("start", "startNode"),
            _node("slow"),
            _node("downstream", "answerNode"),
        ],
        "edges": [
            {"id": "start-slow", "source": "start", "target": "slow"},
            {"id": "slow-downstream", "source": "slow", "target": "downstream"},
        ],
    }
    events = []
    engine = WorkflowEngine(graph=graph, workflow_timeout=0.01)
    _install_recording_nodes(engine, events, slow={"delay": 0.1})

    with pytest.raises(ValueError, match="timeout"):
        engine.execute()

    assert _starts(events, "downstream") == []


def test_expired_task_deadline_blocks_file_fetch_node_before_execution(
    monkeypatch,
) -> None:
    graph = {
        "nodes": [
            _node(
                "extract",
                "fileExtractionNode",
                referenced_variables=[],
            ),
        ],
        "edges": [],
    }
    events = []
    engine = WorkflowEngine(
        graph=graph,
        is_subworkflow=True,
        entry_node_id="extract",
        task_deadline=10.0,
    )
    _install_recording_nodes(engine, events)
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_engine.time.monotonic",
        lambda: 10.0,
    )

    with pytest.raises(ExternalEffectError) as exc_info:
        engine.execute()

    assert exc_info.value.code == "external_effect.deadline_exceeded"
    assert exc_info.value.retryable is False
    assert exc_info.value.node_id == "extract"
    assert _starts(events, "extract") == []
