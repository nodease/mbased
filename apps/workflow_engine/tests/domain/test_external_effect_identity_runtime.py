import uuid
from unittest.mock import Mock

import pytest

from apps.shared.domain.workflow_execution_identity import InvocationSegment
from apps.shared.domain.workflow_node_binding import (
    WorkflowNodeBinding,
    apply_workflow_node_bindings,
)
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine
from apps.workflow_engine.domain.external_effect import (
    ExternalEffectError,
    ExternalEffectRetrySignal,
)


def _graph():
    return {
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {"title": "Start"},
            },
            {
                "id": "http-1",
                "type": "httpRequestNode",
                "position": {"x": 100, "y": 0},
                "data": {"title": "HTTP", "method": "POST", "url": "https://example.test"},
            },
        ],
        "edges": [{"id": "e1", "source": "start-1", "target": "http-1"}],
    }


def _context(execution_id: uuid.UUID | None = None):
    return {
        "organization_id": str(uuid.uuid4()),
        "app_id": str(uuid.uuid4()),
        "workflow_id": str(uuid.uuid4()),
        **({"execution_id": str(execution_id)} if execution_id else {}),
    }


def test_runtime_identity_is_stable_without_entering_node_visible_context() -> None:
    execution_id = uuid.uuid4()
    context = _context(execution_id)
    first = WorkflowEngine(_graph(), execution_context=context)
    duplicate = WorkflowEngine(_graph(), execution_context=context)

    first_control = first._node_execution_control("http-1", ordinal=0, node_run_id=None)
    duplicate_control = duplicate._node_execution_control(
        "http-1", ordinal=0, node_run_id=None
    )

    assert first_control.external_effect_context is not None
    assert (
        first_control.external_effect_context.node_invocation_id
        == duplicate_control.external_effect_context.node_invocation_id
    )
    assert "execution_id" not in first.execution_context
    assert "execution_id" not in first.user_input


def test_loop_iteration_path_changes_node_invocation_identity() -> None:
    execution_id = uuid.uuid4()
    context = _context(execution_id)
    root = (InvocationSegment("root", "", context["workflow_id"]),)
    first = WorkflowEngine(
        _graph(),
        execution_context=context,
        execution_id=execution_id,
        invocation_path_prefix=root + (InvocationSegment("loop", "loop-1", "0"),),
    )
    second = WorkflowEngine(
        _graph(),
        execution_context=context,
        execution_id=execution_id,
        invocation_path_prefix=root + (InvocationSegment("loop", "loop-1", "1"),),
    )

    assert (
        first._node_execution_control("http-1", ordinal=0, node_run_id=None)
        .external_effect_context.node_invocation_id
        != second._node_execution_control("http-1", ordinal=0, node_run_id=None)
        .external_effect_context.node_invocation_id
    )


def test_missing_publisher_identity_cannot_reach_effect_boundary() -> None:
    engine = WorkflowEngine(_graph(), execution_context=_context())

    control = engine._node_execution_control("http-1", ordinal=0, node_run_id=None)

    assert control.external_effect_context is None
    assert control.external_effect_enforced is False


@pytest.mark.parametrize("stream", [False, True])
def test_retry_signal_reaches_task_boundary_without_error_event(stream: bool) -> None:
    engine = WorkflowEngine(_graph(), execution_context=_context(uuid.uuid4()))
    engine.logger = Mock()
    engine.logger.create_run_log.return_value = None

    def request_retry(*_args, **_kwargs):
        raise ExternalEffectRetrySignal("external_effect.retry_allowed")

    engine.node_instances["http-1"].execute = request_retry

    with pytest.raises(ExternalEffectRetrySignal):
        if stream:
            list(engine.execute_stream())
        else:
            engine.execute()

    engine.logger.update_run_log_error.assert_called_with(
        "external_effect.retry_allowed"
    )


def test_external_effect_stream_error_uses_flat_public_payload(monkeypatch) -> None:
    run_id = uuid.uuid4()
    context = _context(uuid.uuid4())
    context["workflow_run_id"] = str(run_id)
    engine = WorkflowEngine(_graph(), execution_context=context)
    engine.logger = Mock()
    published = []

    def fail_permanently(*_args, **_kwargs):
        raise ExternalEffectError(
            "external_effect.outcome_unknown",
            retryable=False,
            node_id="http-1",
        )

    engine.node_instances["http-1"].execute = fail_permanently
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.core.workflow_engine.publish_workflow_event",
        lambda *args: published.append(args),
    )

    events = list(engine.execute_stream())

    expected = {
        "code": "external_effect.outcome_unknown",
        "message": "external_effect.outcome_unknown",
        "retryable": False,
        "node_id": "http-1",
    }
    assert events[-1]["data"] == {**expected, "non_retryable": True}
    assert published[-1] == (str(run_id), "error", expected)
    assert "error" not in events[-1]["data"]


def test_server_owned_workflow_node_binding_stays_out_of_execution_context() -> None:
    target_app_id = uuid.uuid4()
    binding = WorkflowNodeBinding(
        container_path=(),
        workflow_node_id="child-1",
        target_app_id=target_app_id,
        deployment_id=uuid.uuid4(),
        deployment_version=4,
        snapshot_sha256="a" * 64,
    )
    graph = apply_workflow_node_bindings(
            {
                "nodes": [
                    {
                        "id": "start-1",
                        "type": "startNode",
                        "position": {"x": 0, "y": 0},
                        "data": {"title": "Start"},
                    },
                    {
                        "id": "child-1",
                        "type": "workflowNode",
                        "position": {"x": 100, "y": 0},
                    "data": {
                        "title": "Child",
                        "appId": str(target_app_id),
                        "workflowId": str(uuid.uuid4()),
                    },
                }
                ],
                "edges": [
                    {"id": "e1", "source": "start-1", "target": "child-1"}
                ],
        },
        (binding,),
    )
    engine = WorkflowEngine(graph, execution_context=_context(uuid.uuid4()))

    control = engine._node_execution_control("child-1", ordinal=0, node_run_id=None)

    assert control.workflow_node_binding == binding
    assert "_nodease_runtime" not in engine.execution_context
    assert "_nodease_runtime" not in engine.user_input


def test_loop_child_resolves_binding_by_container_path() -> None:
    binding = WorkflowNodeBinding(
        container_path=(("loop", "loop-1"),),
        workflow_node_id="child-1",
        target_app_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=1,
        snapshot_sha256="b" * 64,
    )
    engine = WorkflowEngine(
        _graph(),
        execution_context=_context(uuid.uuid4()),
        workflow_node_bindings=(binding,),
        binding_container_path=(("loop", "loop-1"),),
    )

    control = engine._node_execution_control("child-1", ordinal=0, node_run_id=None)

    assert control.workflow_node_binding == binding
