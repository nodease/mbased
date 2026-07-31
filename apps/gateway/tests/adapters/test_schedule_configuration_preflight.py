from uuid import uuid4

from apps.gateway.adapters.schedule.configuration_preflight import (
    ScheduleConfigurationPreflightAdapter,
)
from apps.gateway.application.deployment.preflight import DeploymentPreflightUseCase


def _workflow_node_graph(data):
    return {
        "nodes": [
            {
                "id": "workflow-call",
                "type": "workflowNode",
                "position": {"x": 0, "y": 0},
                "data": data,
            }
        ],
        "edges": [],
    }


def test_schedule_preflight_blocks_unresolved_local_execution_before_deployment_lookup(
    monkeypatch,
):
    deployment_calls = []
    monkeypatch.setattr(
        DeploymentPreflightUseCase,
        "enforce_schedule_dispatch",
        lambda *_args, **_kwargs: deployment_calls.append(_kwargs),
    )
    adapter = ScheduleConfigurationPreflightAdapter(
        object(),
        node_catalog_by_type={},
    )

    ready = adapter.is_ready(
        graph_snapshot=_workflow_node_graph(
            {"workflowId": str(uuid4()), "configuration_state": "resolved"}
        ),
        organization_id=uuid4(),
    )

    assert ready is False
    assert deployment_calls == []


def test_schedule_preflight_keeps_deployment_policy_for_resolved_local_execution(
    monkeypatch,
):
    graph = _workflow_node_graph(
        {"workflowId": "", "appId": str(uuid4()), "deployment_id": str(uuid4())}
    )
    deployment_calls = []
    monkeypatch.setattr(
        DeploymentPreflightUseCase,
        "enforce_schedule_dispatch",
        lambda *_args, **kwargs: deployment_calls.append(kwargs),
    )
    adapter = ScheduleConfigurationPreflightAdapter(
        object(),
        node_catalog_by_type={},
    )

    ready = adapter.is_ready(
        graph_snapshot=graph,
        organization_id=uuid4(),
    )

    assert ready is True
    assert deployment_calls == [{"graph_snapshot": graph}]
