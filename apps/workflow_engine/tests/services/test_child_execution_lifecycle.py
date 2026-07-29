import uuid
from unittest.mock import MagicMock, Mock

import pytest

from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.domain.workflow_execution_identity import InvocationSegment
from apps.workflow_engine.domain.execution import NodeExecutionControl
from apps.workflow_engine.workflow.core import workflow_engine as engine_module
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine
from apps.workflow_engine.workflow.nodes.template.template_node import TemplateNode


def _body_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "body",
                "type": "templateNode",
                "position": {"x": 0, "y": 0},
                "data": {"title": "Body", "template": "ok"},
            }
        ],
        "edges": [],
    }


def _loop_graph(*, error_strategy: str = "end") -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "Start",
                    "variables": [
                        {
                            "id": "items",
                            "name": "items",
                            "label": "Items",
                            "type": "text",
                        }
                    ],
                },
            },
            {
                "id": "loop",
                "type": "loopNode",
                "position": {"x": 160, "y": 0},
                "data": {
                    "title": "Loop",
                    "loop_key": "items",
                    "inputs": [
                        {
                            "name": "items",
                            "value_selector": ["start", "items"],
                        }
                    ],
                    "error_strategy": error_strategy,
                    "subGraph": _body_graph(),
                },
            },
        ],
        "edges": [{"id": "start-loop", "source": "start", "target": "loop"}],
    }


def _nested_loop_graph() -> dict:
    graph = _loop_graph()
    graph["nodes"][1]["data"]["subGraph"] = {
        "nodes": [
            {
                "id": "inner-loop",
                "type": "loopNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "Inner Loop",
                    "loop_key": "loop.item",
                    "subGraph": _body_graph(),
                },
            }
        ],
        "edges": [],
    }
    return graph


def _workflow_node_graph(*, target_app_id: uuid.UUID) -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "title": "Start",
                    "variables": [
                        {
                            "id": "items",
                            "name": "items",
                            "label": "Items",
                            "type": "text",
                        }
                    ],
                },
            },
            {
                "id": "child-workflow",
                "type": "workflowNode",
                "position": {"x": 160, "y": 0},
                "data": {
                    "title": "Child Workflow",
                    "appId": str(target_app_id),
                    "workflowId": str(uuid.uuid4()),
                    "inputs": [
                        {
                            "name": "items",
                            "value_selector": ["start", "items"],
                        }
                    ],
                },
            },
        ],
        "edges": [
            {
                "id": "start-child",
                "source": "start",
                "target": "child-workflow",
            }
        ],
    }


def _execution_context(*, run_id: uuid.UUID, workflow_id: uuid.UUID) -> dict:
    return {
        "workflow_run_id": str(run_id),
        "workflow_id": str(workflow_id),
        "organization_id": str(uuid.uuid4()),
        "app_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
    }


def _runtime_control(*, execution_id: uuid.UUID, workflow_id: uuid.UUID):
    return NodeExecutionControl(
        execution_id=execution_id,
        invocation_path_prefix=(InvocationSegment("root", "", str(workflow_id)),),
        external_effect_context=None,
        task_deadline=1234.5,
    )


def _capture_lifecycle(monkeypatch):
    logger = Mock()
    logger.create_node_log.return_value = None
    published: list[tuple] = []
    monkeypatch.setattr(engine_module, "WorkflowLogger", lambda _db: logger)
    monkeypatch.setattr(
        engine_module,
        "publish_workflow_event",
        lambda *args: published.append(args),
    )
    return logger, published


def test_child_factory_forces_child_lifecycle_and_preserves_runtime_control() -> None:
    execution_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    run_id = uuid.uuid4()
    context = _execution_context(run_id=run_id, workflow_id=workflow_id)
    control = _runtime_control(execution_id=execution_id, workflow_id=workflow_id)
    segment = InvocationSegment("loop", "loop", "0")

    child = WorkflowEngine.create_child(
        _body_graph(),
        {"item": "first"},
        execution_context=context,
        runtime_control=control,
        invocation_segment=segment,
        parent_run_id=str(run_id),
        entry_node_id="body",
    )

    try:
        assert child.is_subworkflow is True
        assert child.execution_id == execution_id
        assert child.invocation_path_prefix == control.invocation_path_prefix + (
            segment,
        )
        assert child.task_deadline == control.task_deadline
        assert child.execution_context["workflow_run_id"] == str(run_id)
        assert "db_session_factory" not in context
    finally:
        child.cleanup()


def test_child_factory_propagates_public_content_suppression() -> None:
    run_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    context = _execution_context(run_id=run_id, workflow_id=workflow_id)
    context["suppress_content_persistence"] = True

    child = WorkflowEngine.create_child(
        _body_graph(),
        {"item": "private child input"},
        execution_context=context,
        runtime_control=_runtime_control(
            execution_id=uuid.uuid4(),
            workflow_id=workflow_id,
        ),
        invocation_segment=InvocationSegment("loop", "loop", "0"),
        parent_run_id=str(run_id),
        entry_node_id="body",
    )

    try:
        assert child.logger.content_persistence_suppressed is True
    finally:
        child.cleanup()


