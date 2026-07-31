import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.gateway.api.v1.endpoints import workflow as workflow_endpoint


class _NoBudgetDb:
    def query(self, model, *rest):
        return SimpleNamespace(
            filter=lambda *args, **kwargs: SimpleNamespace(first=lambda: None)
        )


class _Celery:
    def __init__(self):
        self.calls = []
        self.backend = SimpleNamespace(TimeoutError=TimeoutError)

    def send_task(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("configuration-blocked workflow must not publish")


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
        }
    )


def _workflow():
    return SimpleNamespace(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        graph={
            "nodes": [{"id": "start-1", "type": "startNode", "data": {}}],
            "edges": [],
        },
    )


def test_test_routing_policy_context_uses_deployment_policy_only_for_matching_nodes(
    monkeypatch,
):
    workflow = _workflow()
    workflow.graph = {
        "nodes": [
            {
                "id": "llm-matching",
                "type": "llmNode",
                "data": {
                    "auto_model_routing": True,
                    "model_id": "gpt-4.1",
                    "user_prompt": "{{message}}",
                },
            },
            {
                "id": "llm-changed",
                "type": "llmNode",
                "data": {
                    "auto_model_routing": True,
                    "model_id": "gpt-4.1-mini",
                    "user_prompt": "changed {{message}}",
                },
            },
        ],
        "edges": [],
    }
    deployment = SimpleNamespace(
        id=uuid.uuid4(),
        graph_snapshot={
            "nodes": [
                workflow.graph["nodes"][0],
                {
                    **workflow.graph["nodes"][1],
                    "data": {
                        **workflow.graph["nodes"][1]["data"],
                        "user_prompt": "deployed {{message}}",
                    },
                },
            ],
            "edges": [],
        },
    )
    monkeypatch.setattr(
        workflow_endpoint,
        "_active_deployments_for_workflow",
        lambda *_args, **_kwargs: [deployment],
    )

    context = workflow_endpoint._test_routing_policy_context(
        object(), workflow=workflow, graph=workflow.graph
    )

    assert context == {
        "routing_policy_preview_node_ids": ["llm-matching", "llm-changed"],
        "routing_policy_deployment_node_ids": ["llm-matching"],
        "routing_policy_deployment_ids_by_node": {
            "llm-matching": str(deployment.id),
        },
        "routing_policy_ambiguous_node_ids": [],
        "routing_policy_preview": True,
        "routing_policy_execute_judge": True,
    }


def test_test_routing_policy_context_enables_judge_without_active_deployment(
    monkeypatch,
):
    workflow = _workflow()
    workflow.graph = {
        "nodes": [
            {
                "id": "llm-router",
                "type": "llmNode",
                "data": {
                    "auto_model_routing": True,
                    "model_id": "gpt-4.1",
                },
            },
            {
                "id": "llm-manual",
                "type": "llmNode",
                "data": {
                    "auto_model_routing": False,
                    "model_id": "gpt-4.1",
                },
            },
        ],
        "edges": [],
    }
    monkeypatch.setattr(
        workflow_endpoint,
        "_active_deployments_for_workflow",
        lambda *_args, **_kwargs: [],
    )

    context = workflow_endpoint._test_routing_policy_context(
        object(), workflow=workflow, graph=workflow.graph
    )

    assert context == {
        "routing_policy_preview_node_ids": ["llm-router"],
        "routing_policy_deployment_node_ids": [],
        "routing_policy_deployment_ids_by_node": {},
        "routing_policy_ambiguous_node_ids": [],
        "routing_policy_preview": True,
        "routing_policy_execute_judge": True,
    }


def _blocked(*args, **kwargs):
    raise HTTPException(
        status_code=409,
        detail={
            "error": {
                "code": "workflow.configuration_preflight.blocked",
                "message": "Workflow configuration preflight blocked execution",
            }
        },
    )


def _configure_block(monkeypatch, workflow, celery):
    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "get_draft",
        lambda *args, **kwargs: workflow.graph,
    )
    monkeypatch.setattr(
        workflow_endpoint.DeploymentService,
        "enforce_authenticated_configuration_preflight",
        _blocked,
    )
    monkeypatch.setattr(workflow_endpoint, "celery_app", celery)


