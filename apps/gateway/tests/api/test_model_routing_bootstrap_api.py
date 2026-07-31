from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.gateway.api.v1.endpoints import workflow as workflow_endpoint


def _workflow():
    return SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        graph={
            "nodes": [
                {
                    "id": "llm-answer",
                    "type": "llmNode",
                    "data": {
                        "title": "답변",
                        "model_id": "gpt-4.1-mini",
                        "auto_model_routing": False,
                    },
                }
            ],
            "edges": [],
        },
    )


def _request():
    return workflow_endpoint.ModelRoutingBootstrapRequest(
        task_description="회사 정책 근거를 비교해 안전한 답변을 작성합니다.",
        default_model_id="gpt-4.1-mini",
        fallback_model_id="gpt-4.1",
        expected_graph_hash="a" * 64,
        expected_updated_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )


def test_model_routing_bootstrap_request_requires_graph_cas_expectation():
    with pytest.raises(ValidationError):
        workflow_endpoint.ModelRoutingBootstrapRequest(
            task_description="회사 정책 근거를 비교해 안전한 답변을 작성합니다.",
            default_model_id="gpt-4.1-mini",
        )


def test_model_routing_bootstrap_writes_artifact_and_graph_through_cas():
    workflow = _workflow()
    current_user = SimpleNamespace(id=uuid4())
    bootstrap = SimpleNamespace(id=uuid4(), task_fingerprint="fingerprint-v1")
    candidate_models = [
        SimpleNamespace(model_id="gpt-4.1-mini"),
        SimpleNamespace(model_id="gpt-4.1"),
    ]
    graph_metadata = {
        "graph_hash": "b" * 64,
        "updated_at": "2026-07-18T00:00:01+00:00",
    }

    with (
        patch.object(
            workflow_endpoint,
            "ensure_workflow_permission",
            return_value=workflow,
        ),
        patch.object(
            workflow_endpoint,
            "_lock_workflow_for_cas_graph_write",
            return_value=workflow,
        ) as lock_workflow,
        patch.object(
            workflow_endpoint,
            "_model_routing_candidates_for_user",
            return_value=candidate_models,
        ),
        patch.object(
            workflow_endpoint.PersistedModelRoutingBootstrapStore,
            "create_ready",
            return_value=bootstrap,
        ) as create_ready,
        patch.object(
            workflow_endpoint.PersistedModelRoutingBootstrapStore,
            "public_summary",
            return_value={"id": str(bootstrap.id), "status": "ready"},
        ),
        patch.object(
            workflow_endpoint,
            "_commit_graph_write_with_canonical_metadata",
            return_value=graph_metadata,
        ) as commit_graph,
    ):
        result = workflow_endpoint.create_model_routing_bootstrap_endpoint(
            str(workflow.id),
            "llm-answer",
            _request(),
            db=MagicMock(),
            current_user=current_user,
        )

    lock_workflow.assert_called_once()
    create_ready.assert_called_once()
    commit_graph.assert_called_once()
    node_data = workflow.graph["nodes"][0]["data"]
    assert node_data["auto_model_routing"] is True
    assert node_data["model_routing_bootstrap_id"] == str(bootstrap.id)
    assert node_data["model_routing_task_description"].startswith("회사 정책")
    assert result == {"id": str(bootstrap.id), "status": "ready", **graph_metadata}


def test_model_routing_bootstrap_stale_graph_stops_before_artifact_creation():
    workflow = _workflow()
    conflict = HTTPException(status_code=409, detail="workflow.graph_conflict")

    with (
        patch.object(
            workflow_endpoint,
            "ensure_workflow_permission",
            return_value=workflow,
        ),
        patch.object(
            workflow_endpoint,
            "_lock_workflow_for_cas_graph_write",
            side_effect=conflict,
        ),
        patch.object(
            workflow_endpoint.PersistedModelRoutingBootstrapStore,
            "create_ready",
        ) as create_ready,
        pytest.raises(HTTPException) as exc_info,
    ):
        workflow_endpoint.create_model_routing_bootstrap_endpoint(
            str(workflow.id),
            "llm-answer",
            _request(),
            db=MagicMock(),
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "workflow.graph_conflict"
    create_ready.assert_not_called()
