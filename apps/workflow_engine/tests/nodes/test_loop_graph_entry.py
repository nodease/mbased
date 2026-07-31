import uuid

import pytest

from apps.shared.domain.workflow_execution_identity import InvocationSegment
from apps.workflow_engine.domain.execution import NodeExecutionControl
from apps.workflow_engine.domain.external_effect import ExternalEffectContext
from apps.workflow_engine.workflow.core.runtime_dependencies import (
    WorkflowRuntimeDependencies,
)
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine
from apps.workflow_engine.workflow.nodes.loop.loop_node import (
    LoopNode,
    LoopNodeData,
    LoopNodeInput,
)


def _loop_node(
    *, subgraph: dict, error_strategy: str = "end", loop_key: str = "items"
) -> LoopNode:
    return LoopNode(
        id="loop-1",
        data=LoopNodeData(
            title="Loop",
            loop_key=loop_key,
            error_strategy=error_strategy,
            subGraph=subgraph,
        ),
    )


def test_loop_child_inherits_parent_runtime_dependencies(monkeypatch) -> None:
    node = _loop_node(
        subgraph={
            "nodes": [
                {
                    "id": "body",
                    "type": "templateNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                }
            ],
            "edges": [],
        }
    )
    runtime_dependencies = WorkflowRuntimeDependencies(
        provider_execution_runtime=object(),
        provider_usage_recorder=object(),
    )
    node.bind_runtime_dependencies(runtime_dependencies)
    captured: dict = {}

    class _ChildEngine:
        _external_effect_output_sensitive = False

        def execute(self):
            return {"result": "ok"}

        def cleanup(self):
            return None

    def create_child(_cls, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _ChildEngine()

    monkeypatch.setattr(WorkflowEngine, "create_child", classmethod(create_child))

    result = node._execute_subgraph_scoped(
        {},
        iteration_index=0,
        entry_node_id="body",
    )

    assert result == {"result": "ok"}
    assert captured["kwargs"]["runtime_dependencies"] is runtime_dependencies


def test_loop_child_appends_its_id_to_the_trusted_binding_container_path(
    monkeypatch,
) -> None:
    node = _loop_node(
        subgraph={
            "nodes": [
                {
                    "id": "body",
                    "type": "templateNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                }
            ],
            "edges": [],
        }
    )
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    control = NodeExecutionControl(
        execution_id=execution_id,
        invocation_path_prefix=(InvocationSegment("root", "", "workflow"),),
        external_effect_context=ExternalEffectContext(
            organization_id=organization_id,
            app_id=uuid.uuid4(),
            workflow_id=workflow_id,
            execution_id=execution_id,
            node_invocation_id=uuid.uuid4(),
            node_id="loop-1",
        ),
        external_effect_enforced=True,
        binding_container_path=(("loop", "outer-loop"),),
    )
    captured: dict = {}

    class _ChildEngine:
        _external_effect_output_sensitive = False

        def execute(self):
            return {"result": "ok"}

        def cleanup(self):
            return None

    def create_child(_cls, *args, **kwargs):
        captured["kwargs"] = kwargs
        return _ChildEngine()

    monkeypatch.setattr(WorkflowEngine, "create_child", classmethod(create_child))

    result = node.execute({"items": ["one"]}, runtime_control=control)

    assert result["results"] == [{"result": "ok"}]
    assert captured["kwargs"]["binding_container_path"] == (
        ("loop", "outer-loop"),
        ("loop", "loop-1"),
    )


def test_loop_without_explicit_key_uses_first_mapped_array() -> None:
    node = _loop_node(subgraph={"nodes": [], "edges": []}, loop_key="")

    assert node._get_iteration_array(
        {}, {"scalar": "ignored", "items": ["a", "b"]}
    ) == ["a", "b"]


def test_loop_body_uses_validated_implicit_entry(monkeypatch) -> None:
    node = _loop_node(
        subgraph={
            "nodes": [
                {
                    "id": "first",
                    "type": "templateNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                },
                {
                    "id": "second",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                },
            ],
            "edges": [{"id": "first-second", "source": "first", "target": "second"}],
        }
    )
    captured_entries: list[str] = []

    def execute_body(context, *, iteration_index, entry_node_id):
        captured_entries.append(entry_node_id)
        return {"iteration": iteration_index}

    monkeypatch.setattr(node, "_execute_subgraph_scoped", execute_body)

    result = node._run({"items": ["a", "b"]})

    assert captured_entries == ["first", "first"]
    assert result["results"] == [{"iteration": 0}, {"iteration": 1}]


def test_loop_body_context_includes_explicit_mapped_inputs(monkeypatch) -> None:
    node = _loop_node(
        subgraph={
            "nodes": [
                {
                    "id": "body",
                    "type": "templateNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                }
            ],
            "edges": [],
        },
        loop_key="",
    )
    node.data.inputs = [
        LoopNodeInput(name="items", value_selector=["source", "items"]),
        LoopNodeInput(name="mail", value_selector=["source", "mail"]),
    ]
    captured_contexts: list[dict] = []

    def execute_body(context, *, iteration_index, entry_node_id):
        captured_contexts.append(context)
        return {"iteration": iteration_index, "entry": entry_node_id}

    monkeypatch.setattr(node, "_execute_subgraph_scoped", execute_body)

    node._run(
        {
            "source": {
                "items": ["first"],
                "mail": {"processing_ref": "opaque-reference"},
            }
        }
    )

    assert captured_contexts == [
        {
            "source": {
                "items": ["first"],
                "mail": {"processing_ref": "opaque-reference"},
            },
            "items": ["first"],
            "mail": {"processing_ref": "opaque-reference"},
            "loop": {"item": "first", "index": 0},
        }
    ]


def test_loop_structure_error_is_not_swallowed_by_continue_strategy() -> None:
    node = _loop_node(
        subgraph={
            "nodes": [
                {
                    "id": "first",
                    "type": "templateNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                },
                {
                    "id": "second",
                    "type": "llmNode",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                },
            ],
            "edges": [],
        },
        error_strategy="continue",
    )

    with pytest.raises(ValueError, match="workflow_graph_invalid"):
        node._run({"items": ["a"]})
