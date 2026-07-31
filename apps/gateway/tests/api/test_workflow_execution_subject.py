import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.gateway.api.v1.endpoints import workflow as workflow_endpoint


class FakeTask:
    def get(self, timeout=None):
        return {"status": "success", "result": {}}


class FakeCeleryApp:
    def __init__(self):
        self.calls = []

    def send_task(self, name, args=None, kwargs=None, **options):
        self.calls.append(
            {
                "name": name,
                "args": args or [],
                "kwargs": kwargs or {},
                "options": options,
            }
        )
        return FakeTask()


class FakeNoBudgetDb:
    """예산 미설정 세션 — 실행 전 예산 확인이 조용히 통과한다."""

    def query(self, model, *rest):
        return SimpleNamespace(
            filter=lambda *args, **kwargs: SimpleNamespace(first=lambda: None)
        )


def _valid_start_graph():
    return {
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            }
        ],
        "edges": [],
    }


def test_authenticated_execute_passes_current_user_execution_subject(monkeypatch):
    workflow_id = str(uuid.uuid4())
    app_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4())
    workflow = SimpleNamespace(
        id=workflow_id,
        app_id=app_id,
        organization_id=organization_id,
        graph=_valid_start_graph(),
    )
    celery = FakeCeleryApp()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/workflows/{workflow_id}/execute",
            "headers": [],
        }
    )

    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "get_draft",
        lambda *args, **kwargs: _valid_start_graph(),
    )
    monkeypatch.setattr(workflow_endpoint, "celery_app", celery)

    asyncio.run(
        workflow_endpoint.execute_workflow(
            workflow_id,
            request,
            user_input={},
            db=FakeNoBudgetDb(),
            current_user=current_user,
        )
    )

    assert len(celery.calls) == 1
    execution_context = celery.calls[0]["args"][2]
    assert execution_context["user_id"] == str(current_user.id)
    assert execution_context["execution_subject"] == {
        "type": "user",
        "id": str(current_user.id),
    }


def test_execute_preflight_error_is_preserved_without_celery_backend(
    monkeypatch,
):
    workflow_id = str(uuid.uuid4())
    workflow = SimpleNamespace(
        id=workflow_id,
        app_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        graph=_valid_start_graph(),
    )
    current_user = SimpleNamespace(id=uuid.uuid4())
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/workflows/{workflow_id}/execute",
            "headers": [],
        }
    )

    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "get_draft",
        lambda *args, **kwargs: {
            "nodes": [{"id": "start-1", "type": "startNode", "data": {}}],
            "edges": [],
        },
    )
    monkeypatch.setattr(workflow_endpoint, "celery_app", FakeCeleryApp())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            workflow_endpoint.execute_workflow(
                workflow_id,
                request,
                user_input={},
                db=FakeNoBudgetDb(),
                current_user=current_user,
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "workflow.configuration_preflight.blocked"


def test_authenticated_execute_dispatches_draft_rag_selection(monkeypatch):
    workflow_id = str(uuid.uuid4())
    app_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    knowledge_base_id = str(uuid.uuid4())
    current_user = SimpleNamespace(id=uuid.uuid4())
    workflow = SimpleNamespace(
        id=workflow_id,
        app_id=app_id,
        organization_id=organization_id,
        graph=_valid_start_graph(),
    )
    celery = FakeCeleryApp()
    draft_graph = {
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
            {
                "id": "llm-1",
                "type": "llmNode",
                "position": {"x": 100, "y": 120},
                "data": {
                    "title": "LLM",
                    "provider": "openai",
                    "model_id": "gpt-4o",
                    "user_prompt": "query",
                    "knowledgeBases": [
                        {"id": knowledge_base_id, "name": "제품 정책"}
                    ],
                    "topK": 4,
                    "scoreThreshold": 0.6,
                },
            }
        ],
        "edges": [
            {"id": "start-llm", "source": "start-1", "target": "llm-1"}
        ],
    }
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/workflows/{workflow_id}/execute",
            "headers": [],
        }
    )

    monkeypatch.setattr(
        workflow_endpoint,
        "ensure_workflow_permission",
        lambda *args, **kwargs: workflow,
    )
    monkeypatch.setattr(
        workflow_endpoint.WorkflowService,
        "get_draft",
        lambda *args, **kwargs: draft_graph,
    )
    monkeypatch.setattr(
        workflow_endpoint.DeploymentService,
        "enforce_authenticated_configuration_preflight",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(workflow_endpoint, "celery_app", celery)

    asyncio.run(
        workflow_endpoint.execute_workflow(
            workflow_id,
            request,
            user_input={"message": "hello"},
            db=FakeNoBudgetDb(),
            current_user=current_user,
        )
    )

    dispatched_graph = celery.calls[0]["args"][0]
    dispatched_context = celery.calls[0]["args"][2]

    dispatched_llm = next(
        node for node in dispatched_graph["nodes"] if node["id"] == "llm-1"
    )
    assert dispatched_llm["data"]["knowledgeBases"] == [
        {"id": knowledge_base_id, "name": "제품 정책"}
    ]
    assert dispatched_llm["data"]["topK"] == 4
    assert dispatched_llm["data"]["scoreThreshold"] == 0.6
    assert dispatched_context["execution_subject"] == {
        "type": "user",
        "id": str(current_user.id),
    }