def test_multiple_loop_iterations_finalize_root_lifecycle_once(monkeypatch) -> None:
    logger, published = _capture_lifecycle(monkeypatch)
    run_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    engine = WorkflowEngine(
        _loop_graph(),
        user_input={"items": ["first", "second"]},
        execution_context=_execution_context(
            run_id=run_id,
            workflow_id=workflow_id,
        ),
        execution_id=uuid.uuid4(),
    )

    try:
        result = engine.execute()
    finally:
        engine.cleanup()

    assert len(result["loop"]["results"]) == 2
    logger.create_run_log.assert_called_once()
    logger.update_run_log_finish.assert_called_once()
    logger.update_run_log_error.assert_not_called()
    assert logger.create_node_log.call_count == 2
    assert [event[1] for event in published].count("workflow_finish") == 1
    assert [event[1] for event in published].count("error") == 0


def test_nested_loops_do_not_duplicate_root_lifecycle(monkeypatch) -> None:
    logger, published = _capture_lifecycle(monkeypatch)
    run_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    engine = WorkflowEngine(
        _nested_loop_graph(),
        user_input={"items": [["first", "second"], ["third"]]},
        execution_context=_execution_context(
            run_id=run_id,
            workflow_id=workflow_id,
        ),
        execution_id=uuid.uuid4(),
    )

    try:
        result = engine.execute()
    finally:
        engine.cleanup()

    assert len(result["loop"]["results"]) == 2
    logger.create_run_log.assert_called_once()
    logger.update_run_log_finish.assert_called_once()
    logger.update_run_log_error.assert_not_called()
    assert logger.create_node_log.call_count == 2
    assert [event[1] for event in published].count("workflow_finish") == 1
    assert [event[1] for event in published].count("error") == 0


def test_workflow_node_with_loop_child_does_not_duplicate_root_lifecycle(
    monkeypatch,
) -> None:
    logger, published = _capture_lifecycle(monkeypatch)
    run_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    target_app_id = uuid.uuid4()
    target_workflow_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    target_graph = _loop_graph()
    app = Mock(
        id=target_app_id,
        organization_id=uuid.uuid4(),
        workflow_id=target_workflow_id,
        active_deployment_id=deployment_id,
    )
    deployment = Mock(
        id=deployment_id,
        app_id=target_app_id,
        version=1,
        type=DeploymentType.WORKFLOW_NODE,
        graph_snapshot=target_graph,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [app, deployment]
    context = _execution_context(run_id=run_id, workflow_id=workflow_id)
    context["organization_id"] = str(app.organization_id)
    context["db_session_factory"] = lambda: db
    engine = WorkflowEngine(
        _workflow_node_graph(target_app_id=target_app_id),
        user_input={"items": ["first", "second"]},
        execution_context=context,
        execution_id=uuid.uuid4(),
    )

    try:
        result = engine.execute()
    finally:
        engine.cleanup()

    assert "child-workflow" in result
    logger.create_run_log.assert_called_once()
    logger.update_run_log_finish.assert_called_once()
    logger.update_run_log_error.assert_not_called()
    assert logger.create_node_log.call_count == 2
    assert [event[1] for event in published].count("workflow_finish") == 1
    assert [event[1] for event in published].count("error") == 0
    db.close.assert_called_once()


def test_loop_continue_does_not_record_child_failure_on_parent_run(
    monkeypatch,
) -> None:
    logger, published = _capture_lifecycle(monkeypatch)

    def fail_body(_self, _inputs):
        raise RuntimeError("body failed")

    monkeypatch.setattr(TemplateNode, "_run", fail_body)
    run_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    engine = WorkflowEngine(
        _loop_graph(error_strategy="continue"),
        user_input={"items": ["first", "second"]},
        execution_context=_execution_context(
            run_id=run_id,
            workflow_id=workflow_id,
        ),
        execution_id=uuid.uuid4(),
    )

    try:
        result = engine.execute()
    finally:
        engine.cleanup()

    assert result["loop"]["results"] == [
        {"error": "node_error"},
        {"error": "node_error"},
    ]
    logger.update_run_log_error.assert_not_called()
    logger.update_run_log_finish.assert_called_once()
    assert [event[1] for event in published].count("error") == 0
    assert [event[1] for event in published].count("workflow_finish") == 1


def test_loop_child_timeout_is_recorded_by_root_once(monkeypatch) -> None:
    logger, published = _capture_lifecycle(monkeypatch)

    def timeout_body(_self, _inputs):
        raise TimeoutError("body timeout")

    monkeypatch.setattr(TemplateNode, "_run", timeout_body)
    run_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    engine = WorkflowEngine(
        _loop_graph(error_strategy="end"),
        user_input={"items": ["first", "second"]},
        execution_context=_execution_context(
            run_id=run_id,
            workflow_id=workflow_id,
        ),
        execution_id=uuid.uuid4(),
    )

    try:
        with pytest.raises(ValueError, match="node_error"):
            engine.execute()
    finally:
        engine.cleanup()

    logger.update_run_log_finish.assert_not_called()
    logger.update_run_log_error.assert_called_once_with("node_error")
    assert [event[1] for event in published].count("workflow_finish") == 0
    assert [event[1] for event in published].count("error") == 1