def test_authenticated_preflight_uses_workflow_id_when_app_id_is_none(monkeypatch):
    workflow = _workflow()
    workflow.app_id = None
    principal_id = uuid.uuid4()
    bound_graph = {"nodes": [], "edges": [], "bound": True}
    captured = {}

    def bind(_db, graph, *, app):
        captured["bind"] = (graph, app.id, app.organization_id)
        return bound_graph

    def preflight(_db, **kwargs):
        captured["preflight"] = kwargs

    monkeypatch.setattr(
        workflow_endpoint.DeploymentService,
        "bind_workflow_node_targets",
        bind,
    )
    monkeypatch.setattr(
        workflow_endpoint.DeploymentService,
        "enforce_authenticated_configuration_preflight",
        preflight,
    )

    result = workflow_endpoint._bind_and_preflight_authenticated_graph(
        object(),
        workflow=workflow,
        graph=workflow.graph,
        principal_id=principal_id,
    )

    assert result is bound_graph
    assert captured["bind"] == (
        workflow.graph,
        workflow.id,
        workflow.organization_id,
    )
    assert captured["preflight"] == {
        "graph_snapshot": bound_graph,
        "organization_id": workflow.organization_id,
        "principal_id": principal_id,
    }


def test_invalid_graph_is_blocked_before_workflow_node_binding(monkeypatch):
    workflow = _workflow()
    invalid_graph = {
        "nodes": [
            {
                "id": "workflow-1",
                "type": "workflowNode",
                "data": {"appId": str(uuid.uuid4())},
            }
        ],
        "edges": [],
    }

    def fail_binding(*args, **kwargs):
        raise AssertionError("invalid graph must not query WorkflowNode targets")

    monkeypatch.setattr(
        workflow_endpoint.DeploymentService,
        "bind_workflow_node_targets",
        fail_binding,
    )
    monkeypatch.setattr(
        workflow_endpoint.DeploymentService,
        "enforce_authenticated_configuration_preflight",
        _blocked,
    )

    with pytest.raises(HTTPException) as exc_info:
        workflow_endpoint._bind_and_preflight_authenticated_graph(
            object(),
            workflow=workflow,
            graph=invalid_graph,
            principal_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 409


def test_transitive_binding_error_uses_common_preflight_envelope(monkeypatch):
    workflow = _workflow()
    principal_id = uuid.uuid4()
    binding_error = HTTPException(
        status_code=422,
        detail={"code": "workflow_graph_invalid"},
    )
    captured = {}

    def fail_binding(*args, **kwargs):
        raise binding_error

    def block_preflight(_db, **kwargs):
        captured.update(kwargs)
        _blocked()

    monkeypatch.setattr(
        workflow_endpoint.DeploymentService,
        "bind_workflow_node_targets",
        fail_binding,
    )
    monkeypatch.setattr(
        workflow_endpoint.DeploymentService,
        "enforce_authenticated_configuration_preflight",
        block_preflight,
    )

    with pytest.raises(HTTPException) as exc_info:
        workflow_endpoint._bind_and_preflight_authenticated_graph(
            object(),
            workflow=workflow,
            graph=workflow.graph,
            principal_id=principal_id,
        )

    assert exc_info.value.status_code == 409
    assert captured == {
        "graph_snapshot": workflow.graph,
        "organization_id": workflow.organization_id,
        "principal_id": principal_id,
    }


def test_execute_configuration_block_does_not_publish(monkeypatch):
    workflow = _workflow()
    current_user = SimpleNamespace(id=uuid.uuid4())
    celery = _Celery()
    _configure_block(monkeypatch, workflow, celery)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            workflow_endpoint.execute_workflow(
                str(workflow.id),
                _request(f"/api/v1/workflows/{workflow.id}/execute"),
                user_input={},
                db=_NoBudgetDb(),
                current_user=current_user,
            )
        )

    assert exc_info.value.status_code == 409
    assert celery.calls == []


def test_stream_malformed_graph_uses_common_preflight_before_sse_publish(monkeypatch):
    workflow = _workflow()
    workflow.graph = {"nodes": "invalid", "edges": []}
    current_user = SimpleNamespace(id=uuid.uuid4())
    celery = _Celery()
    _configure_block(monkeypatch, workflow, celery)

    def fail_legacy_validation(*args, **kwargs):
        raise AssertionError("stream must use the common preflight projection")

    monkeypatch.setattr(
        workflow_endpoint,
        "validate_execution_graph",
        fail_legacy_validation,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            workflow_endpoint.stream_workflow(
                str(workflow.id),
                _request(f"/api/v1/workflows/{workflow.id}/stream"),
                db=_NoBudgetDb(),
                current_user=current_user,
            )
        )

    assert exc_info.value.status_code == 409
    assert celery.calls == []


def test_compare_configuration_block_does_not_start_variants(monkeypatch):
    workflow = _workflow()
    current_user = SimpleNamespace(id=uuid.uuid4())
    celery = _Celery()
    _configure_block(monkeypatch, workflow, celery)

    with pytest.raises(HTTPException) as exc_info:
        workflow_endpoint.compare_workflow_variants(
            str(workflow.id),
            workflow_endpoint.WorkflowCompareRequest(
                node_id="llm-1",
                compare_type="model",
                left="model-a",
                right="model-b",
            ),
            _request(f"/api/v1/workflows/{workflow.id}/compare"),
            db=_NoBudgetDb(),
            current_user=current_user,
        )

    assert exc_info.value.status_code == 409
    assert celery.calls == []


def test_cost_optimizer_configuration_block_does_not_create_candidate(monkeypatch):
    workflow = _workflow()
    workflow.graph = {
        "nodes": [
            {"id": "start-1", "type": "startNode", "data": {}},
            {"id": "llm-1", "type": "llmNode", "data": {}},
        ],
        "edges": [
            {"id": "start-llm", "source": "start-1", "target": "llm-1"}
        ],
    }
    current_user = SimpleNamespace(id=uuid.uuid4())
    celery = _Celery()
    _configure_block(monkeypatch, workflow, celery)

    with pytest.raises(HTTPException) as exc_info:
        workflow_endpoint.compare_cost_optimizer_candidate(
            str(workflow.id),
            "llm-1",
            workflow_endpoint.CostOptimizerCompareRequest(
                baseline_id=str(uuid.uuid4()),
                candidate=workflow_endpoint.CostOptimizerCandidateRequest(
                    model_id="model-b"
                ),
            ),
            _request(
                f"/api/v1/workflows/{workflow.id}/llm-nodes/llm-1/"
                "cost-optimizer/compare"
            ),
            db=_NoBudgetDb(),
            current_user=current_user,
        )

    assert exc_info.value.status_code == 409
    assert celery.calls == []


def test_recommendation_verification_block_happens_before_claim(monkeypatch):
    workflow = _workflow()
    workflow.graph = {
        "nodes": [
            {"id": "start-1", "type": "startNode", "data": {}},
            {"id": "llm-1", "type": "llmNode", "data": {}},
        ],
        "edges": [
            {"id": "start-llm", "source": "start-1", "target": "llm-1"}
        ],
    }
    current_user = SimpleNamespace(id=uuid.uuid4())
    celery = _Celery()
    _configure_block(monkeypatch, workflow, celery)

    def fail_verify(**kwargs):
        raise AssertionError("blocked verification must not create a claim")

    monkeypatch.setattr(
        workflow_endpoint,
        "_verify_cost_optimizer_recommendations",
        fail_verify,
    )

    with pytest.raises(HTTPException) as exc_info:
        workflow_endpoint.verify_cost_optimizer_recommendations(
            str(workflow.id),
            "llm-1",
            workflow_endpoint.CostOptimizerRecommendationVerifyRequest(
                recommendation_ids=["max_tokens"],
                baseline_mode="latest_success",
            ),
            _request(
                f"/api/v1/workflows/{workflow.id}/llm-nodes/llm-1/"
                "cost-optimizer/recommendations/verify"
            ),
            idempotency_key="preflight-blocked",
            db=_NoBudgetDb(),
            current_user=current_user,
        )

    assert exc_info.value.status_code == 409
    assert celery.calls == []
